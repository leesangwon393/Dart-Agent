"""하이브리드 설계(사용자 3번째 제안, 작업 지시사항 상단 다이어그램)의 자체
schema. Task Router/Evidence Router 의 "출력 형태"는 Phase 1
(`app/routing/schemas.py`)과 동일해야 3자 비교가 공정하므로 그 스키마를
그대로 import 해서 재사용한다(로직은 재사용하지 않는다 — 이 파일이 새로
정의하는 건 Phase 1에 없던 두 개념, ComplexityAssessment 와
QueryDecomposition 뿐이다).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ComplexityAssessment(BaseModel):
    """ComplexityDetector 출력. `is_complex=True`일 때만 Reasoning Model
    (HCX-007) Query Decomposition + Evidence Planning 단계를 추가로 태운다."""

    is_complex: bool
    reasons: list[str] = Field(default_factory=list)


class SubQuery(BaseModel):
    """SPEC.md §17 subquery 예시 형태."""

    id: int
    company: str | None = None
    period: int | None = None
    topic: str
    evidence_types: list[str] = Field(default_factory=list)


class QueryDecompositionOutput(BaseModel):
    """ReasoningQueryDecomposer(HCX-007) 출력. §17 Query Decomposition +
    Evidence Planning 을 하나의 구조로 합쳤다."""

    subqueries: list[SubQuery] = Field(default_factory=list)
    evidence_plan_note: str | None = None
