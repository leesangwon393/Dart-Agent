"""Evidence Pack Builder (§63~64).

최종 HCX 에 검색 결과를 그대로 dump 하지 않고 구조화한다. Citation Provenance
(report_id/chunk_id/section_path/filing_date/correction 상태)를 끝까지 보존해
최종 답변에서 근거를 사용자에게 보여줄 수 있게 한다 (§64)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from disclosure_rag.agent.agent_loop import AgentTrace

_CALC_TOOL_NAMES = {"calculate_growth_rate", "calculate_ratio", "calculate_cagr"}


@dataclass
class Citation:
    chunk_id: str
    report_id: str
    company: str | None
    report_name: str | None
    filing_date: str | None
    section_path: list[str]
    is_correction: bool
    is_latest: bool | None


@dataclass
class EvidencePack:
    question: str
    prompt_text: str  # HCX 에 그대로 넘길 최종 텍스트
    citations: list[Citation] = field(default_factory=list)
    tool_results_summary: list[dict] = field(default_factory=list)  # 계산류 등, 검증용


def build_evidence_pack(trace: AgentTrace) -> EvidencePack:
    lines = [f"[USER QUESTION]\n{trace.question}\n"]
    citations: list[Citation] = []
    tool_results_summary: list[dict] = []
    evidence_idx = 1

    for tc in trace.tool_calls:
        if tc.name == "search_disclosures":
            for item in tc.result.get("results", []):
                status = "정정본" if item.get("is_correction") else "원본"
                if item.get("is_latest"):
                    status += " (최신)"
                lines.append(
                    f"[EVIDENCE {evidence_idx}]\n"
                    f"회사: {item.get('company')}\n"
                    f"공시명: {item.get('report_name')}\n"
                    f"공시일: {item.get('filing_date')}\n"
                    f"기간: {item.get('period')}\n"
                    f"Section: {' > '.join(item.get('section_path') or [])}\n"
                    f"정정 상태: {status}\n"
                    f"내용:\n{item.get('text')}\n"
                    f"report_id: {item.get('report_id')}\n"
                    f"chunk_id: {item.get('chunk_id')}\n"
                )
                citations.append(Citation(
                    chunk_id=item.get("chunk_id"), report_id=item.get("report_id"),
                    company=item.get("company"), report_name=item.get("report_name"),
                    filing_date=item.get("filing_date"), section_path=item.get("section_path") or [],
                    is_correction=bool(item.get("is_correction")), is_latest=item.get("is_latest"),
                ))
                evidence_idx += 1
        elif tc.name in _CALC_TOOL_NAMES:
            lines.append(f"[TOOL RESULT]\n계산 종류: {tc.name}\n입력 값: {tc.arguments}\n결과: {tc.result}\n")
            tool_results_summary.append({"tool": tc.name, "arguments": tc.arguments, "result": tc.result})
        else:
            lines.append(f"[TOOL RESULT]\n{tc.name}: {json.dumps(tc.result, ensure_ascii=False)}\n")
            tool_results_summary.append({"tool": tc.name, "arguments": tc.arguments, "result": tc.result})

    if not citations and not tool_results_summary:
        lines.append("[EVIDENCE 없음 — 검색/도구 호출로 근거를 확보하지 못했습니다]")

    return EvidencePack(
        question=trace.question, prompt_text="\n".join(lines),
        citations=citations, tool_results_summary=tool_results_summary,
    )
