"""Answer Validation (§67).

처음부터 완벽할 필요는 없지만 interface 는 분리한다 (§67 마지막 줄). 여기서는
"근거에 없는 숫자를 답변이 지어냈는가", "근거 인용이 있는가", "정정 분석 질문인데
원본/정정본이 둘 다 확보됐는가" 를 체크하는 실용적인 baseline 을 둔다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from disclosure_rag.agent.evidence import EvidencePack
from disclosure_rag.entity.entity_extractor import ExtractedEntities

_NUMBER_PAT = re.compile(r"\d[\d,]*\.?\d*")
_APPROX_PAREN_PAT = re.compile(r"\(약[^)]*\)")
_DOC_ID_PAT = re.compile(r"\b(?:periodic|major|exchange|holding)_\d{10,}\b")


def _extract_numbers(text: str, *, min_digits: int = 3) -> set[str]:
    """콤마/소수점을 제거한 뒤, 우연한 오검출(1~2자리 숫자, 연도 등)을 줄이기 위해
    min_digits 자리 이상만 취급한다.

    회귀 발견(2026-08-16, 회사 일반화 스모크테스트): "7,661,584백만원 (약 7조
    6,615억원)"처럼 답변이 같은 숫자를 조/억 단위로 다시 풀어 쓰면, 괄호 안의
    "6615"가 evidence 원문 문자열과 글자 그대로 일치하지 않아 "근거 없는 숫자"로
    오탐됐다(실제로는 같은 숫자의 재표기일 뿐 새로운 주장이 아님). "(약 ...)"
    괄호는 근사 재표기라는 걸 답변 스스로 명시한 것이므로 grounding 검사에서
    제외한다."""
    text = _APPROX_PAREN_PAT.sub(" ", text)
    out = set()
    for m in _NUMBER_PAT.finditer(text):
        norm = m.group(0).replace(",", "")
        digits_only = norm.replace(".", "")
        if len(digits_only) >= min_digits:
            out.add(norm)
    return out


@dataclass
class ValidationResult:
    numbers_grounded: bool
    ungrounded_numbers: set[str]
    has_citation: bool
    correction_evidence_complete: bool | None  # None = 해당 없음(정정 질문 아님)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.numbers_grounded and self.has_citation and (self.correction_evidence_complete is not False)


def validate_answer(answer: str, evidence_pack: EvidencePack, entities: ExtractedEntities) -> ValidationResult:
    warnings: list[str] = []

    evidence_numbers: set[str] = _extract_numbers(evidence_pack.prompt_text)
    for tr in evidence_pack.tool_results_summary:
        evidence_numbers |= _extract_numbers(str(tr))

    answer_numbers = _extract_numbers(answer)
    ungrounded = answer_numbers - evidence_numbers
    numbers_grounded = not ungrounded
    if ungrounded:
        warnings.append(f"[근거 없는 숫자 의심] 답변에 있지만 Evidence/Tool Result 에서 찾을 수 없는 숫자: {sorted(ungrounded)}")

    # 회귀 발견(2026-08-16): get_correction_history/get_latest_report 만 호출된
    # 답변(search_disclosures 를 안 써서 evidence_pack.citations 가 비어있는
    # 경우)은, 답변이 근거를 정확히 인용했어도 evidence_pack.citations 가
    # 비어있다는 이유만으로 무조건 has_citation=False 로 잡혔다. tool_results_
    # summary(예: get_correction_history 결과)에 등장한 report_id 를 답변이
    # 그대로 인용했는지도 함께 확인한다.
    tool_result_doc_ids = {
        m.group(0) for tr in evidence_pack.tool_results_summary for m in _DOC_ID_PAT.finditer(str(tr))
    }
    has_any_evidence = bool(evidence_pack.citations) or bool(tool_result_doc_ids)
    has_citation = has_any_evidence and (
        any(c.report_id in answer or c.chunk_id in answer for c in evidence_pack.citations)
        or any(doc_id in answer for doc_id in tool_result_doc_ids)
        or "근거" in answer
    )
    if has_any_evidence and not has_citation:
        warnings.append("답변에 근거(report_id/chunk_id) 인용이 없음")

    correction_evidence_complete: bool | None = None
    if entities.explicit_correction:
        has_correction = any(c.is_correction for c in evidence_pack.citations)
        has_original = any(not c.is_correction for c in evidence_pack.citations)
        correction_evidence_complete = has_correction and has_original
        if not correction_evidence_complete:
            warnings.append("정정 분석 질문인데 원본/정정본 근거가 모두 확보되지 않음 (정정 체인 재검색 필요할 수 있음)")

    return ValidationResult(
        numbers_grounded=numbers_grounded, ungrounded_numbers=ungrounded,
        has_citation=has_citation, correction_evidence_complete=correction_evidence_complete,
        warnings=warnings,
    )
