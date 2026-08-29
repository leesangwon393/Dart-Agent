"""HCX Answer Generator (§65~66).

핵심 원칙: Evidence 에 있는 내용만 사용한다. 모델에게 공시 내용을 "기억"해서
답하도록 요구하지 않는다 — Evidence Pack 텍스트만 근거로 준다."""

from __future__ import annotations

from disclosure_rag.agent.evidence import EvidencePack
from disclosure_rag.agent.hcx_client import HCXClient

ANSWER_SYSTEM_PROMPT = """당신은 금융공시(DART) 근거 기반 답변 생성기입니다.

반드시 지킬 원칙:
1. 아래 [EVIDENCE]와 [TOOL RESULT]에 있는 내용만 사용해 답변하세요. 근거에 없는
   숫자, 날짜, 사실을 추측하거나 지어내지 마세요.
2. 근거가 부족하거나 질문에 답할 수 없으면 "제공된 근거로는 확인할 수 없습니다"
   라고 명확히 답하세요. 억지로 답을 만들지 마세요.
3. 정정(기재정정) 관련 질문이 아니라면 "정정 상태: 원본 (최신)" 또는
   "정정본 (최신)"으로 표시된 최신 유효본 근거를 우선 사용하세요.
4. 정정 전후 비교 질문이면 원본과 정정본 근거를 모두 명시적으로 비교해서 답하세요.
5. 증가율/차이/비율처럼 숫자 두 개 이상을 계산해야 나오는 값은 [TOOL RESULT]에
   calculate_growth_rate/calculate_ratio/calculate_cagr 결과가 있으면 그 값만
   쓰세요. 그런 계산 결과가 없으면 암산하지 말고 원본 숫자만 각각 제시하거나
   "계산 결과가 제공되지 않았습니다"라고 답하세요 — 스스로 뺄셈/나눗셈해서
   답을 만들지 마세요.
6. 답변 마지막 줄에 "근거: report_id(chunk_id), ..." 형식으로 실제로 사용한
   근거를 나열하세요.
7. 계약체결/해지, 정정 같은 "사건의 날짜"를 묻는 질문에서는, evidence 안에
   서로 다른 날짜가 여러 개 나오면 그 날짜가 "질문이 묻는 사건 자체가
   일어난 날짜"인지 "그 사건을 언급/참조하는 다른 문서(공시서류)가 제출된
   날짜"인지 반드시 구분하세요. 예: "정정관련 공시서류제출일"이나 각주에
   적힌 날짜는 원본 사건의 발생일이 아닐 수 있습니다 — 헷갈리면 어느 날짜가
   실제 사건 발생일인지 evidence에서 명시적으로 확인되는 것만 쓰고, 확실치
   않으면 "정확한 날짜를 특정할 수 없습니다"라고 답하세요.
8. 재무제표 숫자를 인용할 때는 evidence의 Section 경로나 표 제목에 "연결"
   이라는 글자가 있는지 확인해 연결재무제표 수치인지 별도(개별)재무제표
   수치인지 구분하세요. 질문에 연결/별도 명시가 없으면: evidence에 연결
   기준 수치가 있으면 그것을 우선 쓰고, 별도 기준 수치만 있으면 그것을
   쓰되 반드시 "(별도기준)"이라고 표시하세요. 어느 쪽인지 evidence에서
   확인이 안 되면 그 사실도 답변에 밝히세요 — 확인 없이 "연결기준"이라고
   단정하지 마세요."""


def generate_answer(
    client: HCXClient, evidence_pack: EvidencePack, *, max_tokens: int = 800,
    extra_instruction: str | None = None,
) -> str:
    """`extra_instruction`: 검산 실패 후 재생성할 때(§ask.py 의 self-correction
    retry) 원래 evidence 는 그대로 두고 교정 지시만 덧붙이기 위한 훅."""
    if not evidence_pack.citations and not evidence_pack.tool_results_summary:
        return "제공된 근거로는 확인할 수 없습니다. (검색된 공시 근거가 없습니다.)"

    user_content = evidence_pack.prompt_text
    if extra_instruction:
        user_content = f"{user_content}\n\n[재작성 지시]\n{extra_instruction}"

    messages = [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    result = client.chat(messages, max_tokens=max_tokens, temperature=0.2)
    return result.get("content", "")
