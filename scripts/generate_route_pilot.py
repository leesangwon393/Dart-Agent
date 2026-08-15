"""Router 파인튜닝용 파일럿 데이터 생성 (카테고리당 25개, 총 150개).

- HCX 로 "질문 문장"만 생성시킨다(숫자/사실을 지어내는 게 아니라 자연어 질문
  형태만 만드는 것이라 환각 리스크가 낮음).
- production 이 route 분류기에 넣는 입력은 normalize_query() 를 거친
  [COMPANY]/[COMPANY_1]/[COMPANY_2]/[YEAR] placeholder 형태이므로, 학습
  데이터도 실제 회사명이 아니라 이 placeholder 형태를 유지한다(입력 분포 일치).
- ROUTE_UTTERANCES 예시를 참고자료로만 주고, "이거 그대로 바꿔쓰지 말고
  표현/어투/구조를 다양하게" 명시적으로 지시한다.
- 오늘 실제로 찾은 취약점(카운팅형 질문의 route 모호성)을 의도적으로 일부
  포함시키도록 지시한다 — 이런 edge case 가 자연 생성으로는 잘 안 나옴.
"""
import json, os, re, time
from dotenv import load_dotenv

load_dotenv(".env")
from disclosure_rag.agent.hcx_client import HCXClient
from disclosure_rag.router.routes import ROUTE_UTTERANCES

client = HCXClient(env_path=".env")  # 기본 .env 모델(HCX-007) 사용 — 생성 품질 우선

# results/router_tuning/rubric.md 와 동기화 — 여기서 설명을 바꾸면 그쪽도 갱신할 것.
# 확정된 경계 규칙(사용자 결정, 2026-08-16):
#  1) "기간별 비율/지표 비교"(예: "3개년 부채비율 비교해줘") -> multi_compare (calculation 아님)
#  2) "두 회사의 정정이력 비교" -> correction_analysis (multi_compare 아님)
CATEGORY_DESC = {
    "single_lookup": "특정 회사의 단일 사실/수치를 묻는 질문(매출액, 대표이사, 상장일 등 — 비교·계산·지분·이벤트가 아닌 순수 사실 하나 조회). 최대주주가 누구인지 묻는 질문은 여기 아니라 ownership_analysis.",
    "correction_analysis": "정정공시(기재정정)의 사유/변경내용/전후비교/이력을 묻는 질문. 두 회사 간 정정이력을 비교하는 질문도 여기 포함(multi_compare 아님).",
    "multi_compare": "두 회사 간 비교, 또는 한 회사의 여러 기간(연도/분기) 간 비교를 묻는 질문. 여러 기간의 비율/지표를 나란히 비교하는 질문도 여기 포함(calculation 아님) — 핵심은 '비교' 의도.",
    "calculation": "증가율/CAGR/비율/증감액 등 단일 시점 또는 단일 기간 쌍에 대한 산술 계산 요청. 여러 기간을 나열/비교하는 질문은 multi_compare로.",
    "ownership_analysis": "최대주주/지분율/주요주주/지분변동/자기주식 등 지분 관련 질문. '최대주주가 누구야?'처럼 단순해 보여도 최대주주/지분 관련이면 여기.",
    "event_analysis": "유상증자/계약체결/인수합병/시설투자/자사주 취득 등 특정 이벤트(공시) 발생 여부/내용을 묻는 질문. 개수를 세는 질문('몇 건이야?')도 여기 포함.",
}

EDGE_CASE_HINT = {
    "event_analysis": "그 중 2~3개는 '몇 건이야?/몇 번 있었어?' 처럼 개수를 세는 형태로 만들어라(이런 카운팅형 질문의 route가 실제로 헷갈리는 경우가 있어서 일부러 포함시킨다).",
    "calculation": "'몇 % 늘었어?' 같은 명확한 계산 요청 외에, 계산이 필요 없는데 착각하기 쉬운 경계 케이스는 넣지 마라(순수 계산 질문만).",
}

N_PER_CATEGORY = 25

PROMPT_TMPL = """당신은 금융공시 QA 시스템의 route 분류기 학습용 질문 데이터를 만드는 중입니다.

[카테고리] {route}
[설명] {desc}
[참고 예시 — 그대로 베끼지 말고 표현/어투/문장구조만 참고할 것]
{examples}

[요청]
위 카테고리에 해당하는 자연어 질문을 서로 다른 표현으로 {n}개 만들어라.
- 회사명은 실제 이름 대신 반드시 [COMPANY](한 회사) 또는 [COMPANY_1]/[COMPANY_2](두 회사 비교 시)로 써라.
- 연도가 필요하면 [YEAR] 또는 [YEAR_1]/[YEAR_2] 로 써라.
- 어투를 섞어라: 반말/존댓말, 직접의문문/명령문("~알려줘"/"~해줘"), 짧은 질문/긴 질문.
- 예시 문장을 그대로 재사용하지 마라(단어만 바꾸는 것도 금지).
{edge_hint}
- 숫자나 실제 회사 이름 등 사실(fact)은 절대 넣지 마라 — 오직 질문 형태만.
- 한 줄에 질문 하나씩, 번호나 불릿 없이 순수 텍스트로만 출력해라.
"""


def parse_lines(text: str) -> list[str]:
    lines = []
    for line in text.strip().split("\n"):
        line = line.strip()
        line = re.sub(r"^[\d]+[\.\)]\s*", "", line)  # 번호 매김 제거
        line = re.sub(r"^[-*•]\s*", "", line)  # 불릿 제거
        if line:
            lines.append(line)
    return lines


results: dict[str, list[str]] = {}
for route, examples in ROUTE_UTTERANCES.items():
    desc = CATEGORY_DESC[route]
    edge_hint = EDGE_CASE_HINT.get(route, "")
    example_str = "\n".join(f"- {e}" for e in examples[:6])
    prompt = PROMPT_TMPL.format(route=route, desc=desc, examples=example_str, n=N_PER_CATEGORY, edge_hint=edge_hint)

    print(f"\n=== generating: {route} ===", flush=True)
    text = client.chat_simple(prompt, max_tokens=1500, temperature=0.9)
    lines = parse_lines(text)
    print(f"  생성됨: {len(lines)}개", flush=True)
    for l in lines[:5]:
        print("   ", l, flush=True)
    results[route] = lines
    time.sleep(2)

json.dump(results, open("/tmp/route_pilot_generated.json", "w"), ensure_ascii=False, indent=2)
total = sum(len(v) for v in results.values())
print(f"\nDONE. 총 {total}개 -> /tmp/route_pilot_generated.json", flush=True)
