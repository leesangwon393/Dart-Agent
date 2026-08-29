"""SPEC.md §4, §5: Entity / Universe Resolver.

질문에서 explicit company / sector / industry / period / metric / (heuristic)
topic 을 결정하고, "주요 OO기업 N곳" 패턴(§5)을 sector filter → market_cap
내림차순 → top N 규칙으로 해석한다.

전부 deterministic(정규식 + 사전 + Company Master 조회)이다 — LLM 을 쓰지
않는다. §29 "Entity lookup / peer selection → deterministic" 원칙, 그리고
우리 기존 프로젝트의 실측 결론(ablation Stage 8: rule-only entity extraction
승자)을 따른다.
"""

from __future__ import annotations

import re

from app.company.peer_selector import PeerSelector
from app.company.repository import CompanyMasterRepository, CompanyRecord, normalize_nfc
from app.company.resolver import CompanyResolver
from app.query.schemas import CompanyRef, EntityScope, PeerSelectionMethod, ResolvedEntities
from app.query.terms import METRIC_TERMS

_YEAR_PAT = re.compile(r"(20\d{2})\s*년?")

# §5 "주요 OO기업 N곳" 패턴. 캡처그룹1은 sector/industry 힌트 단어(비탐욕적),
# 그룹2는 top N. "주요 방산기업 3곳", "주요 2차전지 업체 5개" 등을 커버한다.
_TOP_N_PAT = re.compile(
    r"주요\s*([가-힣A-Za-z0-9]+?)\s*(?:기업|업체|회사)\s*(\d+)\s*(?:곳|개|사)"
)

# 명시적 회사 없이 "OO 기업들/업체들"처럼 sector 전체를 가리키는 표현.
_SECTOR_PLURAL_HINT_PAT = re.compile(r"(?:기업들|업체들|회사들|기업|업체)")

_TRAILING_TAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p)
    for p in [
        r"에\s*대해\s*(?:알려|설명해)\s*줘\.?\??$",
        r"(?:알려|설명해|분석해|비교해|계산해|찾아|보여)\s*줘\.?\??$",
        r"관련\s*내용\s*알려\s*줘\.?\??$",
        r"공시가?\s*있(?:어|었어|나요|습니까)\.?\??$",
        r"있(?:어|었어|나요|습니까)\.?\??$",
        r"(?:얼마|몇\s*%)(?:야|입니까|인가요)?\.?\??$",
        r"[?？]+$",
    ]
]
_LEADING_PARTICLE_PAT = re.compile(r"^(?:\s*(?:와|과|의|은|는|이|가|을|를|도)\s*)+")
_TRAILING_PARTICLE_PAT = re.compile(r"(?:\s*(?:의|은|는|이|가|을|를|도)\s*)+$")


def _extract_periods(text: str) -> list[int]:
    years: list[int] = []
    for match in _YEAR_PAT.finditer(text):
        year = int(match.group(1))
        if year not in years:
            years.append(year)
    return years


def _extract_metric(text: str) -> str | None:
    for term in METRIC_TERMS:
        if term in text:
            return term
    return None


def _sector_parts(sector: str) -> list[str]:
    return [p.strip() for p in sector.split("·") if len(p.strip()) >= 2]


def _detect_sector(text: str, repository: CompanyMasterRepository) -> str | None:
    """sector 이름을 "·"로 쪼갠 부분 문자열이 질문에 등장하는지 본다.

    Company Master 데이터 자체에서 후보를 뽑기 때문에 별도 sector alias
    사전을 하드코딩할 필요가 없다(§3 "Company Master는 핵심 Entity Registry"
    원칙과 일치) — 다만 sector 이름 자체가 흔한 일반 명사(예: 짧은 2글자
    단어)인 경우 오탐 가능성이 있으므로, 여러 sector가 매칭되면 가장 긴
    부분 문자열이 매칭된 sector를 우선한다.
    """
    best: tuple[int, str] | None = None
    for sector in repository.sector_names():
        for part in _sector_parts(sector):
            if part in text:
                if best is None or len(part) > best[0]:
                    best = (len(part), sector)
                break
    return best[1] if best else None


def _detect_industry(text: str, repository: CompanyMasterRepository) -> str | None:
    for industry in repository.industry_names():
        if len(industry) >= 2 and industry in text:
            return industry
    return None


