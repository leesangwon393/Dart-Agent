"""Entity Extraction (§35).

질문에서 검색에 필요한 entity(회사/기간/지표/공시명/정정 명시 여부)를 추출한다.
기업명은 universe.csv 의 corp_name + listed_name(통용명, 예: 현대차→현대자동차)
를 alias map 으로 써서 매칭하고, 전부 NFC 로 정규화한다 (§35 "기업명 metadata 는
NFC normalize").
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from disclosure_rag.common.manifest_loader import load_universe
from disclosure_rag.common.unicode_utils import normalize_nfc

_YEAR = r"20\d{2}\s*년?"
_RECENT_N_YEAR = r"최근\s*\d+\s*년"
_QUARTER = r"[1-4]\s*분기"
_HALF = r"상반기|하반기"
_YM = r"20\d{2}[.\-]\s*\d{1,2}\s*월?"
_PERIOD_PAT = re.compile("|".join([_RECENT_N_YEAR, _YM, _YEAR, _QUARTER, _HALF]))

# period_type 분류용 sub-pattern (2026-08-26 확장). 우선순위는 "구체성(narrowness)"
# 기준: 특정 월을 못박는 year_month 가 가장 구체적이고, recent_n_year("최근 3년"
# 처럼 여러 해를 아우르는 범위)가 가장 넓다(=가장 덜 구체적). 한 질의에서 여러
# period 패턴이 동시에 매칭되면(예: "2025년 1분기" -> annual + quarter 둘 다
# 매칭) 이 우선순위표에서 앞에 있는(더 구체적인) 타입 하나를 period_type 으로
# 채택한다 — "리스트로 다 담기"도 합리적인 대안이지만, period_type 을 Agent가
# "이 질의가 어떤 세분화 단위로 조회해야 하는지" 판단하는 단일 힌트로 쓰길
# 원하므로(예: agent가 분기 데이터를 찾아야 하는지 연간 데이터로 충분한지),
# 단일 값이 더 실용적이라고 판단했다. 원본 period 리스트는 그대로 유지되므로
# 여러 매칭이 있었다는 사실 자체는 손실되지 않는다.
_PERIOD_TYPE_PRIORITY: list[tuple[str, re.Pattern[str]]] = [
    ("year_month", re.compile(_YM)),
    ("quarter", re.compile(_QUARTER)),
    ("half", re.compile(_HALF)),
    ("annual", re.compile(_YEAR)),
    ("recent_n_year", re.compile(_RECENT_N_YEAR)),
]

# period_comparison: "두 기간을 비교하려는 신호". 100문항 배치 실패 사례("당기
# 대비 전기 영업이익 변화를 정리해줘" — period=[] 로 빈 채 넘어감, PROJECT_STATE
# §9-0/§10)와 router/routes.py 의 multi_compare/calculation utterance("전년
# 대비", "작년과 올해", "[YEAR_1]년 대비 [YEAR_2]년" 등)에서 실제 쓰이는 표현을
# 모아 정규식으로 구성했다.
_PERIOD_COMPARISON_PAT = re.compile(
    "|".join([
        r"당기\s*대비\s*전기",
        r"전기\s*대비",
        r"전년\s*동기",
        r"전년\s*대비",
        r"작년\s*보다",
        r"작년\s*대비",
        r"작년(?:과|와)\s*올해",
        r"올해(?:와|과)\s*작년",
        r"직전\s*(?:분기|반기|년도?|기)\s*대비",
        r"전기말\s*대비",
        r"전년말\s*대비",
        r"20\d{2}\s*년?\s*대비\s*20\d{2}\s*년?",
    ])
)

_CORRECTION_KEYWORDS = ("기재정정", "정정공시", "정정")

_REPORT_NAME_TERMS = [
    "사업보고서", "반기보고서", "분기보고서",
    "주요사항보고서", "주식등의대량보유상황보고서", "대량보유상황보고서",
    "감사보고서", "연결감사보고서",
]


def _classify_period_type(matched: str) -> str | None:
    """하나의 _PERIOD_PAT 매칭 문자열이 어떤 sub-pattern 인지 fullmatch 로 분류."""
    for name, pat in _PERIOD_TYPE_PRIORITY:
        if pat.fullmatch(matched):
            return name
    return None


# [YEAR] placeholder 치환 대상 — annual/year_month 만. 2026-08-25 발견된 버그:
# routes.py 의 utterance 3개가 "[COMPANY]의 [YEAR] 매출액은..."처럼 [YEAR]를
# 학습하는데, normalize_query() 는 company 만 placeholder 로 치환하고 연도는
# 절대 치환한 적이 없어서 router 가 그 토큰을 실제 추론에서 한 번도 못 봤다
# (company 정규화와 똑같은 이유로 만들어둔 장치가 절반만 구현돼 있었음).
# quarter/half/recent_n_year("1분기"/"상반기"/"최근 3년")는 4자리 연도 숫자를
# 담지 않아 과적합 위험이 적고, routes.py 도 "[YEAR] 반기보고서"처럼 이
# 단어들은 리터럴로 그대로 두므로 치환 대상에서 제외한다.
_YEAR_BEARING_TYPES = {"annual", "year_month"}
_LEADING_YEAR_NUM = re.compile(r"20\d{2}")


class ExtractedEntities(BaseModel):
    raw_query: str
    companies: list[str] = Field(default_factory=list)
    company_count: int = 0
    period: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    report_name: str | None = None
    explicit_correction: bool = False
    # --- 2026-08-26 확장 (event_analysis/ownership_analysis/기간비교형
    # calculation route 전용 신호, 전부 하위호환을 위해 기본값 있음) ---
    period_type: str | None = None
    period_comparison: bool = False
    event_terms: list[str] = Field(default_factory=list)
    ownership_terms: list[str] = Field(default_factory=list)
    comparison_axis: str | None = None
    # (start, end) 는 normalize_query 가 재사용할 수 있도록 company/period 매칭
    # 위치도 보존. period_spans 의 3번째 값은 실제 4자리 연도 문자열("2025")
    # 이며(같은 연도 재언급 시 같은 [YEAR_N] 번호를 재사용하기 위한 dedup 키),
    # company_spans 와 동일한 패턴이다.
    company_spans: list[tuple[int, int, str]] = Field(default_factory=list, exclude=True)
    period_spans: list[tuple[int, int, str]] = Field(default_factory=list, exclude=True)


def _load_metric_terms(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.is_file():
        return []
    return [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


class EntityExtractor:
    def __init__(
        self,
        *,
        corpus_root: str | Path,
        metric_terms_path: str | Path | None = None,
        event_terms_path: str | Path | None = None,
        ownership_terms_path: str | Path | None = None,
    ):
        universe = load_universe(corpus_root)
        alias_map: dict[str, str] = {}
        for _, row in universe.iterrows():
            corp = normalize_nfc(row["corp_name"])
            alias_map[corp] = corp
            listed = normalize_nfc(row.get("listed_name"))
            if listed and listed != corp:
                alias_map[listed] = corp
        self._alias_map = alias_map
        self._sorted_aliases = sorted(alias_map.keys(), key=len, reverse=True)
        self._metric_terms = _load_metric_terms(metric_terms_path) if metric_terms_path else []
        self._event_terms = _load_metric_terms(event_terms_path) if event_terms_path else []
        self._ownership_terms = _load_metric_terms(ownership_terms_path) if ownership_terms_path else []

    def _extract_companies(self, query_nfc: str) -> list[tuple[int, int, str]]:
        spans: list[tuple[int, int, str]] = []
        for alias in self._sorted_aliases:
            start = 0
            while True:
                idx = query_nfc.find(alias, start)
                if idx == -1:
                    break
                end = idx + len(alias)
                if not any(not (end <= s or idx >= e) for s, e, _ in spans):
                    spans.append((idx, end, self._alias_map[alias]))
                start = idx + 1
        return sorted(spans, key=lambda s: s[0])

    def extract(self, query: str) -> ExtractedEntities:
        query_nfc = normalize_nfc(query)

        company_spans = self._extract_companies(query_nfc)
        companies: list[str] = []
        for _s, _e, corp in company_spans:
            if corp not in companies:
                companies.append(corp)

        period_finds = list(_PERIOD_PAT.finditer(query_nfc))
        period_matches = [m.group(0).strip() for m in period_finds]
        periods = period_matches

        period_spans: list[tuple[int, int, str]] = []
        for m in period_finds:
            text = m.group(0).strip()
            if _classify_period_type(text) not in _YEAR_BEARING_TYPES:
                continue
            year_num = _LEADING_YEAR_NUM.match(text)
            key = year_num.group(0) if year_num else text
            period_spans.append((m.start(), m.end(), key))

        period_types = [t for t in (_classify_period_type(p) for p in period_matches) if t is not None]
        period_type = None
        for name, _pat in _PERIOD_TYPE_PRIORITY:
            if name in period_types:
                period_type = name
                break

        period_comparison = bool(_PERIOD_COMPARISON_PAT.search(query_nfc))

        metrics = [term for term in self._metric_terms if term.lower() in query_nfc.lower()]
        event_terms = [term for term in self._event_terms if term.lower() in query_nfc.lower()]
        ownership_terms = [term for term in self._ownership_terms if term.lower() in query_nfc.lower()]

        report_name = next((t for t in _REPORT_NAME_TERMS if t in query_nfc), None)

        explicit_correction = any(kw in query_nfc for kw in _CORRECTION_KEYWORDS)

        # comparison_axis: company_count>=2 와 (period_comparison 또는 period 매칭
        # 2개 이상)가 동시에 참인 "복합 비교"(예: "A기업과 B기업의 2023년과 2025년
        # 매출을 비교해줘")도 실제로 가능하다 — 이 경우 "company"를 우선한다.
        # 근거: 그런 질의라도 Agent 가 결국 해야 할 1차 분기(branching)는 "회사별로
        # 나눠서 조회"이고, 기간 비교는 회사마다 반복되는 2차 축이기 때문이다(회사
        # 축을 놓치면 아예 다른 회사 데이터를 섞어버리는 치명적 오류가 나지만, 기간
        # 축을 놓쳐도 "일단 최근 기간으로 회사별 조회"까지는 절반은 맞는 답이 나옴
        # — 실패 시 피해가 더 큰 축을 우선한다는 원칙).
        has_period_comparison_signal = period_comparison or len(period_matches) >= 2
        if len(companies) >= 2:
            comparison_axis = "company"
        elif has_period_comparison_signal:
            comparison_axis = "period"
        else:
            comparison_axis = None

        return ExtractedEntities(
            raw_query=query,
            companies=companies,
            company_count=len(companies),
            period=periods,
            metrics=metrics,
            report_name=report_name,
            explicit_correction=explicit_correction,
            period_type=period_type,
            period_comparison=period_comparison,
            event_terms=event_terms,
            ownership_terms=ownership_terms,
            comparison_axis=comparison_axis,
            company_spans=company_spans,
            period_spans=period_spans,
        )
