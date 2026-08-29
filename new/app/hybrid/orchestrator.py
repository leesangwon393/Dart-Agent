"""하이브리드 설계(사용자 3번째 제안) 전체 파이프라인 조립.

    Entity Resolver       -> Rule            (app.query.entity_resolver, 재사용)
    Task Router           -> LLM (HCX-005)   (llm_task_router.py, 매 질문 호출)
    Report Rule Router    -> Rule            (report_rule_router.py)
    Evidence Router       -> LLM (HCX-005)   (llm_evidence_router.py, 매 질문 호출)
    Complexity Detector   -> Rule            (complexity_detector.py)
                                 |
                          복잡한 질문일 때만
                                 v
                     Reasoning Model(HCX-007)
                     Query Decomposition + Evidence Planning
                     (reasoning_decomposer.py)

Entity Resolver는 Phase 1(`app/query/entity_resolver.py`)을 그대로 import해서
재사용한다 — 세 설계 모두 "Entity Resolver는 rule"이라는 전제가 동일하므로
새로 만들지 않는다(작업 지시사항 명시).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.company.repository import CompanyMasterRepository
from app.hybrid.complexity_detector import ComplexityDetector
from app.hybrid.llm_evidence_router import HCXEvidenceRouter
from app.hybrid.llm_task_router import HCXTaskRouter
from app.hybrid.reasoning_decomposer import ReasoningQueryDecomposer
from app.hybrid.report_rule_router import ReportRuleRouter
from app.hybrid.schemas import ComplexityAssessment, QueryDecompositionOutput
from app.query.entity_resolver import EntityResolver
from app.query.schemas import ResolvedEntities
from app.routing.schemas import EvidenceRouterOutput, TaskRouterOutput


class HybridPipelineOutput(BaseModel):
    question: str
    entities: ResolvedEntities
    task: TaskRouterOutput
    report_types: list[str]
    evidence: EvidenceRouterOutput
    complexity: ComplexityAssessment
    decomposition: QueryDecompositionOutput | None = None
    # 이번 질문에 실제로 발생한 HCX 호출 횟수(비용/지연시간 비교용, §4).
    llm_call_count: int = 0


class HybridPipeline:
    def __init__(
        self,
        repository: CompanyMasterRepository,
        *,
        task_router: HCXTaskRouter | None = None,
        evidence_router: HCXEvidenceRouter | None = None,
        report_rule_router: ReportRuleRouter | None = None,
        complexity_detector: ComplexityDetector | None = None,
        reasoning_decomposer: ReasoningQueryDecomposer | None = None,
    ):
        self._entity_resolver = EntityResolver(repository)
        self._task_router = task_router or HCXTaskRouter()
        self._evidence_router = evidence_router or HCXEvidenceRouter()
        self._report_rule_router = report_rule_router or ReportRuleRouter()
        self._complexity_detector = complexity_detector or ComplexityDetector()
        self._reasoning_decomposer = reasoning_decomposer or ReasoningQueryDecomposer()

    def run(self, question: str) -> HybridPipelineOutput:
        llm_calls = 0

        entities = self._entity_resolver.resolve(question)

        task = self._task_router.route(question, entities)
        llm_calls += 1

        report_types = self._report_rule_router.route(question, task.route)

        evidence = self._evidence_router.route(question, entities, task, report_types)
        llm_calls += 1

        complexity = self._complexity_detector.assess(question, entities, task.route.value)

        decomposition: QueryDecompositionOutput | None = None
        if complexity.is_complex:
            decomposition = self._reasoning_decomposer.decompose(question, entities, task)
            llm_calls += 1

        return HybridPipelineOutput(
            question=question,
            entities=entities,
            task=task,
            report_types=report_types,
            evidence=evidence,
            complexity=complexity,
            decomposition=decomposition,
            llm_call_count=llm_calls,
        )
