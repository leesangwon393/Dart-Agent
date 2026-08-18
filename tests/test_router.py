"""Phase 13+14 회귀 테스트: Semantic Router + Eval.

BGE-M3 로딩이 필요해 느리다. CPU 경합 시 매우 느려질 수 있으므로 seperate 로 표시.
"""

from __future__ import annotations

import pytest

from disclosure_rag.router.eval import evaluate_router, evaluate_router_ambiguous, threshold_sweep
from disclosure_rag.router.eval_dataset import AMBIGUOUS_SET, EVAL_SET
from disclosure_rag.router.hcx_router import CascadingRouter, HCXStructuredRouter, ROUTE_NAMES
from disclosure_rag.router.routes import ROUTE_UTTERANCES
from disclosure_rag.router.semantic_router_wrapper import NoRouter, RouteResult, SemanticRouterAdapter


def _try_build_router(threshold: float = 0.5):
    try:
        from disclosure_rag.retrieval.embeddings import BgeM3EmbeddingProvider

        provider = BgeM3EmbeddingProvider(device="cpu")
        return SemanticRouterAdapter(provider, threshold=threshold)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"BGE-M3 모델 로딩 불가: {e}")


def test_routes_cover_six_intents():
    assert set(ROUTE_UTTERANCES.keys()) == {
        "single_lookup", "correction_analysis", "multi_compare",
        "calculation", "ownership_analysis", "event_analysis",
    }
    for name, utterances in ROUTE_UTTERANCES.items():
        assert len(utterances) >= 10, f"{name}: utterance 수가 너무 적음"


def test_eval_set_disjoint_from_training_utterances():
    """§48: 등록 utterance 와 평가셋은 반드시 분리돼야 한다."""
    all_training = {u for utts in ROUTE_UTTERANCES.values() for u in utts}
    eval_queries = {ex.query for ex in EVAL_SET}
    assert not (all_training & eval_queries)


def test_no_router_always_falls_back():
    router = NoRouter()
    result = router.route("[COMPANY] 영업이익 얼마야?")
    assert result.route is None


@pytest.mark.slow
def test_semantic_router_routes_clear_single_lookup_query():
    router = _try_build_router(threshold=0.3)
    result = router.route("[COMPANY] 영업이익 알려줘")
    assert result.route == "single_lookup"


@pytest.mark.slow
def test_semantic_router_routes_clear_correction_query():
    router = _try_build_router(threshold=0.3)
    result = router.route("[COMPANY] 정정공시에서 뭐가 바뀌었어?")
    assert result.route == "correction_analysis"


@pytest.mark.slow
def test_semantic_router_high_threshold_increases_fallback():
    """threshold 를 극단적으로 높이면 fallback rate 가 올라가야 한다 (sanity check)."""
    router = _try_build_router(threshold=0.3)
    low_report = evaluate_router(router, EVAL_SET)
    router.set_threshold(0.99)
    high_report = evaluate_router(router, EVAL_SET)
    assert high_report.fallback_rate >= low_report.fallback_rate


@pytest.mark.slow
def test_router_eval_report_has_expected_fields():
    router = _try_build_router(threshold=0.3)
    report = evaluate_router(router, EVAL_SET)
    assert 0.0 <= report.accuracy <= 1.0
    assert 0.0 <= report.macro_f1 <= 1.0
    assert 0.0 <= report.fallback_rate <= 1.0
    assert report.n == len(EVAL_SET)
    assert len(report.confusion_matrix) == len(report.labels)


@pytest.mark.slow
def test_threshold_sweep_runs_without_rebuilding():
    router = _try_build_router(threshold=0.3)
    results = threshold_sweep(router, EVAL_SET, thresholds=[0.2, 0.5, 0.8])
    assert set(results.keys()) == {0.2, 0.5, 0.8}
    for t, report in results.items():
        assert report.n == len(EVAL_SET)


# ── 2026-08-18 회귀 테스트: HCXStructuredRouter unclear escape hatch,
# CascadingRouter margin 게이팅, AMBIGUOUS_SET 평가 (§47 죽은 코드 부활) ──
# 사용자 피드백: "HCX가 6개 route 중 하나만 무조건 고르도록 세팅이 되어
# 있어서 애매한 쿼리에 대해서 제대로 처리하지 못하는 문제" — Stage 9의
# fallback_rate=0.0 이 바로 이 증상이었다.


class _StubHCXClient:
    """실제 API 호출 없이 HCXStructuredRouter 를 검증하기 위한 스텁."""

    def __init__(self, route_value: str):
        self._route_value = route_value

    def chat(self, messages, *, tools=None, tool_choice=None, max_retries=6, **kwargs):
        return {"toolCalls": [{"function": {"arguments": {"route": self._route_value}}}]}


def test_hcx_structured_router_returns_route_when_confident():
    router = HCXStructuredRouter(_StubHCXClient("single_lookup"))
    result = router.route("[COMPANY] 영업이익 알려줘")
    assert result.route == "single_lookup"


def test_hcx_structured_router_unclear_maps_to_fallback():
    """핵심 회귀: HCX가 "unclear"를 고르면 반드시 route=None(fallback)이어야
    한다 — 이전 구현은 이 선택지 자체가 없어서 애매한 질문도 6개 중 하나로
    억지로 배정됐다."""
    router = HCXStructuredRouter(_StubHCXClient("unclear"))
    result = router.route("정정된 영업이익이 몇 % 줄었어?")
    assert result.route is None