def _common_sector(companies: list[CompanyRecord]) -> tuple[str | None, int | None]:
    sectors = {c.sector for c in companies if c.sector}
    if len(sectors) == 1:
        sector = next(iter(sectors))
        sector_no = next((c.sector_no for c in companies if c.sector == sector), None)
        return sector, sector_no
    return None, None


def _extract_topic(text: str, resolver: CompanyResolver) -> str | None:
    """아주 단순한 휴리스틱 topic 추출기 (형태소 분석기 없이 정규식만 사용).

    회사명/연도 span 을 제거하고, 흔한 종결 어미(~비교해줘/~알려줘 등)와
    양끝 조사를 걷어낸 나머지를 topic 으로 본다. 형태소 분석이 아니므로
    일반화 한계가 있다 — COMPARISON.md에 이 한계를 명시했다.
    """
    text_nfc = normalize_nfc(text)
    spans = resolver.find_explicit_company_spans(text_nfc)
    year_spans = [(m.start(), m.end()) for m in _YEAR_PAT.finditer(text_nfc)]
    remove_spans = sorted([(s, e) for s, e, _ in spans] + year_spans)

    pieces = []
    cursor = 0
    for start, end in remove_spans:
        if start < cursor:
            continue
        pieces.append(text_nfc[cursor:start])
        cursor = end
    pieces.append(text_nfc[cursor:])
    remainder = "".join(pieces)

    for pattern in _TRAILING_TAIL_PATTERNS:
        remainder = pattern.sub("", remainder)
    remainder = _LEADING_PARTICLE_PAT.sub("", remainder)
    remainder = _TRAILING_PARTICLE_PAT.sub("", remainder)
    remainder = remainder.strip()
    return remainder or None


class EntityResolver:
    def __init__(self, repository: CompanyMasterRepository):
        self._repository = repository
        self._company_resolver = CompanyResolver(repository)
        self._peer_selector = PeerSelector(repository)

    def resolve(self, question: str) -> ResolvedEntities:
        text = normalize_nfc(question)

        explicit_companies = self._company_resolver.find_explicit_companies(text)
        periods = _extract_periods(text)
        metric = _extract_metric(text)
        topic = _extract_topic(text, self._company_resolver)

        top_n_match = _TOP_N_PAT.search(text)
        if top_n_match:
            hint_word, n_str = top_n_match.group(1), top_n_match.group(2)
            top_n = int(n_str)
            sector = _detect_sector(hint_word, self._repository) or _detect_sector(
                text, self._repository
            )
            if sector:
                companies = self._peer_selector.select_by_sector(sector, top_n=top_n)
                sector_no = companies[0].sector_no if companies else None
                return ResolvedEntities(
                    entity_scope=EntityScope.SECTOR,
                    companies=[_to_ref(c) for c in companies],
                    sector=sector,
                    sector_no=sector_no,
                    periods=periods,
                    topic=topic,
                    metric=metric,
                    peer_selection=PeerSelectionMethod.MARKET_CAP_TOP_N,
                    requested_top_n=top_n,
                )

        if not explicit_companies:
            sector = _detect_sector(text, self._repository)
            if sector:
                companies = self._peer_selector.select_by_sector(sector, top_n=None)
                sector_no = companies[0].sector_no if companies else None
                return ResolvedEntities(
                    entity_scope=EntityScope.SECTOR,
                    companies=[_to_ref(c) for c in companies],
                    sector=sector,
                    sector_no=sector_no,
                    periods=periods,
                    topic=topic,
                    metric=metric,
                )

            industry = _detect_industry(text, self._repository)
            if industry:
                companies = self._repository.filter_by_industry(industry)
                return ResolvedEntities(
                    entity_scope=EntityScope.INDUSTRY,
                    companies=[_to_ref(c) for c in companies],
                    industry=industry,
                    periods=periods,
                    topic=topic,
                    metric=metric,
                )

            return ResolvedEntities(
                entity_scope=EntityScope.MARKET,
                companies=[],
                periods=periods,
                topic=topic,
                metric=metric,
            )

        sector, sector_no = _common_sector(explicit_companies)
        return ResolvedEntities(
            entity_scope=EntityScope.EXPLICIT_COMPANIES,
            companies=[_to_ref(c) for c in explicit_companies],
            sector=sector,
            sector_no=sector_no,
            periods=periods,
            topic=topic,
            metric=metric,
        )


def _to_ref(record: CompanyRecord) -> CompanyRef:
    return CompanyRef(
        corp_code=record.corp_code, stock_code=record.stock_code, corp_name=record.corp_name,
    )
