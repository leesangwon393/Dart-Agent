"""SPEC.md §5 "주요 기업" 해석 — 가장 핵심적인 신규 아이디어.

    sector filter → market_cap descending → top N

"주요 OO기업 N곳"을 LLM이 자의적으로 고르지 않고, Company Master 데이터만으로
deterministic 하게 선택한다.
"""

from __future__ import annotations

from app.company.repository import CompanyMasterRepository, CompanyRecord


class PeerSelector:
    def __init__(self, repository: CompanyMasterRepository):
        self._repository = repository

    def select_by_sector(
        self, sector: str, *, top_n: int | None = None,
    ) -> list[CompanyRecord]:
        """sector 전체 또는 market_cap 상위 top_n 을 반환한다.

        top_n=None 이면 "반도체 기업들" 처럼 개수 제한이 없는 sector 전체
        universe(§37 Case 5)를, top_n=정수 이면 "주요 방산기업 3곳" 처럼
        market_cap 상위 N(§37 Case 6)을 의미한다.
        """
        pool = self._repository.filter_by_sector(sector)
        ranked = sorted(pool, key=lambda r: (r.market_cap or 0.0), reverse=True)
        if top_n is not None:
            return ranked[:top_n]
        return ranked

    def select_by_industry(
        self, industry: str, *, top_n: int | None = None,
    ) -> list[CompanyRecord]:
        pool = self._repository.filter_by_industry(industry)
        ranked = sorted(pool, key=lambda r: (r.market_cap or 0.0), reverse=True)
        if top_n is not None:
            return ranked[:top_n]
        return ranked
