"""SPEC.md §4: Entity / Universe Resolver 출력 Pydantic schema."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class EntityScope(str, Enum):
    EXPLICIT_COMPANIES = "explicit_companies"
    SECTOR = "sector"
    INDUSTRY = "industry"
    MARKET = "market"
    PEER_GROUP = "peer_group"


class PeerSelectionMethod(str, Enum):
    MARKET_CAP_TOP_N = "market_cap_top_n"


class CompanyRef(BaseModel):
    """§7 Router 출력 예시의 companies 배열 항목과 동일한 형태."""

    corp_code: str
    stock_code: str | None = None
    corp_name: str


class ResolvedEntities(BaseModel):
    """§4 JSON 스키마 그대로 + Phase 1 구현 중 추가한 2개 필드.

    peer_selection/requested_top_n 은 스펙 §4 원본 JSON에는 없지만, §5
    "주요 기업 = sector 내 market_cap 상위 N" 규칙이 실제로 적용됐는지(그리고
    top_n 이 몇이었는지)를 라우터 출력에서 추적 가능하게 하려고 추가했다
    (§37 Case 6 "peer_selection = market_cap_top_n, top_n = 3" 요구사항).
    """

    entity_scope: EntityScope
    companies: list[CompanyRef] = Field(default_factory=list)
    sector: str | None = None
    sector_no: int | None = None
    industry: str | None = None
    periods: list[int] = Field(default_factory=list)
    topic: str | None = None
    metric: str | None = None

    peer_selection: PeerSelectionMethod | None = None
    requested_top_n: int | None = None
