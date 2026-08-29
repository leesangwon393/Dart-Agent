"""SPEC.md §8, §29: Evidence Router.

"어떤 공시 문서와 section을 우선 검색해야 하는지"(WHERE)를 Task Router의
route(WHAT)와 분리해서 결정한다. route 별 report_type/section 매핑은
정적 테이블로 deterministic 하게 정하고, single_lookup 은 metric 유무로
"재무 조회"인지 "사업 설명 조회"인지 한 번 더 분기한다.

query_concepts 확장(§16 Corpus-aware Query Expansion)은 optional LLM 자리를
남겨둔다(§29) — `ConceptExpander` Protocol. Phase 1은 미니 동의어 사전만
사용하고 실제 LLM 호출은 붙이지 않는다.
"""

from __future__ import annotations

from typing import Protocol

from app.query.schemas import ResolvedEntities
from app.routing.schemas import EvidenceRouterOutput, Route, TaskRouterOutput

# §16 예시(HBM 질문)를 재현하기 위한 최소 동의어 사전. 전체 corpus 기반
# term dictionary 는 Phase 2 이후(실제 공시 본문 접근 가능 시점) 대상.
_CONCEPT_SYNONYMS: dict[str, list[str]] = {
    "HBM": ["고대역폭메모리", "HBM3E"],
    "AI": ["인공지능", "생성형AI", "데이터센터"],
    "반도체": ["메모리", "파운드리", "생산능력"],
    "설비투자": ["CAPEX", "증설", "생산능력"],
    "수주": ["수주잔고", "계약체결"],
}

_FINANCIAL_SECTIONS = ["재무에 관한 사항"]
_BUSINESS_SECTIONS = ["사업의 내용", "이사의 경영진단 및 분석의견"]
_COMPARISON_SECTIONS = ["사업의 내용", "재무에 관한 사항", "이사의 경영진단 및 분석의견"]
_OWNERSHIP_SECTIONS = [
    "주주에 관한 사항", "최대주주 등의 주식소유현황", "주식의 대량보유 상황",
]


class ConceptExpander(Protocol):
    """§16/§29 optional LLM Query Expansion 확장 지점.

    deterministic 하게 만든 EvidenceRouterOutput 을 받아 query_concepts 만
    보강해서 돌려준다(report_type/section 판단 자체는 override하지 않는다 —
    "어디를 볼지"는 deterministic 판단을 신뢰하고, LLM은 "어떤 표현으로
    검색할지"만 돕는다는 §16 취지에 맞춘 설계)."""

    def expand_concepts(
        self, question: str, entities: ResolvedEntities, task: TaskRouterOutput,
        base_concepts: list[str],
    ) -> list[str]: ...


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _base_query_concepts(entities: ResolvedEntities, task: TaskRouterOutput) -> list[str]:
    concepts: list[str] = []
    if entities.topic:
        concepts.append(entities.topic)
        for word, synonyms in _CONCEPT_SYNONYMS.items():
            if word in entities.topic:
                concepts.extend(synonyms)
    if entities.metric:
        concepts.append(entities.metric)
    if task.event_type:
        concepts.append(task.event_type)
        for word, synonyms in _CONCEPT_SYNONYMS.items():
            if word in task.event_type:
                concepts.extend(synonyms)
    return _dedupe(concepts)


class EvidenceRouter:
    def __init__(self, concept_expander: ConceptExpander | None = None):
        self._concept_expander = concept_expander

    def route(
        self, question: str, entities: ResolvedEntities, task: TaskRouterOutput,
    ) -> EvidenceRouterOutput:
        output = self._plan_deterministic(entities, task)

        if self._concept_expander is not None:
            expanded = self._concept_expander.expand_concepts(
                question, entities, task, output.query_concepts,
            )
            output = output.model_copy(
                update={"query_concepts": _dedupe(output.query_concepts + list(expanded))}
            )
        return output

    @staticmethod
    def _plan_deterministic(
        entities: ResolvedEntities, task: TaskRouterOutput,
    ) -> EvidenceRouterOutput:
        query_concepts = _base_query_concepts(entities, task)

        if task.route == Route.SINGLE_LOOKUP:
            if entities.metric:
                return EvidenceRouterOutput(
                    report_types=["사업보고서"],
                    section_candidates=_FINANCIAL_SECTIONS,
                    content_types=["table", "text"],
                    evidence_types=["quantitative"],
                    query_concepts=query_concepts,
                )
            return EvidenceRouterOutput(
                report_types=["사업보고서"],
                section_candidates=_BUSINESS_SECTIONS,
                content_types=["text"],
                evidence_types=["business", "management_commentary"],
                query_concepts=query_concepts,
            )

        if task.route == Route.COMPARISON:
            return EvidenceRouterOutput(
                report_types=["사업보고서"],
                section_candidates=_COMPARISON_SECTIONS,
                content_types=["text", "table"],
                evidence_types=["business", "quantitative", "management_commentary"],
                query_concepts=query_concepts,
            )

        if task.route == Route.CALCULATION:
            return EvidenceRouterOutput(
                report_types=["사업보고서"],
                section_candidates=_FINANCIAL_SECTIONS,
                content_types=["table"],
                evidence_types=["quantitative"],
                query_concepts=query_concepts,
            )

        if task.route == Route.CORRECTION:
            # 정정은 correction_group 전체 버전을 대상으로 하므로(§22)
            # section 을 미리 좁히지 않는다 — 어느 section 이 바뀌었는지는
            # diff 단계(Phase 5)에서 알 수 있다.
            return EvidenceRouterOutput(
                report_types=["사업보고서", "정정신고서"],
                section_candidates=[],
                content_types=["text", "table"],
                evidence_types=["event"],
                query_concepts=query_concepts,
            )

        if task.route == Route.OWNERSHIP:
            return EvidenceRouterOutput(
                report_types=["사업보고서", "주식등의대량보유상황보고서"],
                section_candidates=_OWNERSHIP_SECTIONS,
                content_types=["text", "table"],
                evidence_types=["ownership"],
                query_concepts=query_concepts,
            )

        if task.route == Route.EVENT:
            return EvidenceRouterOutput(
                report_types=["주요사항보고서"],
                section_candidates=[],
                content_types=["text"],
                evidence_types=["event"],
                query_concepts=query_concepts,
            )

        # 방어적 fallback (Route enum 이 늘어나는 경우에만 도달)
        return EvidenceRouterOutput(query_concepts=query_concepts)
