"""SPEC.md §37: Router Test Questions — 8개 케이스 전부.

각 테스트는 EntityResolver → TaskRouter → EvidenceRouter 전체 파이프라인을
돌려서 §37에 명시된 "기대" 값만 정확히 검증한다(스펙에 없는 필드는 참고용
출력일 뿐 강제 assert하지 않는다 — 예: topic 필드는 §37 어느 케이스에서도
명시적으로 요구되지 않으므로 강한 assert 대상이 아니다)."""

from __future__ import annotations

from app.query.schemas import EntityScope, PeerSelectionMethod
from app.routing.schemas import Route


def _names(companies) -> list[str]:
    return [c.corp_name for c in companies]


def _run(question, entity_resolver, task_router, evidence_router):
    entities = entity_resolver.resolve(question)
    task = task_router.route(question, entities)
    evidence = evidence_router.route(question, entities, task)
    return entities, task, evidence


# ── Case 1 ──────────────────────────────────────────────────────────────
def test_case1_single_lookup_financial(entity_resolver, task_router, evidence_router):
    q = "삼성전자 2024년 영업이익은?"
    entities, task, evidence = _run(q, entity_resolver, task_router, evidence_router)

    assert _names(entities.companies) == ["삼성전자"]
    assert entities.periods == [2024]
    assert task.route == Route.SINGLE_LOOKUP
    # "evidence = financial" -> quantitative evidence_type, 재무 section.
    assert "quantitative" in evidence.evidence_types
    assert "재무에 관한 사항" in evidence.section_candidates


# ── Case 2 ──────────────────────────────────────────────────────────────
def test_case2_comparison_two_companies(entity_resolver, task_router, evidence_router):
    q = "삼성전자와 SK하이닉스의 2024년 HBM 투자 전략을 비교해줘"
    entities, task, evidence = _run(q, entity_resolver, task_router, evidence_router)

    assert _names(entities.companies) == ["삼성전자", "SK하이닉스"]
    assert entities.periods == [2024]
    assert task.route == Route.COMPARISON
    assert entities.sector == "반도체·전자부품"
    assert task.sector == "반도체·전자부품"
    for expected_section in ["사업의 내용", "재무에 관한 사항", "이사의 경영진단 및 분석의견"]:
        assert expected_section in evidence.section_candidates


# ── Case 3 ──────────────────────────────────────────────────────────────
def test_case3_calculation_growth_rate(entity_resolver, task_router, evidence_router):
    q = "삼성전자 2023년 대비 2024년 영업이익 증가율은?"
    entities, task, evidence = _run(q, entity_resolver, task_router, evidence_router)

    assert task.route == Route.CALCULATION
    assert entities.periods == [2023, 2024]
    assert task.operation == "growth_rate"
    assert entities.metric == "영업이익"
    assert task.requires_calculation is True


# ── Case 4 ──────────────────────────────────────────────────────────────
def test_case4_correction(entity_resolver, task_router, evidence_router):
    q = "삼성전자 사업보고서에서 정정된 내용 알려줘"
    entities, task, evidence = _run(q, entity_resolver, task_router, evidence_router)

    assert task.route == Route.CORRECTION
    assert task.requires_historical_versions is True


# ── Case 5 ──────────────────────────────────────────────────────────────
def test_case5_sector_comparison_semiconductor(entity_resolver, task_router, evidence_router):
    q = "반도체 기업들의 최근 설비투자 전략을 비교해줘"
    entities, task, evidence = _run(q, entity_resolver, task_router, evidence_router)

    assert entities.entity_scope == EntityScope.SECTOR
    assert entities.sector == "반도체·전자부품"
    assert task.route == Route.COMPARISON
    # sector filter 로 deterministic 하게 생성된 universe (top N 제한 없음)
    assert set(_names(entities.companies)) == {
        "삼성전자", "SK하이닉스", "삼성전기", "한미반도체", "LG이노텍",
    }


# ── Case 6 (핵심 신규 아이디어) ─────────────────────────────────────────
def test_case6_peer_group_top_n_defense(entity_resolver, task_router, evidence_router):
    q = "주요 방산기업 3곳의 수주 전략을 비교해줘"
    entities, task, evidence = _run(q, entity_resolver, task_router, evidence_router)

    assert entities.entity_scope == EntityScope.SECTOR
    assert entities.sector == "방산·항공우주"
    assert task.route == Route.COMPARISON
    assert entities.peer_selection == PeerSelectionMethod.MARKET_CAP_TOP_N
    assert entities.requested_top_n == 3
    assert len(entities.companies) == 3
    # market_cap 내림차순 top 3 (sector filter -> market_cap desc -> top N).
    assert _names(entities.companies) == [
        "한화에어로스페이스", "현대로템", "LIG디펜스앤에어로스페이스",
    ]


# ── Case 7 ──────────────────────────────────────────────────────────────
def test_case7_ownership(entity_resolver, task_router, evidence_router):
    q = "삼성전자 최대주주 관련 내용 알려줘"
    entities, task, evidence = _run(q, entity_resolver, task_router, evidence_router)

    assert task.route == Route.OWNERSHIP
    assert any("주주" in s for s in evidence.section_candidates)
    assert any("최대주주" in s for s in evidence.section_candidates)


# ── Case 8 ──────────────────────────────────────────────────────────────
def test_case8_event_rights_offering(entity_resolver, task_router, evidence_router):
    q = "현대차 최근 유상증자 공시가 있어?"
    entities, task, evidence = _run(q, entity_resolver, task_router, evidence_router)

    assert _names(entities.companies) == ["현대자동차"]
    assert task.route == Route.EVENT
    assert task.event_type == "유상증자"
