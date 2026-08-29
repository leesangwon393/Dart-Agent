from __future__ import annotations

from app.query.schemas import EntityScope


def test_manual_alias_resolution(entity_resolver):
    entities = entity_resolver.resolve("삼전 영업이익 얼마야?")
    assert [c.corp_name for c in entities.companies] == ["삼성전자"]


def test_listed_name_alias_resolution(entity_resolver):
    entities = entity_resolver.resolve("현대차 매출 얼마야")
    assert [c.corp_name for c in entities.companies] == ["현대자동차"]


def test_no_company_no_sector_defaults_to_market_scope(entity_resolver):
    entities = entity_resolver.resolve("영업이익 얼마야?")
    assert entities.companies == []
    assert entities.entity_scope == EntityScope.MARKET


def test_period_extraction_two_years_in_order(entity_resolver):
    entities = entity_resolver.resolve("2023년 대비 2024년 매출 변화")
    assert entities.periods == [2023, 2024]


def test_metric_longest_match_wins(entity_resolver):
    entities = entity_resolver.resolve("삼성전자 영업이익률 알려줘")
    assert entities.metric == "영업이익률"
