"""하이브리드 설계의 마지막 컴포넌트: ComplexityDetector가 복잡하다고 판단한
질문에 대해서만 HCX-007(reasoning 모델)로 Query Decomposition + Evidence
Planning(SPEC §17)을 수행한다.

HCX-007은 thinking 모드가 기본 on 인데 tools 와 같이 쓰면 400이 난다(실측,
`hcx_client.py` docstring) — `HCXClient`가 모델명에 "007"이 있으면 자동으로
`thinking={"effort": "none"}`을 채워주므로 이 파일은 그 처리를 신경 쓸 필요가
없다(호출부 무수정 원칙, 클라이언트가 흡수).

§12 300자 system prompt 제약은 여기도 방어적으로 지킨다.
"""

from __future__ import annotations

from app.hybrid import _legacy_client_path  # noqa: F401
from disclosure_rag.agent.hcx_client import HCXClient

from app.hybrid.schemas import QueryDecompositionOutput, SubQuery
from app.query.schemas import ResolvedEntities
from app.routing.schemas import TaskRouterOutput

HCX_REASONING_MODEL = "HCX-007"

# 300자 이내 (§12).
_SYSTEM_PROMPT = (
    "복잡한 공시 질문을 여러 subquery로 분해하세요. 각 subquery는 회사/기간/"
    "topic/필요한 evidence_types를 가집니다. decompose_query를 호출하세요."
)

_EVIDENCE_TYPES = [
    "quantitative", "business", "market", "risk", "ownership", "event",
    "management_commentary",
]

_DECOMPOSE_TOOL = {
    "type": "function",
    "function": {
        "name": "decompose_query",
        "description": (
            "복잡한 질문(예: '2023년 대비 2024년 수익성이 왜 개선됐어?')을 "
            "SPEC.md §17 예시처럼 여러 subquery로 나눈다. 예: "
            "1) 2023년 실적 수치, 2) 2024년 실적 수치, "
            "3) 실적 변화의 시장/사업 배경, 4) 경영진 설명(management_commentary). "
            f"evidence_types는 {', '.join(_EVIDENCE_TYPES)} 중에서 고른다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subqueries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "company": {"type": "string"},
                            "period": {"type": "integer"},
                            "topic": {"type": "string"},
                            "evidence_types": {
                                "type": "array",
                                "items": {"type": "string", "enum": _EVIDENCE_TYPES},
                            },
                        },
                        "required": ["id", "topic", "evidence_types"],
                    },
                },
                "evidence_plan_note": {"type": "string"},
            },
            "required": ["subqueries", "evidence_plan_note"],
        },
    },
}
_TOOL_CHOICE = {"type": "function", "function": {"name": "decompose_query"}}


def _context(entities: ResolvedEntities, task: TaskRouterOutput) -> str:
    companies = ", ".join(c.corp_name for c in entities.companies) or "(없음)"
    return (
        f"[참고] route={task.route.value}, companies=[{companies}], "
        f"periods={task.periods}, topic={task.topic or '(없음)'}"
    )


class ReasoningQueryDecomposer:
    """ComplexityDetector.is_complex=True 인 질문에서만 호출된다. HCX-007로
    query decomposition + evidence planning(§17)을 수행한다."""

    def __init__(self, client: HCXClient | None = None, *, max_retries: int = 3):
        self._client = client or HCXClient(model=HCX_REASONING_MODEL)
        self._max_retries = max_retries

    def decompose(
        self, question: str, entities: ResolvedEntities, task: TaskRouterOutput,
    ) -> QueryDecompositionOutput:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"{question}\n\n{_context(entities, task)}"},
        ]
        msg = self._client.chat(
            messages, tools=[_DECOMPOSE_TOOL], tool_choice=_TOOL_CHOICE,
            max_retries=self._max_retries,
        )
        tool_calls = msg.get("toolCalls") or []
        if not tool_calls:
            return QueryDecompositionOutput()

        args = tool_calls[0]["function"]["arguments"]
        subqueries = [
            SubQuery(
                id=sq.get("id", i + 1),
                company=sq.get("company") or None,
                period=sq.get("period") or None,
                topic=sq.get("topic", ""),
                evidence_types=list(sq.get("evidence_types") or []),
            )
            for i, sq in enumerate(args.get("subqueries") or [])
        ]
        return QueryDecompositionOutput(
            subqueries=subqueries,
            evidence_plan_note=args.get("evidence_plan_note") or None,
        )
