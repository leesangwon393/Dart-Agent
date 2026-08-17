"""100문항을 6개 카테고리(검색추출/비교연산/복합추론 × Closed/Open)에
분배해서 생성한다. 회사는 38개사 풀에서 라운드로빈으로 배정해 중복을
최소화한다."""
import json

COMPANIES = [
    "삼성전자", "삼성SDI", "LG에너지솔루션", "한미반도체", "KB금융",
    "알테오젠", "HD현대중공업", "현대자동차", "현대건설", "SK텔레콤",
    "기아", "SK하이닉스", "NAVER", "카카오", "신한지주",
    "삼성바이오로직스", "셀트리온", "하이브", "와이지엔터테인먼트", "크래프톤",
    "두산로보틱스", "레인보우로보틱스", "한화에어로스페이스", "한국항공우주",
    "이마트", "LG생활건강", "아모레퍼시픽", "POSCO홀딩스", "고려아연",
    "HMM", "현대글로비스", "대우건설", "케이티", "LG유플러스",
    "미래에셋증권", "에코프로비엠", "현대모비스", "삼성전기",
]

SECTOR = {
    "삼성전자": "IT/반도체", "삼성SDI": "2차전지", "LG에너지솔루션": "2차전지",
    "한미반도체": "반도체", "KB금융": "금융", "알테오젠": "바이오", "HD현대중공업": "조선",
    "현대자동차": "자동차", "현대건설": "건설", "SK텔레콤": "통신", "기아": "자동차",
    "SK하이닉스": "반도체", "NAVER": "IT/플랫폼", "카카오": "IT/플랫폼", "신한지주": "금융",
    "삼성바이오로직스": "바이오", "셀트리온": "바이오", "하이브": "엔터", "와이지엔터테인먼트": "엔터",
    "크래프톤": "게임", "두산로보틱스": "로봇", "레인보우로보틱스": "로봇",
    "한화에어로스페이스": "방산", "한국항공우주": "방산", "이마트": "유통",
    "LG생활건강": "화장품", "아모레퍼시픽": "화장품", "POSCO홀딩스": "철강",
    "고려아연": "비철금속", "HMM": "해운", "현대글로비스": "물류", "대우건설": "건설",
    "케이티": "통신", "LG유플러스": "통신", "미래에셋증권": "증권",
    "에코프로비엠": "2차전지소재", "현대모비스": "자동차부품", "삼성전기": "전자부품",
}

_ci = 0
def next_company(exclude=()):
    global _ci
    for _ in range(len(COMPANIES)):
        c = COMPANIES[_ci % len(COMPANIES)]
        _ci += 1
        if c not in exclude:
            return c
    raise RuntimeError("no company available")


questions = []

def add(category, template, companies, note_companies=None):
    q = template.format(*companies)
    label = "+".join(companies) if len(companies) > 1 else companies[0]
    sector = "+".join(SECTOR.get(c, "?") for c in companies)
    questions.append({"label": f"{label}({sector})", "category": category, "query": q,
                       "companies": companies})


# ── 검색추출_Closed: 20개 ──
CLOSED_EXTRACT_TEMPLATES = [
    "{}의 2025년 매출액은 얼마인가?",
    "{}의 2025년 영업이익은 얼마야?",
    "{}의 최근 자기주식 취득 결정 규모는 얼마야?",
    "{}의 2025년 부채비율은 얼마야?",
    "{}의 최근 체결한 단일판매·공급계약 금액은 얼마야?",
]
for i in range(20):
    t = CLOSED_EXTRACT_TEMPLATES[i % len(CLOSED_EXTRACT_TEMPLATES)]
    c = next_company()
    add("검색추출_Closed", t, [c])

# ── 검색추출_Open: 15개 ──
OPEN_EXTRACT_TEMPLATES = [
    "{}의 주요 사업부문 구성을 설명해줘",
    "{}의 최근 투자 계획을 정리해줘",
    "{}의 주요 매출처를 정리해줘",
]
for i in range(15):
    t = OPEN_EXTRACT_TEMPLATES[i % len(OPEN_EXTRACT_TEMPLATES)]
    c = next_company()
    add("검색추출_Open", t, [c])

