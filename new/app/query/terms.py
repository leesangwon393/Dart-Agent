"""§4/§8용 소규모 term dictionary. 기존 시스템의 config/*.txt(metric_terms.txt,
event_terms.txt, ownership_terms.txt) 파일 자체는 import/참조하지 않는다
(비교 대상 독립성 유지) — 다만 동일한 "사전 substring 매칭" 아이디어는
스펙 §4-1 "deterministic lookup 우선" 원칙에 따라 재사용한다. 목록은
새로 정리했고, 이번 Phase 1(§37 8개 테스트)에 필요한 만큼만 채웠다 —
전체 재무 용어 사전은 Phase 2 이후 corpus 기반으로 확장 대상.

길이 내림차순으로 순회해야 "영업이익률"이 "영업이익"보다 먼저 매칭된다.
"""

from __future__ import annotations

METRIC_TERMS: list[str] = [
    "영업이익률", "영업이익", "당기순이익률", "당기순이익", "순이익률", "순이익",
    "매출액", "매출", "부채비율", "부채총계", "자본총계", "자산총계",
    "유동비율", "연구개발비", "배당금", "EBITDA", "ROE", "자기자본이익률",
    "CAGR",
]
METRIC_TERMS = sorted(set(METRIC_TERMS), key=len, reverse=True)

CALCULATION_KEYWORDS: list[str] = [
    "증가율", "감소율", "성장률", "증감률", "증감액", "CAGR", "연평균성장률",
    "몇 %", "몇%", "영업이익률 차이", "순이익률 차이",
]

OWNERSHIP_TERMS: list[str] = [
    "최대주주", "주요주주", "특수관계자", "특별관계자", "대량보유", "지분율",
    "보유비율", "주식소유", "경영권", "자기주식", "주주구성", "5% 이상 보유",
]

# event_type 추출용. 첫 매칭을 event_type 으로 채택하므로, "유상증자"처럼
# 구체적인 항목을 "증자"보다 먼저 오도록 길이 내림차순 유지.
EVENT_TERMS: list[str] = [
    "유상증자", "무상증자", "전환사채", "신주인수권부사채", "교환사채",
    "회사합병", "합병", "회사분할", "분할합병", "주식교환", "영업양수",
    "영업양도", "자기주식취득", "자기주식처분", "자기주식소각",
    "단일판매ㆍ공급계약체결", "단일판매·공급계약체결", "단일판매ㆍ공급계약해지",
    "단일판매·공급계약해지", "신규시설투자", "시설투자", "타법인주식취득",
    "M&A", "인수합병", "소송", "감자", "상장폐지",
]
EVENT_TERMS = sorted(set(EVENT_TERMS), key=len, reverse=True)

CORRECTION_KEYWORDS: list[str] = [
    "정정", "기재정정", "최초 공시", "최종 공시", "원본", "수정본",
]

REPORT_NAME_TERMS: list[str] = [
    "사업보고서", "반기보고서", "분기보고서", "주요사항보고서",
    "주식등의대량보유상황보고서", "대량보유상황보고서", "감사보고서",
]

# "비교" 의도를 나타내는 표현. company_count>=2 가 아니어도(예: sector 전체
# 비교) 이 키워드가 있으면 comparison route 후보가 된다.
COMPARISON_KEYWORDS: list[str] = ["비교", "중 어디가", "누가 더", "순위"]
