"""하이브리드 설계의 5번째 컴포넌트: Complexity Detector.

작업 지시사항이 예로 든 신호를 rule 기반으로 그대로 구현한다:

    - comparison_axis="period" 이면서 companies>=1
      (여기서는 ResolvedEntities.periods 개수 >= 2 로 판정한다 —
      `new/`의 EntityResolver 는 기존 시스템과 달리 `comparison_axis` 필드를
      따로 두지 않으므로, "서로 다른 두 시점을 언급했다"는 동등한 신호로
      periods 리스트 길이를 쓴다)
    - "왜"/"원인"/"이유" 같은 설명 요구 키워드
    - 다중 evidence_type 이 필요한 신호(companies>=2 두 회사 비교 + 설명 요구)

**설계 결정**: 세 신호 중 "설명 요구 키워드"가 없으면 단순 계산/비교로 보고
복잡 경로를 타지 않는다. 예를 들어 "2023년 대비 2024년 영업이익 증가율은?"은
periods=[2023,2024]로 2개지만 Calculation workflow(§21, deterministic Python
계산)로 충분히 처리되는 질문이라 Query Decomposition이 필요 없다 — 굳이 HCX-007
reasoning 호출을 태우면 §37 8개 케이스 전부가 매번 3번째 LLM 호출까지 하게 돼
"복잡할 때만 태운다"는 설계 의도(사용자 지시사항)가 무의미해진다. 반대로
"왜 개선됐어?"처럼 원인 설명을 요구하면서 동시에 기간/기업 축이 여러 개면
quantitative+business+management_commentary 를 조합해야 하는 진짜 복합
질문(SPEC §17/§18 예시와 정확히 일치)이므로 복잡 경로로 보낸다.
"""

from __future__ import annotations

from typing import Protocol

from app.hybrid.schemas import ComplexityAssessment
from app.query.schemas import EntityScope, ResolvedEntities

_EXPLANATION_KEYWORDS = ["왜", "원인", "이유", "배경"]


class ComplexityClassifier(Protocol):
    """LLM 확장 지점. rule 기반 판정이 애매하거나(설계 지침 "LLM 확장점도
    남겨라") 더 정교한 판단이 필요할 때 이 Protocol을 구현한 LLM 판정기를
    ComplexityDetector 생성자에 주입할 수 있다. 이번 구현은 실제 LLM
    구현체를 꽂지 않는다(rule만으로 §37+보강 질문에 충분히 대응됨을
    실측으로 확인했다 — 최종 보고서 참고)."""

    def classify(
        self, question: str, entities: ResolvedEntities, route: str,
    ) -> ComplexityAssessment: ...


class ComplexityDetector:
    def __init__(self, llm_classifier: ComplexityClassifier | None = None):
        self._llm_classifier = llm_classifier

    def assess(
        self, question: str, entities: ResolvedEntities, route: str,
    ) -> ComplexityAssessment:
        reasons: list[str] = []

        has_explanation_keyword = any(k in question for k in _EXPLANATION_KEYWORDS)
        if has_explanation_keyword:
            reasons.append("explanation_keyword")

        multi_period = len(entities.periods) >= 2
        if multi_period:
            reasons.append("multi_period")

        multi_company = len(entities.companies) >= 2
        if multi_company:
            reasons.append("multi_company")

        multi_scope = entities.entity_scope in (
            EntityScope.SECTOR, EntityScope.INDUSTRY, EntityScope.PEER_GROUP,
        )
        if multi_scope:
            reasons.append("multi_entity_scope")

        # 설명 요구 + (기간/기업/scope 중 하나라도 다중) 일 때만 진짜 복합
        # 질문으로 본다(위 docstring 근거).
        is_complex = has_explanation_keyword and (multi_period or multi_company or multi_scope)

        if not is_complex and self._llm_classifier is not None:
            return self._llm_classifier.classify(question, entities, route)

        return ComplexityAssessment(is_complex=is_complex, reasons=reasons)
