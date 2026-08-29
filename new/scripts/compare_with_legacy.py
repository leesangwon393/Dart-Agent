"""§37의 8개 테스트 케이스를 기존 시스템(src/disclosure_rag)에도 그대로
넣어보고, new/의 4개 컴포넌트 결과와 나란히 비교하기 위한 실행 스크립트.

주의: 이 스크립트는 COMPARISON.md 작성을 위한 "비교 목적 실행 도구"이며
`new/app/` 패키지 코드 자체는 아니다 — 작업 지시사항이 명시한 대로
"new/ 패키지 코드가 src/disclosure_rag를 import하면 안 된다"는 제약은
new/app/** 에만 적용되고, 이 스크립트에는 적용되지 않는다.

기존 시스템 파이프라인 재현 대상(src/disclosure_rag/agent/agent_loop.py 실측):
    entities = entity_extractor.extract(question)
    normalized = normalize_query(entities)
    route_result = router.route(normalized)

라우터는 BGE-M3(로컬 캐시 존재, API 호출 없음) 기반 SemanticRouterAdapter를
그대로 사용한다(tests/test_router.py 의 `_try_build_router` 패턴과 동일,
threshold=0.3). 네트워크 호출이나 API 키가 전혀 필요 없다.

실행: (.venv 활성화 후) python new/scripts/compare_with_legacy.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NEW_ROOT = REPO_ROOT / "new"
sys.path.insert(0, str(NEW_ROOT))  # new/app/*
sys.path.insert(0, str(REPO_ROOT / "src"))  # disclosure_rag (혹은 pip -e 설치본 사용)

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


def run_new_system():
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
            "entity_scope": entities.entity_scope.value,
            "companies": [c.corp_name for c in entities.companies],
            "sector": entities.sector,
            "periods": entities.periods,
            "metric": entities.metric,
            "route": task.route.value,
            "operation": task.operation,
            "event_type": task.event_type,
            "requires_calculation": task.requires_calculation,
            "requires_historical_versions": task.requires_historical_versions,
            "peer_selection": task.peer_selection.value if task.peer_selection else None,
            "requested_top_n": task.requested_top_n,
            "section_candidates": evidence.section_candidates,
            "evidence_types": evidence.evidence_types,
        })
    return results


def run_legacy_system():
    from disclosure_rag.entity.entity_extractor import EntityExtractor
    from disclosure_rag.entity.query_normalizer import normalize_query

    extractor = EntityExtractor(
        corpus_root=CORPUS_ROOT,
        metric_terms_path=CONFIG_ROOT / "metric_terms.txt",
        event_terms_path=CONFIG_ROOT / "event_terms.txt",
        ownership_terms_path=CONFIG_ROOT / "ownership_terms.txt",
    )

    router = None
    router_load_error = None
    try:
        from disclosure_rag.retrieval.embeddings import BgeM3EmbeddingProvider
        from disclosure_rag.router.semantic_router_wrapper import SemanticRouterAdapter

        provider = BgeM3EmbeddingProvider(device="cpu")
        router = SemanticRouterAdapter(provider, threshold=0.3)
    except Exception as e:  # noqa: BLE001
        router_load_error = str(e)

    results = []
    for q in QUESTIONS:
        entities = extractor.extract(q)
        normalized = normalize_query(entities)
        route_result = router.route(normalized) if router is not None else None
        results.append({
            "question": q,
            "companies": entities.companies,
            "period": entities.period,
            "period_type": entities.period_type,
            "period_comparison": entities.period_comparison,
            "metrics": entities.metrics,
            "event_terms": entities.event_terms,
            "ownership_terms": entities.ownership_terms,
            "comparison_axis": entities.comparison_axis,
            "explicit_correction": entities.explicit_correction,
            "report_name": entities.report_name,
            "normalized_query": normalized,
            "route": route_result.route if route_result else None,
            "route_score": route_result.score if route_result else None,
            "route_source": route_result.source if route_result else None,
            "router_load_error": router_load_error,
        })
    return results


def main():
    new_results = run_new_system()
    legacy_results = run_legacy_system()

    out = {"new": new_results, "legacy": legacy_results}
    out_path = NEW_ROOT / "scripts" / "compare_output.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    for i, (n, l) in enumerate(zip(new_results, legacy_results), start=1):
        print(f"=== Case {i}: {n['question']} ===")
        print(f"  [new]    route={n['route']:<12} entity_scope={n['entity_scope']:<20} "
              f"companies={n['companies']} sector={n['sector']} periods={n['periods']}")
        print(f"  [legacy] route={l['route']!s:<12} score={l['route_score']} "
              f"companies={l['companies']} period={l['period']} comparison_axis={l['comparison_axis']}")
        print()

    print(f"결과 저장: {out_path}")


if __name__ == "__main__":
    main()
