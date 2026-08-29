"""SPEC.md §6, §7, §29: Task Router.

6개 route(single_lookup/comparison/calculation/correction/ownership/event)로
질문을 분류하고 §7의 구조화 JSON을 만든다.

우선순위 기반 deterministic rule-chain 을 1차로 쓴다(§29 "Task Router → LLM
또는 classifier", 그리고 우리 기존 프로젝트의 Stage 8 ablation 결론
"rule-only 승자" — deterministic 우선 원칙). 확실한 신호가 하나도 없는
질문만 `RouteClassifier` Protocol(LLM 확장 지점)로 넘긴다 — 이번 Phase 1
에서는 실제 LLM 구현을 꽂지 않고, classifier 가 없으면 안전망으로
single_lookup 을 반환한다.

우선순위(먼저 매칭되는 규칙이 채택됨) — 이 순서는 §6의 route 정의 예문들
(특히 "두 기업의 영업이익률 차이"가 comparison이 아니라 calculation인 것,
"최초 공시와 최종 공시의 매출 차이"가 correction인 것)을 관찰해서 정했다:

    1. correction  (정정/최초·최종 공시 키워드)
    2. calculation (증가율/CAGR/성장률 등 명시적 연산 키워드)
    3. ownership   (최대주주/지분/대량보유 등)
    4. event       (유상증자/합병/전환사채 등 개별 이벤트 키워드)
    5. comparison  ("비교" 키워드, 회사 2개 이상, 또는 entity_scope가 sector/industry)
    6. single_lookup (그 외 — 회사/지표/topic 중 하나라도 있으면 안전한 기본값)
"""

from __future__ import annotations

from typing import Protocol

from app.company.repository import normalize_nfc
from app.query.schemas import EntityScope, ResolvedEntities
from app.query.terms import (
    CORRECTION_KEYWORDS,
    EVENT_TERMS,
    OWNERSHIP_TERMS,
)
from app.routing.schemas import Route, TaskRouterOutput

_CAGR_KEYWORDS = ["CAGR", "연평균성장률"]
_GROWTH_RATE_KEYWORDS = ["증가율", "감소율", "성장률", "증감률", "얼마나 늘었", "얼마나 줄었"]
_DIFFERENCE_KEYWORDS = ["차이", "증감액"]
_COMPARISON_KEYWORDS = ["비교", "중 어디가", "누가 더", "순위"]


def _detect_operation(text: str) -> str | None:
    if any(k in text for k in _CAGR_KEYWORDS):
        return "cagr"
    if any(k in text for k in _GROWTH_RATE_KEYWORDS):
        return "growth_rate"
    if any(k in text for k in _DIFFERENCE_KEYWORDS):
        return "difference"
    return None


def _detect_event_type(text: str) -> str | None:
    for term in EVENT_TERMS:
        if term in text:
            return term
    return None


class RouteClassifier(Protocol):
    """§29 "Task Router → LLM 또는 classifier" 확장 지점.

    deterministic rule-chain 이 확신 있는 신호를 하나도 못 찾았을 때만
    호출된다. Phase 1은 이 Protocol의 실제 구현체(HCX 등)를 만들지 않는다 —
    인터페이스만 남겨서 나중에 꽂을 수 있게 한다."""

    def classify(self, question: str, entities: ResolvedEntities) -> Route: ...


class TaskRouter:
    def __init__(self, llm_classifier: RouteClassifier | None = None):
        self._llm_classifier = llm_classifier

    def route(self, question: str, entities: ResolvedEntities) -> TaskRouterOutput:
        text = normalize_nfc(question) or ""
        route, extra = self._classify_deterministic(text, entities)

        if route is None:
            if self._llm_classifier is not None:
                route = self._llm_classifier.classify(text, entities)
            else:
                route = Route.SINGLE_LOOKUP

        return TaskRouterOutput.from_entities(entities, route, **extra)

    @staticmethod
    def _classify_deterministic(
        text: str, entities: ResolvedEntities,
    ) -> tuple[Route | None, dict]:
        if not text.strip():
            return None, {}

        if any(k in text for k in CORRECTION_KEYWORDS):
            return Route.CORRECTION, {"requires_historical_versions": True}

        operation = _detect_operation(text)
        if operation is not None:
            return Route.CALCULATION, {"requires_calculation": True, "operation": operation}

        if any(k in text for k in OWNERSHIP_TERMS):
            return Route.OWNERSHIP, {}

        event_type = _detect_event_type(text)
        if event_type is not None:
            return Route.EVENT, {"event_type": event_type}

        is_multi_entity_scope = entities.entity_scope in (
            EntityScope.SECTOR, EntityScope.INDUSTRY, EntityScope.PEER_GROUP,
        )
        if len(entities.companies) >= 2 or is_multi_entity_scope or any(
            k in text for k in _COMPARISON_KEYWORDS
        ):
            return Route.COMPARISON, {}

        if entities.companies or entities.metric or entities.topic:
            return Route.SINGLE_LOOKUP, {}

        # 회사/지표/topic/비교/계산/이벤트/정정 신호가 전부 없으면 진짜
        # 애매한 질문 — 여기서만 LLM classifier(또는 안전망)로 넘긴다.
        return None, {}
