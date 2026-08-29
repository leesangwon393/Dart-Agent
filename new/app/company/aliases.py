"""SPEC.md §4-1: Alias dictionary.

Company Master 의 corp_name/listed_name/corp_eng_name/stock_code 만으로는
잡히지 않는 구어체 축약어를 수동으로 등록한다(스펙 §4-1 예시: "삼전"→삼성전자,
"하이닉스"→SK하이닉스, "현차"→현대자동차 — 단 "현차"는 이미 universe.csv 의
listed_name 컬럼에 존재해서 CompanyResolver 가 별도 alias 없이도 잡아낸다;
그래도 스펙 예시를 보존하려고 중복 등록해둔다. dict 라 중복 키는 문제 없음).

주의: 여기 값은 반드시 universe.csv 의 corp_name(정식 회사명)과 정확히
일치해야 한다 — CompanyResolver 가 이 값으로 CompanyMasterRepository.find_by_name()
을 호출하기 때문이다.
"""

from __future__ import annotations

MANUAL_ALIASES: dict[str, str] = {
    "삼전": "삼성전자",
    "하이닉스": "SK하이닉스",
    "현차": "현대자동차",
    "엘지엔솔": "LG에너지솔루션",
    "LG엔솔": "LG에너지솔루션",
    "포스코": "POSCO홀딩스",
}
