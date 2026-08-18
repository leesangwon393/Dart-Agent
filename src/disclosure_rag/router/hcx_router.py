"""Stage 9 에서 채택된 hcx_structured_router 를 정식 `src/` 코드로 승격하고,
CascadingRouter(semantic 우선 → 애매하면 HCX escalate) 를 추가한다.

## 배경: 왜 이 파일이 필요했는가 (2026-08-18, 사용자 피드백)

Stage 9 는 `hcx_structured_router` 를 최종 baseline 으로 채택했지만
(`results/router/hcx_structured_router/`), 그 구현 자체는 `src/`에 한 번도
정식으로 들어온 적이 없었다 — eval 스크립트 안에서만 존재했고, 이후
여러 세션에서 매번 거의 동일한 `HCXRouter` 클래스를 스크래치패드에
새로 작성해서 재사용해왔다. 그 구현에는 구조적 문제가 있었다:

1. **`classify_route` tool 을 `tool_choice` 로 강제 호출**했고, enum 이
   6개 route 뿐이었다 — 즉 질문이 아무리 애매해도 반드시 6개 중 하나로
   확정 배정됐다. `RouteResult.route: str | None` 설계상 `None` 은
   "fallback → Agent 가 직접 판단"을 의미하는데, 강제 6択 구조에서는
   이 fallback 이 **구조적으로 발생할 수 없었다**(Stage 9 결과의
   `fallback_rate=0.0` 이 그 증거).
2. `router/eval_dataset.py` 에는 애초에 이 문제를 테스트하려고 만든
   `AMBIGUOUS_SET`(§47, 여러 route 가 동시에 정답인 질문 4건)이 있었지만
   `evaluate_router()` 가 이걸 전혀 참조하지 않아 죽은 코드였다 — Stage 9
   에서 "라우터가 애매한 질문을 잘 처리하는가"는 사실 한 번도 테스트되지
   않았다.
3. `semantic_router` 쪽도 마찬가지로 문제가 있었다: `DEFAULT_THRESHOLD=0.5`
   가 top-1 절대 유사도 점수에 걸려 있는데, 재분석 결과 **절대 점수는
   정답/오답 구분력이 거의 없었다**(EVAL_SET 55건 기준 정답 median=0.781
   vs 오답 median=0.804 — 오답 쪽이 오히려 더 높다). 그래서 threshold=0.5
   에서는 거의 아무 것도 필터링되지 않고 그대로 통과됐다(confident=55/55).
   반면 **top1-top2 margin** 은 뚜렷한 구분력이 있었다: margin>=0.05 인
   하위집합은 accuracy=1.000(23/55, 42%), margin<0.05 인 나머지가
   진짜 애매한/헷갈리는 질문들이었다.

## 해결책

- `HCXStructuredRouter`: 기존과 동일하게 강제 tool-calling 을 쓰되, enum 에
  `"unclear"` 를 추가해서 "여러 유형에 걸치거나 애매함"을 정직하게 답할 수
  있게 한다(→ `RouteResult(route=None)`, Agent 자유 판단으로 감).
- `CascadingRouter`: semantic_router 를 절대 threshold 가 아니라 top1-top2
  margin 으로 게이팅해서 먼저 시도한다(로컬, ~40ms). margin 이 크면
  (기본 0.05) 그대로 채택 — 명백한 질문은 빠르게 처리된다. margin 이
  작으면 `HCXStructuredRouter` 로 escalate 한다(4.5초, 정확도 우선).
  HCX 도 unclear 면 그 결과(route=None) 를 그대로 돌려준다.
"""

from __future__ import annotations

from disclosure_rag.agent.hcx_client import HCXClient
from disclosure_rag.router.routes import ROUTE_UTTERANCES
from disclosure_rag.router.semantic_router_wrapper import Router, RouteResult

ROUTE_NAMES = list(ROUTE_UTTERANCES.keys())

# HCX Agent/Router system prompt 는 ~300자 넘으면 tool-calling 이 결정적으로
# 깨진다(3회 독립 재현, PROJECT_STATE.md §9 참고) — 짧게 유지.
_SYSTEM_PROMPT = (
    "질문을 분류해 classify_route를 호출하세요. "
    "여러 유형에 걸치거나 애매하면 route=unclear로 답하세요."
)

_ROUTE_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_route",
        "description": "질문을 유형 중 하나로 분류한다. 특정하기 어려우면 unclear.",
        "parameters": {
            "type": "object",
            "properties": {
                "route": {"type": "string", "enum": ROUTE_NAMES + ["unclear"]},
            },
            "required": ["route"],
        },
    },
}
_TOOL_CHOICE = {"type": "function", "function": {"name": "classify_route"}}


class HCXStructuredRouter:
    """Stage 9 채택 라우터의 정식 버전 — "unclear" escape hatch 포함."""

    name = "hcx_structured_router"

    def __init__(self, client: HCXClient, *, max_retries: int = 6):
        self._client = client
        self._max_retries = max_retries

    def route(self, normalized_query: str) -> RouteResult:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": normalized_query},
        ]
        msg = self._client.chat(
            messages, tools=[_ROUTE_TOOL], tool_choice=_TOOL_CHOICE,
            max_retries=self._max_retries,
        )
        tool_calls = msg.get("toolCalls") or []
        if not tool_calls:
            return RouteResult(route=None, score=None)
        route = tool_calls[0]["function"]["arguments"].get("route")
        if route not in ROUTE_NAMES:  # "unclear" 뿐 아니라 모델이 뭔가 이상한 값을 줘도 안전하게 fallback
            return RouteResult(route=None, score=None)
        return RouteResult(route=route, score=None)


class CascadingRouter:
    """semantic_router(로컬, margin 게이팅) 우선 시도 → 애매하면 HCX escalate."""

    name = "cascading"

    def __init__(self, semantic_router, hcx_router: Router, *, margin_threshold: float = 0.05):
        """`semantic_router`: `build_semantic_router(..., threshold=0.0)`로 만든
        raw `SemanticRouter` 객체를 넘길 것 — 라이브러리 자체의 절대 threshold
        게이팅이 아니라 여기서 top1-top2 margin으로 직접 게이팅하기 때문에,
        내부 threshold는 0으로 열어둬야 top-2 후보가 항상 나온다."""
        self._semantic = semantic_router
        self._hcx = hcx_router
        self._margin_threshold = margin_threshold

    def route(self, normalized_query: str) -> RouteResult:
        choices = self._semantic(normalized_query, limit=2)
        if not isinstance(choices, list):
            choices = [choices]
        top1 = choices[0]
        top1_score = top1.similarity_score or 0.0
        top2_score = (choices[1].similarity_score or 0.0) if len(choices) > 1 else 0.0
        margin = top1_score - top2_score
        if top1.name is not None and margin >= self._margin_threshold:
            return RouteResult(route=top1.name, score=top1_score)
        return self._hcx.route(normalized_query)
