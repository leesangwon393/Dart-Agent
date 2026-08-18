"""전체 온라인 파이프라인 진입점 (§34 다이어그램의 우측 절반):
질문 -> Entity/Router -> HCX Agent Loop(Tool Calling) -> Evidence Pack -> HCX Answer -> Validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from disclosure_rag.agent.agent_loop import AgentTrace, run_agent_loop
from disclosure_rag.agent.answer_generator import generate_answer
from disclosure_rag.agent.evidence import EvidencePack, build_evidence_pack
from disclosure_rag.agent.hcx_client import HCXClient
from disclosure_rag.agent.tools import ToolDef
from disclosure_rag.agent.validator import ValidationResult, validate_answer
from disclosure_rag.entity.entity_extractor import EntityExtractor
from disclosure_rag.router.semantic_router_wrapper import Router

logger = logging.getLogger(__name__)


@dataclass
class AskResult:
    question: str
    trace: AgentTrace
    evidence_pack: EvidencePack
    answer: str
    validation: ValidationResult


def ask(
    client: HCXClient,
    tools: list[ToolDef],
    question: str,
    *,
    entity_extractor: EntityExtractor,
    router: Router | None = None,
    max_iterations: int = 6,
    max_answer_retries: int = 1,
) -> AskResult:
    trace = run_agent_loop(
        client, tools, question,
        entity_extractor=entity_extractor, router=router, max_iterations=max_iterations,
    )
    evidence_pack = build_evidence_pack(trace)
    answer = generate_answer(client, evidence_pack)
    validation = validate_answer(answer, evidence_pack, trace.entities)

    # 2026-08-18: 답변 모델이 tool 없이 evidence 숫자를 암산해서 검산도 안 되는
    # 값을 낸 경우(numbers_grounded=False), 그냥 경고만 남기지 않고 교정 지시를
    # 덧붙여 최대 max_answer_retries 번 재생성한다 — "LLM 암산을 신뢰하지 않고
    # 직접 확인한다"는 원칙을 검증 단계뿐 아니라 최종 사용자에게 나가는 답변
    # 자체에도 적용한다(사용자 피드백: "숫자 계산은 LLM 안 시키고 직접 계산").
    retries = 0
    while not validation.numbers_grounded and retries < max_answer_retries:
        correction = (
            f"이전 답변의 숫자 {sorted(validation.ungrounded_numbers)}가 evidence 로 검산되지 "
            "않았습니다. 암산하지 말고, evidence 에 있는 원본 숫자만 쓰거나 계산 결과가 "
            "없으면 \"계산 결과가 제공되지 않았습니다\"라고 답하세요."
        )
        logger.warning("[VALIDATION] question=%r 검산 실패로 재생성 시도 %d/%d: %s",
                        question, retries + 1, max_answer_retries, sorted(validation.ungrounded_numbers))
        answer = generate_answer(client, evidence_pack, extra_instruction=correction)
        validation = validate_answer(answer, evidence_pack, trace.entities)
        retries += 1

    if validation.warnings:
        for w in validation.warnings:
            logger.warning("[VALIDATION] question=%r %s", question, w)

    return AskResult(question=question, trace=trace, evidence_pack=evidence_pack, answer=answer, validation=validation)
