"""하이브리드 설계의 Evidence Router — LLM(HCX-005) 버전.

작업 지시사항 아키텍처: report_type(공시종류)은 "Report Rule Router"(별도 rule
컴포넌트, `report_rule_router.py`)가 결정하고, 이 컴포넌트는 §8 스키마의 나머지
4개 필드(section_candidates/content_types/evidence_types/query_concepts)만
LLM으로 채운다. 즉 "어느 공시를 볼지"는 rule이, "그 공시 안에서 어디를/무엇을
볼지"는 LLM이 담당하도록 책임을 쪼갰다.

**§12 300자 system prompt 제약을 반드시 지킨다** — 이 프로젝트에서 3회 독립
재현된 버그(tool-calling + 긴 system prompt 조합에서 400 에러/deterministic
failure)라서 여기서도 방어적으로 지킨다. tool description(별도 필드)은 길게
써도 안전하다는 것도 이미 확인됐다(hcx_router.py 참고) — 여기서는 section
후보 예시 목록을 tool description 안에 넣는다.
"""

from __future__ import annotations

from app.hybrid import _legacy_client_path  # noqa: F401
from disclosure_rag.agent.hcx_client import HCXClient

from app.query.schemas import ResolvedEntities
from app.routing.schemas import EvidenceRouterOutput, TaskRouterOutput

HCX_EVIDENCE_ROUTER_MODEL = "HCX-005"

# 300자 이내 (§12).
_SYSTEM_PROMPT = (
    "공시 질문에 답하기 위해 어떤 section/자료 유형을 찾아야 하는지 "
    "plan_evidence를 호출해 정하세요. report_type은 이미 결정돼 있으니 "
    "신경쓰지 마세요."
)

_SECTION_EXAMPLES = [
    "사업의 내용", "재무에 관한 사항", "이사의 경영진단 및 분석의견",
    "주주에 관한 사항", "최대주주 등의 주식소유현황", "주식의 대량보유 상황",
    "임원 및 직원 등에 관한 사항", "위험관리 및 파생거래",
]
_CONTENT_TYPES = ["text", "table"]
_EVIDENCE_TYPES = [
    "quantitative", "business", "market", "risk", "ownership", "event",
    "management_commentary",
]

_EVIDENCE_TOOL = {
    "type": "function",
    "function": {
        "name": "plan_evidence",
        "description": (
            "질문에 답하는 데 필요한 공시 section/자료 유형을 계획한다.\n"
            f"section_candidates 예시: {', '.join(_SECTION_EXAMPLES)} "
            "(목록에 없어도 적절한 section명이면 자유롭게 추가 가능).\n"
            f"content_types: {', '.join(_CONTENT_TYPES)} 중 필요한 것.\n"
            f"evidence_types: {', '.join(_EVIDENCE_TYPES)} 중 필요한 것 "
            "(원인 설명이 필요하면 management_commentary도 포함).\n"
            "query_concepts: 검색에 쓸 동의어/관련어(예: HBM -> 고대역폭메모리)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "section_candidates": {"type": "array", "items": {"type": "string"}},
                "content_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": _CONTENT_TYPES},
                },
                "evidence_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": _EVIDENCE_TYPES},
                },
                "query_concepts": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "section_candidates", "content_types", "evidence_types", "query_concepts",
            ],
        },
    },
}
_TOOL_CHOICE = {"type": "function", "function": {"name": "plan_evidence"}}


def _task_context(entities: ResolvedEntities, task: TaskRouterOutput, report_types: list[str]) -> str:
    companies = ", ".join(c.corp_name for c in entities.companies) or "(없음)"
    return (
        f"[참고] route={task.route.value}, companies=[{companies}], "
        f"periods={task.periods}, topic={task.topic or '(없음)'}, "
        f"metric={task.metric or '(없음)'}, report_types(이미 결정됨)={report_types}"
    )


class HCXEvidenceRouter:
    """매 질문마다 HCX-005를 호출해 §8 스키마 중 report_types를 제외한
    나머지 필드를 채운다. report_types는 호출부(`orchestrator.py`)가
    `ReportRuleRouter` 결과를 그대로 채워 넣는다."""

    def __init__(self, client: HCXClient | None = None, *, max_retries: int = 3):
        self._client = client or HCXClient(model=HCX_EVIDENCE_ROUTER_MODEL)
        self._max_retries = max_retries

    def route(
        self,
        question: str,
        entities: ResolvedEntities,
        task: TaskRouterOutput,
        report_types: list[str],
    ) -> EvidenceRouterOutput:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{question}\n\n{_task_context(entities, task, report_types)}",
            },
        ]
        msg = self._client.chat(
            messages, tools=[_EVIDENCE_TOOL], tool_choice=_TOOL_CHOICE,
            max_retries=self._max_retries,
        )
        tool_calls = msg.get("toolCalls") or []
        if not tool_calls:
            return EvidenceRouterOutput(report_types=report_types)

        args = tool_calls[0]["function"]["arguments"]
        return EvidenceRouterOutput(
            report_types=report_types,
            section_candidates=list(args.get("section_candidates") or []),
            content_types=list(args.get("content_types") or []),
            evidence_types=list(args.get("evidence_types") or []),
            query_concepts=list(args.get("query_concepts") or []),
        )
