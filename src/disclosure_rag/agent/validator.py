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


_MAX_VERIFY_NUMBERS = 200  # 안전장치: evidence 숫자가 너무 많으면 O(n^2) 검산을 생략


def _verify_derived_number(claimed: str, evidence_numbers: set[str]) -> str | None:
    """`claimed` 가 evidence 안의 서로 다른 두 숫자로 정확히 설명되는
    사칙연산/비율 결과인지 검산한다. 설명되면 그 식을 문자열로 반환하고
    (grounded 로 인정), 아니면 None(여전히 근거 없음).

    2026-08-18 도입 배경: 답변 모델(HCX-005, 계산 tool 을 안 쓰는 자유
    텍스트 생성)이 evidence 안의 두 숫자로 직접 뺄셈/비율을 암산해서
    답변에 적는 경우가 실측으로 확인됐다(예: "영업이익 2,164,043백만원 vs
    1,795,249백만원, 368,794백만원 증가" — 368,794 는 그 자체로 evidence에
    없지만 두 숫자의 차이와 정확히 일치했다). 기존 로직은 이런 "암산이지만
    맞는 계산"과 "완전히 틀린/지어낸 숫자"를 똑같이 "근거 없음"으로만
    표시해서 구분이 안 됐다(알테오젠 10배 오류 사례와 동일한 경고로 보임).
    여기서 실제로 검산해서, 맞으면 통과시키고 틀리면(=어떤 조합으로도
    설명 안 되면) 그대로 의심스러운 숫자로 남긴다 — "LLM 암산을 신뢰하지
    않고 직접 확인한다"는 원칙(calculation.py 최상단 docstring)을 답변
    생성 이후 단계에서도 관철한다."""
    try:
        target = float(claimed)
    except ValueError:
        return None

    parsed: list[tuple[str, float]] = []
    for n in evidence_numbers:
        try:
            parsed.append((n, float(n)))
        except ValueError:
            continue
    if len(parsed) > _MAX_VERIFY_NUMBERS:
        return None  # 성능 안전장치 — 이 경우는 검산 없이 기존 방식(근거없음 의심)으로 처리

    tol = max(abs(target) * 0.005, 0.5)  # 반올림 오차 허용: 상대 0.5% 또는 절대 0.5
    for s1, v1 in parsed:
        for s2, v2 in parsed:
            if s1 == s2:
                continue
            candidates = {f"{s1} - {s2}": v1 - v2, f"{s1} + {s2}": v1 + v2}
            if v2 != 0:
                candidates[f"{s1} / {s2} * 100(%)"] = v1 / v2 * 100
            for expr, val in candidates.items():
                if abs(val - target) <= tol:
                    return expr
    return None


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
        # 회귀 발견(2026-08-19, 100문항 배치): "[기재정정]사업보고서 (2023.12)"처럼
        # 날짜가 "YYYY.MM" 형태로 붙어 있으면 _NUMBER_PAT이 "2023.12"를 하나의
        # 토큰으로 통째로 묶는다. 그런데 답변이 같은 연도를 "2023년 3월"처럼 점
        # 없이 따로 쓰면 "2023"이 evidence_numbers 안에 별도 항목으로 없어서
        # "근거 없는 숫자"로 오탐됐다(correction_analysis 라우트 10건, single_lookup
        # 2건 실측). 실제로는 evidence 원문에 "2023"이 "2023.12"의 앞부분으로
        # 문자 그대로 존재하므로, 소수점으로 이어붙은 토큰은 "."로 쪼갠 부분도
        # 함께 등록해 grounding 판정에서 놓치지 않게 한다.
        if "." in norm:
            for part in norm.split("."):
                if len(part) >= min_digits:
                    out.add(part)
    return out


@dataclass
class ValidationResult:
    numbers_grounded: bool
    ungrounded_numbers: set[str]
    has_citation: bool
    correction_evidence_complete: bool | None  # None = 해당 없음(정정 질문 아님)
    warnings: list[str] = field(default_factory=list)
    # {답변에 적힌 숫자: 그 숫자를 설명하는 evidence 사칙연산 식} — 답변 모델이
    # calculate_* tool 을 안 쓰고 evidence 숫자를 직접 조합해 계산했지만 검산
    # 결과 맞았던 경우. numbers_grounded=True 로 인정되긴 하지만, "LLM이 tool
    # 없이 암산했다"는 신호이므로 별도로 노출한다(§2026-08-18 처리 방식 참고).
    verified_derived_numbers: dict[str, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.numbers_grounded and self.has_citation and (self.correction_evidence_complete is not False)


def validate_answer(answer: str, evidence_pack: EvidencePack, entities: ExtractedEntities) -> ValidationResult:
    warnings: list[str] = []

    evidence_numbers: set[str] = _extract_numbers(evidence_pack.prompt_text)
    for tr in evidence_pack.tool_results_summary:
        evidence_numbers |= _extract_numbers(str(tr))

    answer_numbers = _extract_numbers(answer)
    not_literally_present = answer_numbers - evidence_numbers

    ungrounded: set[str] = set()
    verified_derived: dict[str, str] = {}
    for n in not_literally_present:
        expr = _verify_derived_number(n, evidence_numbers)
        if expr is not None:
            verified_derived[n] = expr
        else:
            ungrounded.add(n)

    numbers_grounded = not ungrounded
    if ungrounded:
        warnings.append(f"[근거 없는 숫자 의심] 답변에 있지만 Evidence/Tool Result 로 검산도 안 되는 숫자: {sorted(ungrounded)}")
    if verified_derived:
        warnings.append(f"[참고] 답변이 evidence 수치를 직접 조합해 계산한 값(검산 통과): {verified_derived}")

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
        warnings=warnings, verified_derived_numbers=verified_derived,
    )
