"""하이브리드 설계의 Task Router — LLM(HCX-005) 버전.

작업 지시사항: "Task Router 는 일반 LLM/작은 모델"이며, 기존 `CascadingRouter`의
HCX escalation과 유사하지만 **margin 게이팅 없이 매 질문마다** 호출한다는 점이
다르다. `entity_extractor.py`/`hcx_router.py` 의 라우팅 *로직*(margin 계산,
semantic router, RouteResult 등)은 전혀 가져오지 않고, 인프라성 클라이언트인
`HCXClient` 만 import 해서 재사용한다(작업 지시사항이 명시적으로 허용).

Entity Resolver(rule, Phase 1과 동일)가 이미 뽑아둔 companies/periods/sector를
LLM에게 참고 정보로 넘겨서 route 판단을 돕는다 — LLM에게 회사명 추출까지
맡기지 않는다(SPEC §4-1 원칙은 하이브리드 설계에서도 유지: Entity Resolver는
그대로 rule).

§12(300자 system prompt 제약)는 이 라우터에도 방어적으로 적용한다 — 실제로
버그가 재현된 곳은 Evidence Router(tool 호출 + 긴 system prompt 조합)이지만,
같은 tool-calling 메커니즘을 쓰는 이 라우터도 안전하게 짧게 유지한다.
"""

from __future__ import annotations

from app.hybrid import _legacy_client_path  # noqa: F401  (sys.path 부트스트랩, import 순서 유지 필요)
from disclosure_rag.agent.hcx_client import HCXClient

from app.query.schemas import ResolvedEntities
from app.routing.schemas import Route, TaskRouterOutput

HCX_TASK_ROUTER_MODEL = "HCX-005"

# 300자 이내 (§12). 실측: len() 기준 아래 문자열은 130자 내외.
_SYSTEM_PROMPT = (
    "공시 질문을 6가지 유형(single_lookup/comparison/calculation/correction/"
    "ownership/event) 중 하나로 분류해 classify_task를 호출하세요. "
    "회사/기간 정보는 참고용입니다."
)

_ROUTE_NAMES = [r.value for r in Route]

_ROUTE_DESCRIPTIONS = {
    "single_lookup": "하나의 기업/주제에 대한 단순 조회·설명 (계산·비교 불필요)",
    "comparison": "기업 간 또는 기간 간 비교",
    "calculation": "증가율/CAGR/비율 등 검색된 숫자로 명시적 연산이 필요함",
    "correction": "정정공시의 원본/수정본 비교, 기재정정 사유",
    "ownership": "최대주주/지분/대량보유 등 지분·소유 구조",
    "event": "유상증자/합병/전환사채 등 개별 이벤트 발생 여부",
}

_TASK_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_task",
        "description": (
            "질문을 아래 6개 유형 중 하나로 분류하고 부가 정보를 채운다.\n"
            + "\n".join(f"- {name}: {desc}" for name, desc in _ROUTE_DESCRIPTIONS.items())
            + "\noperation: calculation일 때만 growth_rate/cagr/difference 중 하나, "
            "아니면 none.\n"
            "event_type: event일 때만 구체적 이벤트명(예: 유상증자), 아니면 빈 문자열."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "route": {"type": "string", "enum": _ROUTE_NAMES},
                "requires_calculation": {"type": "boolean"},
                "requires_historical_versions": {"type": "boolean"},
                "operation": {
                    "type": "string",
                    "enum": ["growth_rate", "cagr", "difference", "none"],
                },
                "event_type": {"type": "string"},
            },
            "required": [
                "route", "requires_calculation", "requires_historical_versions",
                "operation", "event_type",
            ],
        },
    },
}
_TOOL_CHOICE = {"type": "function", "function": {"name": "classify_task"}}


def _entities_context(entities: ResolvedEntities) -> str:
    companies = ", ".join(c.corp_name for c in entities.companies) or "(없음)"
    periods = ", ".join(str(p) for p in entities.periods) or "(없음)"
    return (
        f"[참고: rule 기반 Entity Resolver 결과] "
        f"entity_scope={entities.entity_scope.value}, companies=[{companies}], "
        f"sector={entities.sector or '(없음)'}, periods=[{periods}], "
        f"metric={entities.metric or '(없음)'}"
    )


class HCXTaskRouter:
    """매 질문마다 HCX-005를 호출해 6-way 분류를 수행한다(margin 게이팅 없음)."""

    def __init__(self, client: HCXClient | None = None, *, max_retries: int = 3):
        self._client = client or HCXClient(model=HCX_TASK_ROUTER_MODEL)
        self._max_retries = max_retries

    def route(self, question: str, entities: ResolvedEntities) -> TaskRouterOutput:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{question}\n\n{_entities_context(entities)}",
            },
        ]
        msg = self._client.chat(
            messages, tools=[_TASK_TOOL], tool_choice=_TOOL_CHOICE,
            max_retries=self._max_retries,
        )
        tool_calls = msg.get("toolCalls") or []
        if not tool_calls:
            # 안전망: tool 호출이 비어 오면 Phase 1과 동일하게 single_lookup으로
            # fallback한다(§29 "확실한 신호가 없으면 안전한 기본값").
            return TaskRouterOutput.from_entities(entities, Route.SINGLE_LOOKUP)

        args = tool_calls[0]["function"]["arguments"]
        route_value = args.get("route")
        try:
            route = Route(route_value)
        except ValueError:
            route = Route.SINGLE_LOOKUP

        operation = args.get("operation") or None
        if operation == "none":
            operation = None
        event_type = args.get("event_type") or None

        return TaskRouterOutput.from_entities(
            entities,
            route,
            requires_calculation=bool(args.get("requires_calculation", False)),
            requires_historical_versions=bool(args.get("requires_historical_versions", False)),
            operation=operation,
            event_type=event_type,
        )