def test_hcx_structured_router_unknown_value_also_falls_back():
    """모델이 enum 밖의 값을 뱉는 방어적인 경우도 크래시 대신 fallback."""
    router = HCXStructuredRouter(_StubHCXClient("garbage_value"))
    result = router.route("아무 질문")
    assert result.route is None


class _StubRouteChoice:
    def __init__(self, name, similarity_score):
        self.name = name
        self.similarity_score = similarity_score


class _StubSemanticRouter:
    """CascadingRouter 가 기대하는 `router(query, limit=2) -> list[choice]`
    인터페이스를 흉내내는 스텁 — top1/top2 점수를 고정해서 margin 게이팅을
    결정론적으로 검증한다."""

    def __init__(self, top1_name, top1_score, top2_score):
        self._top1_name = top1_name
        self._top1_score = top1_score
        self._top2_score = top2_score

    def __call__(self, query, limit=2):
        return [
            _StubRouteChoice(self._top1_name, self._top1_score),
            _StubRouteChoice("other_route", self._top2_score),
        ]


class _StubRouter:
    name = "stub_hcx"

    def __init__(self, route_value):
        self._route_value = route_value
        self.called = False

    def route(self, normalized_query: str) -> RouteResult:
        self.called = True
        return RouteResult(route=self._route_value, score=None)


def test_cascading_router_uses_semantic_when_margin_wide():
    """margin(top1-top2)이 threshold 이상이면 HCX를 호출하지 않고 semantic
    결과를 그대로 채택해야 한다(빠른 경로)."""
    semantic = _StubSemanticRouter("single_lookup", 0.80, 0.60)  # margin=0.20
    hcx = _StubRouter("event_analysis")
    router = CascadingRouter(semantic, hcx, margin_threshold=0.05)
    result = router.route("[COMPANY] 영업이익 알려줘")
    assert result.route == "single_lookup"
    assert not hcx.called, "margin이 충분히 크면 HCX를 호출하면 안 됨"


def test_cascading_router_escalates_to_hcx_when_margin_narrow():
    """margin이 threshold 미만이면(=진짜 애매한 질문) HCX로 escalate 해야 한다
    — 이게 바로 사용자가 요청한 cascade 구조의 핵심 동작."""
    semantic = _StubSemanticRouter("calculation", 0.82, 0.80)  # margin=0.02
    hcx = _StubRouter("correction_analysis")
    router = CascadingRouter(semantic, hcx, margin_threshold=0.05)
    result = router.route("정정된 영업이익이 몇 % 줄었어?")
    assert result.route == "correction_analysis"
    assert hcx.called, "margin이 좁으면 반드시 HCX로 escalate 해야 함"


def test_cascading_router_propagates_hcx_unclear_as_fallback():
    """escalate 했는데 HCX도 애매하다고 하면(route=None) 그대로 Agent 자유
    판단으로 넘겨야 한다 — route 강제 배정이 완전히 사라지는 최종 경로."""
    semantic = _StubSemanticRouter("calculation", 0.82, 0.80)  # margin=0.02 -> escalate
    hcx = _StubRouter(None)
    router = CascadingRouter(semantic, hcx, margin_threshold=0.05)
    result = router.route("애매한 질문")
    assert result.route is None
    assert hcx.called


def test_evaluate_router_ambiguous_counts_fallback_as_appropriate():
    """AMBIGUOUS_SET(§47) 평가: fallback(route=None)도 '적절한 처리'로 세야
    한다 — 진짜 애매한 질문에서는 정직하게 모른다고 하는 게 억지로 하나를
    찍는 것보다 낫다."""
    router = _StubRouter(None)  # 뭘 물어봐도 fallback
    report = evaluate_router_ambiguous(router, AMBIGUOUS_SET)
    assert report.n == len(AMBIGUOUS_SET)
    assert report.appropriate_rate == 1.0
    assert report.fallback_rate == 1.0
    assert report.forced_wrong_rate == 0.0


def test_evaluate_router_ambiguous_flags_forced_wrong_answers():
    """AMBIGUOUS_SET 의 acceptable 목록에 절대 없는 route 를 강제로 찍으면
    forced_wrong 으로 잡혀야 한다 — 이게 바로 (수정 전) HCX 라우터의 실제
    실패 모드였다."""
    router = _StubRouter("__never_a_valid_route__")
    report = evaluate_router_ambiguous(router, AMBIGUOUS_SET)
    assert report.forced_wrong_rate == 1.0
    assert report.appropriate_rate == 0.0


def test_hcx_structured_router_tool_enum_includes_unclear():
    """tool schema 자체에 unclear 선택지가 있는지 고정 — 이게 없으면 HCX가
    구조적으로 강제 6択을 벗어날 수 없다(사용자가 지적한 원래 버그)."""
    from disclosure_rag.router.hcx_router import _ROUTE_TOOL

    enum = _ROUTE_TOOL["function"]["parameters"]["properties"]["route"]["enum"]
    assert "unclear" in enum
    assert set(ROUTE_NAMES) <= set(enum)


def test_hcx_router_system_prompt_stays_short():
    """잠긴 제약: HCX Router system prompt 는 ~300자 넘으면 tool-calling 이
    결정적으로 깨진다(3회 독립 재현, PROJECT_STATE.md 참고)."""
    from disclosure_rag.router.hcx_router import _SYSTEM_PROMPT

    assert len(_SYSTEM_PROMPT) < 300
