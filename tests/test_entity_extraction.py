"""Phase 12 회귀 테스트: Entity Extraction + Query Normalize."""

from __future__ import annotations

from pathlib import Path

import pytest

from disclosure_rag.entity.entity_extractor import EntityExtractor
from disclosure_rag.entity.query_normalizer import normalize_query

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config"
pytestmark = pytest.mark.skipif(not CORPUS_ROOT.is_dir(), reason="corpus/ 없음")


@pytest.fixture(scope="module")
def extractor():
    return EntityExtractor(
        corpus_root=CORPUS_ROOT,
        metric_terms_path=CONFIG_ROOT / "metric_terms.txt",
        event_terms_path=CONFIG_ROOT / "event_terms.txt",
        ownership_terms_path=CONFIG_ROOT / "ownership_terms.txt",
    )


def test_spec_example_two_companies(extractor):
    """§35 명세 예시를 그대로 재현."""
    e = extractor.extract("삼성전자랑 SK하이닉스 최근 3년 영업이익률 비교해줘")
    assert e.companies == ["삼성전자", "SK하이닉스"]
    assert e.company_count == 2
    assert "최근 3년" in e.period
    assert "영업이익률" in e.metrics
    assert e.explicit_correction is False


def test_common_name_alias_resolution(extractor):
    """§2 README: 현대차→현대자동차, KT→케이티, 엔씨소프트→NC, LIG넥스원→LIG디펜스앤에어로스페이스."""
    assert extractor.extract("현대차 매출 얼마야").companies == ["현대자동차"]
    assert extractor.extract("KT 실적 알려줘").companies == ["케이티"]
    assert extractor.extract("엔씨소프트 영업이익").companies == ["NC"]
    assert extractor.extract("LIG넥스원 주요사항보고서").companies == ["LIG디펜스앤에어로스페이스"]


def test_explicit_correction_detection(extractor):
    e = extractor.extract("삼성전자 정정 전후 영업이익이 어떻게 달라졌어?")
    assert e.explicit_correction is True


def test_report_name_detection(extractor):
    e = extractor.extract("삼성전자 사업보고서에서 매출액 찾아줘")
    assert e.report_name == "사업보고서"


def test_no_company_mentioned(extractor):
    e = extractor.extract("영업이익 얼마야?")
    assert e.companies == []
    assert e.company_count == 0


def test_query_normalize_single_company(extractor):
    e = extractor.extract("삼성전자 2025년 영업이익 얼마야?")
    normalized = normalize_query(e)
    assert normalized == "[COMPANY] 2025년 영업이익 얼마야?"


def test_query_normalize_two_companies_numbered(extractor):
    e = extractor.extract("삼성전자와 SK하이닉스 매출 비교해줘")
    normalized = normalize_query(e)
    assert normalized == "[COMPANY_1]와 [COMPANY_2] 매출 비교해줘"


def test_query_normalize_no_company_returns_unchanged(extractor):
    e = extractor.extract("영업이익 얼마야?")
    assert normalize_query(e) == "영업이익 얼마야?"


def test_query_normalize_repeated_company_reuses_number(extractor):
    e = extractor.extract("삼성전자와 SK하이닉스 비교, 삼성전자가 더 커?")
    normalized = normalize_query(e)
    assert normalized.count("[COMPANY_1]") == 2
    assert normalized.count("[COMPANY_2]") == 1


# --- 2026-08-26 확장: period_type / period_comparison / event_terms /
# ownership_terms / comparison_axis 회귀 테스트 ---


def test_period_type_annual(extractor):
    e = extractor.extract("삼성전자의 2025년 매출액은 얼마야?")
    assert e.period_type == "annual"
    assert e.comparison_axis is None


def test_period_type_quarter_over_annual_priority(extractor):
    """"2025년 1분기" 처럼 annual+quarter 가 동시에 매칭되면 더 구체적인
    quarter 를 채택한다(우선순위 정책, entity_extractor.py 주석 참고)."""
    e = extractor.extract("삼성전자의 2025년 1분기 실적 알려줘")
    assert "2025년" in e.period
    assert "1분기" in e.period
    assert e.period_type == "quarter"


def test_period_comparison_detection(extractor):
    e = extractor.extract("삼성전자의 당기 대비 전기 영업이익 변화를 정리해줘")
    assert e.period_comparison is True


def test_event_terms_detection(extractor):
    e = extractor.extract("삼성전자의 자기주식취득 결정 내용 알려줘")
    assert "자기주식취득" in e.event_terms


def test_event_terms_generic_contract_wording(extractor):
    """파트 2 검증(실제 대회 예시 질문 5번) 중 발견된 갭 회귀: 문서 제목의
    명사구 형태("단일판매ㆍ공급계약체결")가 아니라 "~이 체결한 계약 이후
    해지된 계약"처럼 동사가 명사 앞에 오는 자연어 관계절 표현도 잡아야 한다."""
    e = extractor.extract("LG에너지솔루션이 2025년에 체결한 주요 계약 이후 해지된 계약이 존재하는가?")
    assert "체결" in e.event_terms
    assert "해지" in e.event_terms


def test_ownership_terms_detection_and_metrics_unrelated(extractor):
    e = extractor.extract("삼성전자의 최대주주가 누구야?")
    assert "최대주주" in e.ownership_terms
    assert e.metrics == []


def test_comparison_axis_company_for_two_company_query(extractor):
    e = extractor.extract("삼성전자와 SK하이닉스 중 설비투자가 더 큰 곳은?")
    assert e.comparison_axis == "company"


def test_comparison_axis_period_for_single_company_period_comparison(extractor):
    e = extractor.extract("삼성전자의 당기 대비 전기 영업이익 변화를 정리해줘")
    assert e.comparison_axis == "period"


def test_comparison_axis_prefers_company_when_both_signals_present(extractor):
    """회사 2개 + 기간 비교 신호가 동시에 있으면 company 를 우선한다(정책은
    entity_extractor.py 의 comparison_axis 계산부 주석 참고)."""
    e = extractor.extract("삼성전자와 SK하이닉스의 2023년과 2025년 매출을 비교해줘")
    assert e.company_count == 2
    assert len(e.period) >= 2
    assert e.comparison_axis == "company"
