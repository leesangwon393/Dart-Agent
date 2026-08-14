"""HCX Agent Loop (§57~61).

Entity Extraction(§35) + Query Normalize(§36) + Semantic Router(§37) 결과를
"힌트"로 Agent 에게 먼저 보여주고, 이후 실제 Tool 호출 여부/횟수는 HCX 가
스스로 판단하게 한다 (§59: 질문마다 필요한 tool 이 다르다 — 강제로 고정하지 않음).

무한 loop 방지를 위해 agent_max_iterations 를 둔다 (§61).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from disclosure_rag.agent.hcx_client import HCXClient
from disclosure_rag.agent.tools import ToolDef, dispatch_tool_call
from disclosure_rag.entity.entity_extractor import EntityExtractor, ExtractedEntities
from disclosure_rag.entity.query_normalizer import normalize_query
from disclosure_rag.router.semantic_router_wrapper import Router

logger = logging.getLogger(__name__)

# 주의: 이 프롬프트를 길게 늘리지 말 것 — 실측으로 확인된 이슈: system 프롬프트가
# 길면(이전 버전은 ~6줄 bullet, 400자+) tool-calling 2번째 turn 이후 HCX API 가
# 400("Unsupported function")을 결정적으로(재시도해도 실패) 반환했다. 같은 내용을
# 훨씬 짧게 줄이니 문제없이 동작함을 확인했다 — 원인은 불명(추정: system+tools+
# 누적 tool 결과 합산 길이 제한). 새 지침을 추가할 땐 반드시 실제 tool-calling
# 라운드트립(2턴 이상)까지 통과하는지 확인 후 반영할 것.
AGENT_SYSTEM_PROMPT = (
    "당신은 금융공시(DART) 분석 Agent입니다. 추측하지 말고 tool 로 확인하세요. "
    "정정/최신본 확인은 get_correction_history/get_latest_report, 계산은 calculate_* "
    "를 쓰세요. 근거가 충분하면 tool 호출을 멈추세요."
)


@dataclass
class ToolCallRecord:
    name: str
    arguments: dict
    result: dict


@dataclass
class AgentTrace:
    question: str
    entities: ExtractedEntities
    normalized_query: str
    route: str | None
    route_score: float | None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    iterations: int = 0
    stopped_reason: str = ""  # "no_more_tool_calls" | "max_iterations"
    final_assistant_message: str = ""


def _route_hint_message(entities: ExtractedEntities, route: str | None, route_score: float | None) -> str:
    return (
        "[질의 분석 결과 — 참고용 힌트, 최종 판단은 직접 하세요]\n"
        f"질문 유형(route): {route or '판단 불가 (직접 분류하세요)'}"
        + (f" (신뢰도 {route_score:.2f})" if route_score is not None else "")
        + f"\n회사: {entities.companies or '명시 안 됨'}\n"
        f"기간: {entities.period or '명시 안 됨'}\n"
        f"지표: {entities.metrics or '명시 안 됨'}\n"
        f"공시명: {entities.report_name or '명시 안 됨'}\n"
        f"정정 관련 질문 여부: {entities.explicit_correction}"
    )


def run_agent_loop(
    client: HCXClient,
    tools: list[ToolDef],
    question: str,
    *,
    entity_extractor: EntityExtractor,
    router: Router | None = None,
    max_iterations: int = 6,
) -> AgentTrace:
    entities = entity_extractor.extract(question)
    normalized = normalize_query(entities)
    route_result = router.route(normalized) if router is not None else None
    route = route_result.route if route_result else None
    route_score = route_result.score if route_result else None

    trace = AgentTrace(
        question=question, entities=entities, normalized_query=normalized,
        route=route, route_score=route_score,
    )

    messages = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": _route_hint_message(entities, route, route_score) + f"\n\n[질문]\n{question}"},
    ]
    hcx_tools = [t.to_hcx_schema() for t in tools]

    for i in range(max_iterations):
        trace.iterations = i + 1
        msg = client.chat(messages, tools=hcx_tools)
        messages.append({"role": msg["role"], "content": msg.get("content", ""), **({"toolCalls": msg["toolCalls"]} if msg.get("toolCalls") else {})})

        tool_calls = msg.get("toolCalls") or []
        if not tool_calls:
            trace.stopped_reason = "no_more_tool_calls"
            trace.final_assistant_message = msg.get("content", "")
            break

        for tc in tool_calls:
            fn = tc["function"]
            result = dispatch_tool_call(tools, fn["name"], fn["arguments"])
            trace.tool_calls.append(ToolCallRecord(name=fn["name"], arguments=fn["arguments"], result=result))
            messages.append({
                "role": "tool", "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
    else:
        trace.stopped_reason = "max_iterations"
        logger.warning("[AGENT] max_iterations(%d) 도달: question=%r", max_iterations, question)

    return trace
