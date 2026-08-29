from __future__ import annotations


def test_loads_all_70_companies(repository):
    assert len(repository.all()) == 70


def test_get_by_corp_code(repository):
    rec = repository.get_by_corp_code("00126380")
    assert rec is not None
    assert rec.corp_name == "삼성전자"
    assert rec.stock_code == "005930"


def test_get_by_stock_code(repository):
    rec = repository.get_by_stock_code("000660")
    assert rec is not None
    assert rec.corp_name == "SK하이닉스"


def test_find_by_name_matches_listed_name(repository):
    rec = repository.find_by_name("현대차")
    assert rec is not None
    assert rec.corp_name == "현대자동차"


def test_find_by_name_unknown_returns_none(repository):
    assert repository.find_by_name("존재하지않는회사") is None


def test_filter_by_sector(repository):
    semis = repository.filter_by_sector("반도체·전자부품")
    names = {r.corp_name for r in semis}
    assert names == {"삼성전자", "SK하이닉스", "삼성전기", "한미반도체", "LG이노텍"}


def test_top_n_by_market_cap_within_sector(repository):
    defense = repository.filter_by_sector("방산·항공우주")
    top3 = repository.top_n_by_market_cap(defense, n=3)
    assert [r.corp_name for r in top3] == ["한화에어로스페이스", "현대로템", "LIG디펜스앤에어로스페이스"]


def test_top_n_by_market_cap_descending(repository):
    top5 = repository.top_n_by_market_cap(n=5)
    caps = [r.market_cap for r in top5]
    assert caps == sorted(caps, reverse=True)
