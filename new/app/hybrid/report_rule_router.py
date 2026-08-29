"""하이브리드 설계의 "Report Rule Router" — rule 기반, report_type(공시종류)만
결정한다.

작업 지시사항의 아키텍처 다이어그램은 Report Rule Router 와 Evidence Router를
별개 박스로 그린다: Evidence Router(LLM)는 section_candidates/content_types/
evidence_types/query_concepts 만 담당하고, "어느 공시 종류를 볼지"는 이 rule
라우터가 먼저 확정한다. §8 예시 JSON은 report_types 를 evidence router 출력
스키마 안에 두지만, 이번 하이브리드 조립(`orchestrator.py`)에서는 이 rule
라우터의 결과를 report_types 필드에 그대로 꽂아 넣는다 — "report_type 결정은
LLM에게 맡기지 않는다"는 사용자의 아키텍처 의도를 그대로 반영한 것이다.

기존 시스템(`entity_extractor.py`)의 `_REPORT_NAME_TERMS`/event_terms substring
매칭 아이디어를 참고했지만 그 파일을 복제하지 않고 새로 짰다: 이 라우터는
(1) 질문에 공시명이 명시돼 있으면 그 명칭을 그대로 채택하고, (2) 없으면
Task Router 의 route 별 기본 report_type 테이블로 fallback한다.
"""

from __future__ import annotations

from app.company.repository import normalize_nfc
from app.routing.schemas import Route

# DART 공시명 그대로. 길이 내림차순으로 순회해야 "연결감사보고서"가
# "감사보고서"보다 먼저 매칭된다(부분집합 관계인 명칭들).
_EXPLICIT_REPORT_NAMES: list[str] = sorted(
    [
        "사업보고서", "반기보고서", "분기보고서",
        "주요사항보고서",
        "정정신고서", "기재정정",
        "연결감사보고서", "감사보고서",
        "주식등의대량보유상황보고서", "대량보유상황보고서",
    ],
    key=len, reverse=True,
)

# "기재정정"은 report_type 명칭 자체는 아니지만 질문에 등장하면 정정신고서를
# 봐야 한다는 신호이므로 별도로 매핑한다.
_ALIAS_TO_REPORT_TYPE = {
    "기재정정": "정정신고서",
}

# route 별 기본 report_type (질문에 공시명이 명시되지 않았을 때만 쓰는 fallback).
_DEFAULT_REPORT_TYPES_BY_ROUTE: dict[Route, list[str]] = {
    Route.SINGLE_LOOKUP: ["사업보고서"],
    Route.COMPARISON: ["사업보고서"],
    Route.CALCULATION: ["사업보고서"],
    Route.CORRECTION: ["사업보고서", "정정신고서"],
    Route.OWNERSHIP: ["사업보고서", "주식등의대량보유상황보고서"],
    Route.EVENT: ["주요사항보고서"],
}


class ReportRuleRouter:
    """rule 기반 report_type(공시종류) 결정기. LLM을 전혀 쓰지 않는다."""

    def route(self, question: str, route: Route) -> list[str]:
        text = normalize_nfc(question) or ""

        explicit: list[str] = []
        for name in _EXPLICIT_REPORT_NAMES:
            if name in text:
                mapped = _ALIAS_TO_REPORT_TYPE.get(name, name)
                if mapped not in explicit:
                    explicit.append(mapped)

        defaults = _DEFAULT_REPORT_TYPES_BY_ROUTE.get(route, ["사업보고서"])
        if not explicit:
            return list(defaults)

        # 명시된 공시명을 우선하되, correction/ownership처럼 route 자체가
        # "두 문서 종류를 함께 봐야" 의미가 성립하는 경우(정정 원본/정정본,
        # 지분보고서+사업보고서) route 기본값도 함께 union한다 — 명시 언급
        # 하나만으로 route가 원래 요구하는 문서군을 놓치지 않게 하려는 것.
        merged = list(explicit)
        for d in defaults:
            if d not in merged:
                merged.append(d)
        return merged
