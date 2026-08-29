"""SPEC.md §6, §7, §8: Task Router / Evidence Router 출력 Pydantic schema."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.query.schemas import CompanyRef, EntityScope, PeerSelectionMethod, ResolvedEntities


class Route(str, Enum):
    SINGLE_LOOKUP = "single_lookup"
    COMPARISON = "comparison"
    CALCULATION = "calculation"
    CORRECTION = "correction"
    OWNERSHIP = "ownership"
    EVENT = "event"


class TaskRouterOutput(BaseModel):
    """§7 JSON 예시를 그대로 따르되, Phase 1 구현 중 필요해진 3개 필드를
    추가했다(근거는 각 필드 옆 주석):

    - operation: §37 Case 3("operation = growth_rate")가 명시적으로 요구한다.
      §7 원본 스키마엔 없지만 계산 route 의 "무엇을 계산해야 하는지"를
      Router 단계에서 이미 알 수 있는 신호이므로 여기서 흘려보낸다(실제
      Calculation Planner(§21)는 Phase 4 대상이라 이번엔 값만 만든다).
    - event_type: §37 Case 8("event_type = 유상증자")가 명시적으로 요구한다.
    - sector/sector_no/industry/peer_selection/requested_top_n: ResolvedEntities
      와 동일한 필드를 그대로 노출한다(§37 Case 2/5/6이 sector/peer_selection
      값을 Router 출력에서 직접 확인하길 요구함).
    """

    route: Route
    entity_scope: EntityScope
    companies: list[CompanyRef] = Field(default_factory=list)
    sector: str | None = None
    sector_no: int | None = None
    industry: str | None = None
    periods: list[int] = Field(default_factory=list)
    metric: str | None = None
    topic: str | None = None
    requires_calculation: bool = False
    requires_historical_versions: bool = False
    peer_selection: PeerSelectionMethod | None = None
    requested_top_n: int | None = None
    operation: str | None = None
    event_type: str | None = None

    @classmethod
    def from_entities(
        cls,
        entities: ResolvedEntities,
        route: Route,
        *,
        requires_calculation: bool = False,
        requires_historical_versions: bool = False,
        operation: str | None = None,
        event_type: str | None = None,
    ) -> "TaskRouterOutput":
        return cls(
            route=route,
            entity_scope=entities.entity_scope,
            companies=entities.companies,
            sector=entities.sector,
            sector_no=entities.sector_no,
            industry=entities.industry,
            periods=entities.periods,
            metric=entities.metric,
            topic=entities.topic,
            requires_calculation=requires_calculation,
            requires_historical_versions=requires_historical_versions,
            peer_selection=entities.peer_selection,
            requested_top_n=entities.requested_top_n,
            operation=operation,
            event_type=event_type,
        )


class EvidenceRouterOutput(BaseModel):
    """§8 JSON 예시 그대로."""

    report_types: list[str] = Field(default_factory=list)
    section_candidates: list[str] = Field(default_factory=list)
    content_types: list[str] = Field(default_factory=list)
    evidence_types: list[str] = Field(default_factory=list)
    query_concepts: list[str] = Field(default_factory=list)
