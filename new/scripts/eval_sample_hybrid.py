"""`router/eval_dataset.py`(src/disclosure_rag/router/eval_dataset.py) EVAL_SET
55건 중 margin 구간별로 12건을 표본추출해(§margin_threshold_resweep 문서의
버킷 정의: low<0.03 / mid 0.03~0.05 / high>=0.05, route 다양성도 고려) 하이브리드
Task Router(HCX-005, 매번 라이브 호출)의 route 분류 정확도를 기존
CascadingRouter(@margin_threshold=0.03, production 기본값)와 비교한다.

**방법론 노트 (중요)**: EVAL_SET 원본 질문은 "[COMPANY]"/"[COMPANY_1]" 같은
placeholder를 그대로 담고 있다 — 이는 실수가 아니라 `routes.py`의 학습
utterance도 동일한 placeholder를 쓰기 때문에(§margin resweep 문서, 실제
2026-08-29 재조정 작업이 이 placeholder 문자열 그대로 BGE-M3에 넣어
margin/route를 측정했다) 기존 시스템과 정확히 같은 입력으로 비교하려면
이 스크립트도 동일한 literal placeholder 문자열을 그대로 써야 한다. 회사명을
실제로 채워 넣으면(예: "삼성전자") 오히려 다른 실험이 된다(entity resolution
정확도 실험이지 route 분류 실험이 아니게 됨).

**legacy 쪽은 재호출하지 않고 이미 확보된 실측 캐시를 재사용한다**
(`results/router_v2/semantic_margin_2026-08-29.json` — BGE-M3 top1/top2/margin,
`results/router_v2/hcx_escalation_cache_2026-08-29.json` — margin<0.03인
항목의 실제 HCX-007 escalation 결과, `.env`의 실키로 2026-08-29에 이미
라이브 호출된 것) — "무리하게 재실행하지 마라"는 작업 지시사항과 "필요시
재실행"의 균형을 맞춘 것: legacy는 캐시가 이미 있으니 재실행하지 않고,
하이브리드(이번에 새로 만든 시스템)만 실제로 라이브 호출한다.

이 스크립트는 Task Router 정확도만 비교한다(Evidence Router는 EVAL_SET에
gold section 라벨이 없어 비교 대상이 아니다) — RPM/비용을 아끼기 위해
Evidence Router는 호출하지 않는다(§4 비용 비교 참고).

실행: (.venv 활성화 후) python new/scripts/eval_sample_hybrid.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NEW_ROOT = REPO_ROOT / "new"
sys.path.insert(0, str(NEW_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

# route 이름 매핑: 기존 시스템(6-way, *_analysis 접미사/multi_compare) <-> new/하이브리드(§6 이름)
_LEGACY_TO_NEW_ROUTE = {
    "single_lookup": "single_lookup",
    "correction_analysis": "correction",
    "multi_compare": "comparison",
    "calculation": "calculation",
    "ownership_analysis": "ownership",
    "event_analysis": "event",
}

# margin 버킷별 표본(2026-08-29 semantic_margin/hcx_escalation 캐시에서 stratified
# sampling, low<0.03(5)/mid 0.03-0.05(3)/high>=0.05(4), 코드: 재현 가능하도록
# random.seed(42)로 고정 선택한 결과를 그대로 박아둔다).
SAMPLE = [
    {"query": "[COMPANY] 감사의견이 뭐였어?", "expected": "single_lookup", "margin": 0.0152,
     "bucket": "low", "semantic_predicted": "single_lookup"},
    {"query": "[COMPANY] 부채비율이 몇 퍼센트인지 계산해줘", "expected": "calculation", "margin": 0.0185,
     "bucket": "low", "semantic_predicted": "single_lookup"},
    {"query": "[COMPANY] 최근 공시 중에 이벤트성 뉴스 있어?", "expected": "event_analysis", "margin": 0.0094,
     "bucket": "low", "semantic_predicted": "correction_analysis"},
    {"query": "[COMPANY] 자사주 비율이 어떻게 돼?", "expected": "ownership_analysis", "margin": 0.0102,
     "bucket": "low", "semantic_predicted": "calculation"},
    {"query": "[COMPANY] 재무제표 수치가 바뀐 부분이 있어?", "expected": "correction_analysis", "margin": 0.0188,
     "bucket": "low", "semantic_predicted": "correction_analysis"},
    {"query": "[COMPANY] 재무제표 좀 보여줘", "expected": "single_lookup", "margin": 0.0457,
     "bucket": "mid", "semantic_predicted": "single_lookup"},
    {"query": "[COMPANY] 매출 감소폭이 얼마야?", "expected": "calculation", "margin": 0.0491,
     "bucket": "mid", "semantic_predicted": "calculation"},
    {"query": "[COMPANY_1], [COMPANY_2], [COMPANY_3] 매출 규모 순서대로 알려줘", "expected": "multi_compare",
     "margin": 0.0396, "bucket": "mid", "semantic_predicted": "multi_compare"},
    {"query": "[COMPANY] 기재정정 사유 알려줘", "expected": "correction_analysis", "margin": 0.0621,
     "bucket": "high", "semantic_predicted": "correction_analysis"},
    {"query": "[COMPANY_1] vs [COMPANY_2] 부채비율 어디가 낮아?", "expected": "multi_compare", "margin": 0.8218,
     "bucket": "high", "semantic_predicted": "multi_compare"},
    {"query": "[COMPANY] 공장 증설 계획 있어?", "expected": "event_analysis", "margin": 0.8077,
     "bucket": "high", "semantic_predicted": "event_analysis"},
    {"query": "[COMPANY] 지분 5% 이상 가진 사람 있어?", "expected": "ownership_analysis", "margin": 0.7577,
     "bucket": "high", "semantic_predicted": "ownership_analysis"},
]

# margin<0.03(=위 SAMPLE의 "low" 버킷 5건)에 대한 실제 HCX-007 escalation 결과
# (results/router_v2/hcx_escalation_cache_2026-08-29.json 에서 그대로 발췌).
_HCX_ESCALATION_CACHE = {
    "[COMPANY] 감사의견이 뭐였어?": "single_lookup",
    "[COMPANY] 부채비율이 몇 퍼센트인지 계산해줘": "calculation",
    "[COMPANY] 최근 공시 중에 이벤트성 뉴스 있어?": "event_analysis",
    "[COMPANY] 자사주 비율이 어떻게 돼?": "ownership_analysis",
    "[COMPANY] 재무제표 수치가 바뀐 부분이 있어?": "correction_analysis",
}

MARGIN_THRESHOLD = 0.03  # production 기본값 (2026-08-29 재조정)


def cascading_router_route(item: dict) -> tuple[str, str]:
    """캐시된 데이터로 CascadingRouter@0.03의 최종 route를 재구성한다
    (재호출 없음). 반환: (route, source)."""
    if item["margin"] < MARGIN_THRESHOLD:
        route = _HCX_ESCALATION_CACHE[item["query"]]
        return route, "hcx_escalation"
    return item["semantic_predicted"], "semantic_fast_path"


def run_hybrid_task_router(sample: list[dict]) -> list[str]:
    from app.core.config import DEFAULT_CONFIG
    from app.company.repository import CompanyMasterRepository
    from app.query.entity_resolver import EntityResolver
    from app.hybrid.llm_task_router import HCXTaskRouter

    repo = CompanyMasterRepository(DEFAULT_CONFIG.universe_csv_path)
    entity_resolver = EntityResolver(repo)
    task_router = HCXTaskRouter()  # HCX-005, 매번 라이브 호출

    routes = []
    for item in sample:
        q = item["query"]
        entities = entity_resolver.resolve(q)  # placeholder라 companies=[] 로 나옴(의도된 것)
        task = task_router.route(q, entities)
        routes.append(task.route.value)
    return routes


def main():
    print(f"=== EVAL_SET 12건 표본 route 분류 3자 비교 (margin_threshold={MARGIN_THRESHOLD}) ===")
    print("[legacy] 캐시 재사용 (재호출 없음) / [hybrid] HCX-005 라이브 호출 12건\n")

    hybrid_routes = run_hybrid_task_router(SAMPLE)

    rows = []
    legacy_correct = 0
    hybrid_correct = 0
    for item, hybrid_route in zip(SAMPLE, hybrid_routes):
        legacy_route, legacy_source = cascading_router_route(item)
        expected_new_name = _LEGACY_TO_NEW_ROUTE[item["expected"]]
        legacy_ok = legacy_route == item["expected"]
        hybrid_ok = hybrid_route == expected_new_name
        legacy_correct += legacy_ok
        hybrid_correct += hybrid_ok
        rows.append({
            "query": item["query"], "bucket": item["bucket"], "margin": item["margin"],
            "expected": item["expected"],
            "legacy_route": legacy_route, "legacy_source": legacy_source, "legacy_ok": legacy_ok,
            "hybrid_route": hybrid_route, "hybrid_ok": hybrid_ok,
        })
        print(f"[{item['bucket']:<4} margin={item['margin']:.4f}] {item['query']}")
        print(f"    expected={item['expected']:<20} "
              f"legacy={legacy_route!s:<20}({legacy_source:<18}) {'OK' if legacy_ok else 'FAIL'}   "
              f"hybrid={hybrid_route:<12} {'OK' if hybrid_ok else 'FAIL'}")

    n = len(SAMPLE)
    print(f"\n legacy(CascadingRouter@0.03) accuracy: {legacy_correct}/{n} = {legacy_correct/n:.3f}")
    print(f" hybrid(HCX-005 Task Router)  accuracy: {hybrid_correct}/{n} = {hybrid_correct/n:.3f}")

    out_path = Path(__file__).resolve().parent / "eval_sample_hybrid_output.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()
