"""§37 8개 케이스를 3개 시스템에 실제로 돌려서 비교한다:

  1. 기존 production 시스템: entity_extractor.py + CascadingRouter
     (margin_threshold=0.03, semantic 우선 -> margin<0.03이면 HCX-007 escalate)
  2. new/ Phase 1: 100% deterministic (rule)
  3. 하이브리드(이번 구현): rule Entity Resolver + rule Report Rule Router +
     LLM(HCX-005) Task Router + LLM(HCX-005) Evidence Router + rule Complexity
     Detector (+ 복잡하면 HCX-007 Query Decomposition)

주의: 이 스크립트는 "new/app/**가 src/disclosure_rag를 import하면 안 된다"는
제약의 대상이 아니다(compare_with_legacy.py와 동일한 예외 — 비교 목적
실행 도구). `new/app/hybrid/**`는 이 제약에서 애초에 제외돼 있다(작업
지시사항이 HCXClient 재사용을 명시적으로 허용).

HCX 실호출: CascadingRouter가 margin<0.03인 케이스에서 escalate하면 HCX-007을
호출하고, 하이브리드 Task/Evidence Router는 매 케이스마다 HCX-005를 호출한다.
`HCXClient`의 `min_interval_sec` pacing을 그대로 쓴다(직접 sleep 추가하지 않음).

실행: (.venv 활성화 후) python new/scripts/compare_three_way.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NEW_ROOT = REPO_ROOT / "new"
sys.path.insert(0, str(NEW_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

CORPUS_ROOT = REPO_ROOT / "corpus"
CONFIG_ROOT = REPO_ROOT / "config"

QUESTIONS = [
    "삼성전자 2024년 영업이익은?",
    "삼성전자와 SK하이닉스의 2024년 HBM 투자 전략을 비교해줘",
    "삼성전자 2023년 대비 2024년 영업이익 증가율은?",
    "삼성전자 사업보고서에서 정정된 내용 알려줘",
    "반도체 기업들의 최근 설비투자 전략을 비교해줘",
    "주요 방산기업 3곳의 수주 전략을 비교해줘",
    "삼성전자 최대주주 관련 내용 알려줘",
    "현대차 최근 유상증자 공시가 있어?",
]

# ComplexityDetector가 rule만으로는 §37 8개 케이스 전부에서 False가 나오므로
# (설명 요구 키워드가 하나도 없음), Reasoning Model 경로를 최소 1~2건 라이브로
# 검증하기 위한 보강 질문(SPEC §17 예시와 정확히 일치하는 형태).
COMPLEX_QUESTIONS = [
    "삼성전자의 2023년 대비 2024년 반도체 사업 수익성이 왜 개선됐어?",
    "SK하이닉스와 삼성전자의 영업이익률 차이가 나는 이유가 뭐야?",
]


def run_new_phase1():
    from app.core.config import DEFAULT_CONFIG
    from app.company.repository import CompanyMasterRepository
    from app.query.entity_resolver import EntityResolver
    from app.routing.task_router import TaskRouter
    from app.routing.evidence_router import EvidenceRouter

    repo = CompanyMasterRepository(DEFAULT_CONFIG.universe_csv_path)
    entity_resolver = EntityResolver(repo)
    task_router = TaskRouter()
    evidence_router = EvidenceRouter()

    results = []
    for q in QUESTIONS:
        entities = entity_resolver.resolve(q)
        task = task_router.route(q, entities)
        evidence = evidence_router.route(q, entities, task)
        results.append({
            "question": q,
            "route": task.route.value,
            "entity_scope": entities.entity_scope.value,
            "companies": [c.corp_name for c in entities.companies],
            "sector": entities.sector,
            "periods": entities.periods,
            "section_candidates": evidence.section_candidates,
            "evidence_types": evidence.evidence_types,
            "llm_call_count": 0,
        })
    return results


def run_legacy_cascading():
    from disclosure_rag.entity.entity_extractor import EntityExtractor
    from disclosure_rag.entity.query_normalizer import normalize_query
    from disclosure_rag.retrieval.embeddings import BgeM3EmbeddingProvider
    from disclosure_rag.router.hcx_router import build_cascading_router
    from disclosure_rag.agent.hcx_client import HCXClient

    extractor = EntityExtractor(
        corpus_root=CORPUS_ROOT,
        metric_terms_path=CONFIG_ROOT / "metric_terms.txt",
        event_terms_path=CONFIG_ROOT / "event_terms.txt",
        ownership_terms_path=CONFIG_ROOT / "ownership_terms.txt",
    )
    provider = BgeM3EmbeddingProvider(device="cpu")
    hcx_client = HCXClient()  # .env HCX_MODEL(HCX-007) 그대로 사용 (agent 모델)
    router = build_cascading_router(provider, hcx_client, margin_threshold=0.03)

    results = []
    for q in QUESTIONS:
        entities = extractor.extract(q)
        normalized = normalize_query(entities)
        route_result = router.route(normalized)
        results.append({
            "question": q,
            "route": route_result.route,
            "route_source": route_result.source,  # semantic_fast_path | hcx_escalation | hcx_unclear
            "route_score": route_result.score,
            "companies": entities.companies,
            "period": entities.period,
            "comparison_axis": entities.comparison_axis,
            "llm_call_count": 1 if route_result.source in ("hcx_escalation", "hcx_unclear") else 0,
        })
    return results


def run_hybrid():
    from app.core.config import DEFAULT_CONFIG
    from app.company.repository import CompanyMasterRepository
    from app.hybrid.orchestrator import HybridPipeline

    repo = CompanyMasterRepository(DEFAULT_CONFIG.universe_csv_path)
    pipeline = HybridPipeline(repo)

    results = []
    for q in QUESTIONS + COMPLEX_QUESTIONS:
        out = pipeline.run(q)
        results.append({
            "question": q,
            "route": out.task.route.value,
            "entity_scope": out.entities.entity_scope.value,
            "companies": [c.corp_name for c in out.entities.companies],
            "sector": out.entities.sector,
            "periods": out.task.periods,
            "report_types": out.report_types,
            "section_candidates": out.evidence.section_candidates,
            "evidence_types": out.evidence.evidence_types,
            "query_concepts": out.evidence.query_concepts,
            "is_complex": out.complexity.is_complex,
            "complexity_reasons": out.complexity.reasons,
            "decomposition": (
                [sq.model_dump() for sq in out.decomposition.subqueries]
                if out.decomposition else None
            ),
            "llm_call_count": out.llm_call_count,
        })
    return results


def main():
    print("=== [1/3] new/ Phase 1 (rule only, deterministic, 무료) ===")
    new_results = run_new_phase1()

    print("=== [2/3] 기존 production 시스템 (entity_extractor + CascadingRouter@0.03) ===")
    print("    (BGE-M3 로컬 로드 + margin<0.03 케이스는 HCX-007 라이브 호출)")
    legacy_results = run_legacy_cascading()

    print("=== [3/3] 하이브리드 (rule + LLM(HCX-005) 매번 + 복잡하면 HCX-007) ===")
    print("    (모든 케이스에서 HCX-005 최소 2회 라이브 호출)")
    hybrid_results = run_hybrid()

    out = {"new_phase1": new_results, "legacy_cascading": legacy_results, "hybrid": hybrid_results}
    out_path = Path(__file__).resolve().parent / "compare_three_way_output.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n\n=== 3자 비교표 (§37 8개 케이스) ===")
    for i, q in enumerate(QUESTIONS):
        n, l, h = new_results[i], legacy_results[i], hybrid_results[i]
        print(f"\n--- Case {i+1}: {q} ---")
        print(f"  [new Phase1] route={n['route']:<14} companies={n['companies']} sector={n['sector']}")
        print(f"  [legacy]     route={l['route']!s:<14} source={l['route_source']:<20} "
              f"companies={l['companies']} llm_calls={l['llm_call_count']}")
        print(f"  [hybrid]     route={h['route']:<14} companies={h['companies']} sector={h['sector']} "
              f"llm_calls={h['llm_call_count']}")

    print("\n\n=== 보강: Reasoning Model(HCX-007) Query Decomposition 라이브 검증 ===")
    for i, q in enumerate(COMPLEX_QUESTIONS):
        h = hybrid_results[len(QUESTIONS) + i]
        print(f"\n--- Complex Q{i+1}: {q} ---")
        print(f"  is_complex={h['is_complex']} reasons={h['complexity_reasons']} llm_calls={h['llm_call_count']}")
        print(f"  subqueries={json.dumps(h['decomposition'], ensure_ascii=False, indent=2)}")

    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()
