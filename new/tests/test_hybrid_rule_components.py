"""하이브리드 설계(new/app/hybrid/)의 rule 기반 컴포넌트만 검증한다
(ComplexityDetector, ReportRuleRouter) — LLM(HCX) 호출이 필요한
HCXTaskRouter/HCXEvidenceRouter/ReasoningQueryDecomposer 는 API 키 없이도
CI에서 항상 돌아가야 한다는 §38 원칙("LLM이 없어도 deterministic component
테스트가 가능해야 한다")에 따라 이 파일의 대상이 아니다 — 그 세 컴포넌트의
실제 검증은 라이브 HCX 호출로 `new/scripts/compare_three_way.py` /
`new/scripts/eval_sample_hybrid.py` 에서 수행했다(최종 보고서 참고)."""

from __future__ import annotations

from app.hybrid.complexity_detector import ComplexityDetector
from app.hybrid.report_rule_router import ReportRuleRouter
from app.routing.schemas import Route


def test_complexity_false_for_all_37_cases(entity_resolver):
    """§37 8개 케이스는 설명 요구 키워드("왜"/"원인"/"이유")가 하나도 없으므로
    ComplexityDetector 가 전부 False 를 반환해야 한다 — 즉 이 케이스들에서는
    Reasoning Model(HCX-007) 경로를 타지 않는다(비용/RPM 관점에서 중요)."""
    questions = [
        "삼성전자 2024년 영업이익은?",
        "삼성전자와 SK하이닉스의 2024년 HBM 투자 전략을 비교해줘",
        "삼성전자 2023년 대비 2024년 영업이익 증가율은?",
        "삼성전자 사업보고서에서 정정된 내용 알려줘",
        "반도체 기업들의 최근 설비투자 전략을 비교해줘",
        "주요 방산기업 3곳의 수주 전략을 비교해줘",
        "삼성전자 최대주주 관련 내용 알려줘",
        "현대차 최근 유상증자 공시가 있어?",
    ]
    detector = ComplexityDetector()
    for q in questions:
        entities = entity_resolver.resolve(q)
        assert detector.assess(q, entities, "n/a").is_complex is False, q


def test_complexity_true_for_explanation_plus_multi_period(entity_resolver):
    q = "삼성전자의 2023년 대비 2024년 반도체 사업 수익성이 왜 개선됐어?"
    entities = entity_resolver.resolve(q)
    result = ComplexityDetector().assess(q, entities, "n/a")
    assert result.is_complex is True
    assert "explanation_keyword" in result.reasons
    assert "multi_period" in result.reasons


def test_complexity_true_for_explanation_plus_multi_company(entity_resolver):
    q = "SK하이닉스와 삼성전자의 영업이익률 차이가 나는 이유가 뭐야?"
    entities = entity_resolver.resolve(q)
    result = ComplexityDetector().assess(q, entities, "n/a")
    assert result.is_complex is True
    assert "explanation_keyword" in result.reasons
    assert "multi_company" in result.reasons


def test_complexity_false_when_only_explanation_keyword_no_multi_axis(entity_resolver):
    """설명 요구 키워드만 있고 기간/기업/scope 축이 전부 단일이면 여전히
    False (단순 single_lookup 이 "왜"를 묻는 경우 - Reasoning 모델 없이도
    single_lookup workflow 로 충분히 처리 가능하다는 설계 판단)."""
    q = "삼성전자가 HBM 사업을 왜 중요하게 생각해?"
    entities = entity_resolver.resolve(q)
    result = ComplexityDetector().assess(q, entities, "n/a")
    assert result.is_complex is False


def test_report_rule_router_explicit_mention_wins_but_merges_route_default():
    router = ReportRuleRouter()
    result = router.route("삼성전자 사업보고서에서 정정된 내용 알려줘", Route.CORRECTION)
    assert result[0] == "사업보고서"  # 명시된 공시명이 우선
    assert "정정신고서" in result  # correction route 기본값도 함께 포함


def test_report_rule_router_event_default():
    router = ReportRuleRouter()
    assert router.route("현대차 최근 유상증자 공시가 있어?", Route.EVENT) == ["주요사항보고서"]


def test_report_rule_router_ownership_default():
    router = ReportRuleRouter()
    result = router.route("삼성전자 최대주주 관련 내용 알려줘", Route.OWNERSHIP)
    assert result == ["사업보고서", "주식등의대량보유상황보고서"]
