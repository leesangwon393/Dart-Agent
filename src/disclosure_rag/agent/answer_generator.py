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
5. 답변 마지막 줄에 "근거: report_id(chunk_id), ..." 형식으로 실제로 사용한
   근거를 나열하세요."""


def generate_answer(client: HCXClient, evidence_pack: EvidencePack, *, max_tokens: int = 800) -> str:
    if not evidence_pack.citations and not evidence_pack.tool_results_summary:
        return "제공된 근거로는 확인할 수 없습니다. (검색된 공시 근거가 없습니다.)"

    messages = [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {"role": "user", "content": evidence_pack.prompt_text},
    ]
    result = client.chat(messages, max_tokens=max_tokens, temperature=0.2)
    return result.get("content", "")
