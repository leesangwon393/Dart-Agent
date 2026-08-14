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
) -> AskResult:
    trace = run_agent_loop(
        client, tools, question,
        entity_extractor=entity_extractor, router=router, max_iterations=max_iterations,
    )
    evidence_pack = build_evidence_pack(trace)
    answer = generate_answer(client, evidence_pack)
    validation = validate_answer(answer, evidence_pack, trace.entities)

    if validation.warnings:
        for w in validation.warnings:
            logger.warning("[VALIDATION] question=%r %s", question, w)

    return AskResult(question=question, trace=trace, evidence_pack=evidence_pack, answer=answer, validation=validation)