# ── 비교연산_Closed: 15개 (같은 업종 위주로 페어링) ──
COMPARE_CLOSED_PAIRS = [
    ("기아", "현대자동차", "{}와 {}의 2025년 매출액을 비교해줘"),
    ("SK하이닉스", "삼성전자", "{}와 {}의 2025년 설비투자 규모를 비교해줘"),
    ("NAVER", "카카오", "{}와 {}의 2025년 영업이익을 비교해줘"),
    ("신한지주", "KB금융", "{}와 {}의 2025년 영업수익을 비교해줘"),
    ("삼성SDI", "LG에너지솔루션", "{}와 {}의 2025년 매출액을 비교해줘"),
    ("삼성바이오로직스", "셀트리온", "{}와 {}의 2025년 매출액을 비교해줘"),
    ("하이브", "와이지엔터테인먼트", "{}와 {}의 2025년 영업이익을 비교해줘"),
    ("두산로보틱스", "레인보우로보틱스", "{}와 {}의 2025년 매출액을 비교해줘"),
    ("한화에어로스페이스", "한국항공우주", "{}와 {}의 2025년 매출액을 비교해줘"),
    ("LG생활건강", "아모레퍼시픽", "{}와 {}의 2025년 영업이익을 비교해줘"),
    ("POSCO홀딩스", "고려아연", "{}와 {}의 2025년 매출액을 비교해줘"),
    ("HMM", "현대글로비스", "{}와 {}의 2025년 매출액을 비교해줘"),
    ("현대건설", "대우건설", "{}와 {}의 2025년 매출액을 비교해줘"),
    ("SK텔레콤", "케이티", "{}와 {}의 2025년 영업이익을 비교해줘"),
    ("현대모비스", "삼성전기", "{}와 {}의 2025년 매출액을 비교해줘"),
]
for a, b, t in COMPARE_CLOSED_PAIRS[:15]:
    if a == b:
        continue
    add("비교연산_Closed", t, [a, b])

# ── 비교연산_Open: 10개 ──
COMPARE_OPEN_TEMPLATES = [
    "{}의 당기 대비 전기 영업이익 변화를 정리해줘",
    "{}의 최근 자금조달 내역을 유형별로 정리해줘",
]
for i in range(10):
    t = COMPARE_OPEN_TEMPLATES[i % len(COMPARE_OPEN_TEMPLATES)]
    c = next_company()
    add("비교연산_Open", t, [c])

# ── 복합추론_Closed: 20개 ──
REASON_CLOSED_TEMPLATES = [
    "{}가 최근 체결한 계약 중 해지되거나 정정된 게 있어?",
    "{}의 최근 기재정정 이력이 있어?",
]
for i in range(20):
    t = REASON_CLOSED_TEMPLATES[i % len(REASON_CLOSED_TEMPLATES)]
    c = next_company()
    add("복합추론_Closed", t, [c])

# ── 복합추론_Open: 20개 ──
REASON_OPEN_TEMPLATES = [
    "{}의 최근 자사주 취득/처분 이력을 정리해줘",
    "{}의 계약 해지 이력을 시간순으로 정리해줘",
    "{}의 최근 공시 이력을 종합해서 정리해줘",
]
for i in range(20):
    t = REASON_OPEN_TEMPLATES[i % len(REASON_OPEN_TEMPLATES)]
    c = next_company()
    add("복합추론_Open", t, [c])

print(f"총 질문 수: {len(questions)}")
from collections import Counter
print(Counter(q["category"] for q in questions))
comp_counter = Counter(c for q in questions for c in q["companies"])
print("회사별 등장 횟수:", dict(sorted(comp_counter.items(), key=lambda x: -x[1])))

json.dump(questions, open("/tmp/hundred_questions.json", "w"), ensure_ascii=False, indent=2)
print("저장 완료: /tmp/hundred_questions.json")
