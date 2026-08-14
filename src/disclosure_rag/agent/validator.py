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


def _extract_numbers(text: str, *, min_digits: int = 3) -> set[str]:
    """콤마/소수점을 제거한 뒤, 우연한 오검출(1~2자리 숫자, 연도 등)을 줄이기 위해
    min_digits 자리 이상만 취급한다."""
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

    has_citation = bool(evidence_pack.citations) and (
        "근거" in answer or any(c.report_id in answer or c.chunk_id in answer for c in evidence_pack.citations)
    )
    if evidence_pack.citations and not has_citation:
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
