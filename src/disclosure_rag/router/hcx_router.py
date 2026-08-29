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
from disclosure_rag.retrieval.embeddings import EmbeddingProvider
from disclosure_rag.router.routes import ROUTE_UTTERANCES
from disclosure_rag.router.semantic_router_wrapper import Router, RouteResult, build_semantic_router

ROUTE_NAMES = list(ROUTE_UTTERANCES.keys())

# HCX Agent/Router system prompt 는 ~300자 넘으면 tool-calling 이 결정적으로
# 깨진다(3회 독립 재현, PROJECT_STATE.md §9 참고) — 짧게 유지.
_SYSTEM_PROMPT = (
    "질문을 분류해 classify_route를 호출하세요. "
    "여러 유형에 걸치거나 애매하면 route=unclear로 답하세요."
)

# 2026-08-25 추가(§12 후보): CascadingRouter가 escalate하는 hard case에서
# HCX 자체 오분류가 여전히 많았다(routes.py 경계 재정리는 semantic_router
# 쪽만 개선했음, §5-A "남은 부분" 참고) — HCX는 routes.py의 utterance 예시를
# 안 보고 이 tool description만 보므로, route별 짧은 구별 기준을 직접 준다.
# system prompt(위 300자 제약)와는 별개 필드라 길이 제약이 다르지만, 과거
# 사례를 감안해 보수적으로 짧게 유지한다.
_ROUTE_DESCRIPTIONS = {
    "single_lookup": "문서에 그대로 적힌 단일 수치/사실 1개 조회(계산·비교 불필요)",
    "correction_analysis": "정정공시(기재정정) 사유/변경내용/원본-정정본 비교/정정이력",
    "multi_compare": "2개 이상 회사 또는 2개 이상 기간을 서로 비교",
    "calculation": "증가율/CAGR/비율처럼 문서에 없는 값을 연산해야 나옴(단순 조회면 single_lookup)",
    "ownership_analysis": "지분율/최대주주/종속기업·계열사 등 소유·지배구조",
    "event_analysis": "계약체결/해지, 자기주식취득 등 개별 이벤트 1건의 내용",
}

_ROUTE_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_route",
        "description": (
            "질문을 아래 유형 중 하나로 분류한다. 특정하기 어려우면 unclear.\n"
            + "\n".join(f"- {name}: {desc}" for name, desc in _ROUTE_DESCRIPTIONS.items())
        ),
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
            return RouteResult(route=None, score=None, source="hcx_unclear")
        route = tool_calls[0]["function"]["arguments"].get("route")
        if route not in ROUTE_NAMES:  # "unclear" 뿐 아니라 모델이 뭔가 이상한 값을 줘도 안전하게 fallback
            return RouteResult(route=None, score=None, source="hcx_unclear")
        return RouteResult(route=route, score=None, source="hcx_escalation")


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
            # CascadingRouter 자체는 새 source 를 만들지 않는다 — 여기서 만드는
            # RouteResult 는 raw SemanticRouter choice 를 감싸는 것뿐이라
            # SemanticRouterAdapter.route()와 동일한 의미로 "semantic_fast_path".
            return RouteResult(route=top1.name, score=top1_score, source="semantic_fast_path")
        # escalate: 하위 HCXStructuredRouter(또는 다른 Router 구현)가 이미
        # source 를 세팅한 RouteResult 를 그대로 통과시킨다.
        return self._hcx.route(normalized_query)


def build_cascading_router(
    embed_provider: EmbeddingProvider, hcx_client: HCXClient, *,
    margin_threshold: float = 0.05, hcx_max_retries: int = 6,
) -> CascadingRouter:
    """§12 "CascadingRouter를 ask.py 진입점에 실제 배선" — production 조립용
    단일 진입점. 여태까지 이 프로젝트의 모든 배치 스크립트가 (a) 이 CascadingRouter
    자체를 안 쓰고 SemanticRouterAdapter(절대 threshold=0.5)만 썼거나(§9-0의
    100문항 배치가 그 예 — accuracy 0.836), (b) CascadingRouter를 매번 즉석
    스텁으로 새로 조립해왔다(§12). 둘 다 이 함수 하나로 대체한다.

    `semantic_router`는 raw SemanticRouter를 threshold=0.0으로 만들어야 한다 —
    CascadingRouter가 라이브러리의 절대 threshold 게이팅이 아니라 자체
    top1-top2 margin으로 게이팅하기 때문에, top-2 후보가 항상 나와야 margin을
    계산할 수 있다(CascadingRouter.__init__ docstring 참고)."""
    semantic = build_semantic_router(embed_provider, threshold=0.0)
    hcx_router = HCXStructuredRouter(hcx_client, max_retries=hcx_max_retries)
    return CascadingRouter(semantic, hcx_router, margin_threshold=margin_threshold)
