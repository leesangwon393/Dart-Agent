"""Agent Tool 정의 (§58~62).

각 Tool 의 역할은 deterministic 하고 명확해야 한다. 이름을 스펙 그대로 맞출
필요는 없지만(§58), 여기서는 스펙 이름을 그대로 따른다 — 이해하기 쉬우라고.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from disclosure_rag.agent.calculation import calculate_cagr, calculate_growth_rate, calculate_ratio
from disclosure_rag.chunking.chunk_schema import ChunkSchema
from disclosure_rag.common.manifest_loader import ManifestRow
from disclosure_rag.common.unicode_utils import normalize_nfc
from disclosure_rag.correction.correction_graph_builder import CorrectionRecord
from disclosure_rag.retrieval.metadata_filter import RetrievalFilter


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON schema (HCX tools[].function.parameters)
    handler: Callable[..., dict]

    def to_hcx_schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def _evidence_dict(chunk: ChunkSchema, score: float | None = None) -> dict:
    d = {
        "chunk_id": chunk.chunk_id,
        "report_id": chunk.report_id,
        "company": chunk.company,
        "report_type": chunk.report_type,
        "report_name": chunk.report_name,
        "period": chunk.period,
        "filing_date": chunk.filing_date,
        "section_path": chunk.section_path,
        "content_type": chunk.content_type,
        "is_correction": chunk.is_correction,
        "correction_group_id": chunk.correction_group_id,
        "is_latest": chunk.is_latest,
        "text": chunk.raw_text,
    }
    if score is not None:
        d["score"] = round(float(score), 4)
    return d


_METRIC_TERM_RE = re.compile(r"[가-힣A-Za-z0-9]+")


def _extract_query_terms(query: str) -> set[str]:
    """LLM 을 semantic boundary/매칭 판단에 쓰지 않는다(§12 원칙 9) — 아주 단순한
    부분문자열 키워드 집합만 뽑는다. metric_hint sibling 우선순위 결정에만 쓰인다."""
    return {w for w in _METRIC_TERM_RE.findall(query) if len(w) >= 2}


def _table_sibling_index(retriever) -> dict[str, list[ChunkSchema]]:
    """table_id -> 같은 표에서 나온 chunk 목록(표 안 순서대로 정렬). 첫 호출 때만
    전체 chunk 를 훑어 만들고, retriever 인스턴스에 캐시해 재사용한다(§12 원칙:
    BM25/Dense/Fusion scoring 로직 자체는 건드리지 않는다 — 이건 그 바깥, evidence
    후보 구성 단계에서만 쓰는 별도 인덱스)."""
    cached = getattr(retriever, "_table_sibling_index_cache", None)
    if cached is not None:
        return cached
    chunks_by_id = getattr(retriever, "chunks_by_id", None)
    if chunks_by_id is None:
        chunks_by_id = getattr(getattr(retriever, "bm25", None), "chunks_by_id", None) or {}
    index: dict[str, list[ChunkSchema]] = {}
    for c in chunks_by_id.values():
        if c.table_id:
            index.setdefault(c.table_id, []).append(c)
    for group in index.values():
        group.sort(key=lambda c: (c.table_chunk_index or 0))
    try:
        retriever._table_sibling_index_cache = index
    except Exception:  # noqa: BLE001 — 캐시 실패해도 기능엔 지장 없음(매번 재계산)
        pass
    return index


def _expand_table_siblings(
    retriever, results: list[tuple[ChunkSchema, float]], query: str, max_table_sibling_expansion: int,
) -> list[tuple[ChunkSchema, float | None]]:
    """검색된 chunk 가 table_id(Phase 4)를 가지면, 같은 table_id 의 다른 chunk(형제)
    를 evidence 후보에 추가한다 (Phase 5, "매출액/영업이익이 서로 다른 chunk 로
    갈라져 하나만 검색됐을 때" 대비).

    우선순위: 직접 검색된 chunk > metric_hint 가 query 와 겹치는 sibling >
    prev/next(표 안에서 가까운) sibling. score boost 는 하지 않는다 — BM25/Dense/
    Fusion 스코어링 로직은 이번 작업 범위 밖이라 건드리지 않는다(§TODO, PROJECT_STATE
    §12 후보로 기록). 여기서 추가되는 sibling 은 score=None 으로 표시한다.
    """
    if max_table_sibling_expansion <= 0:
        return results
    index = _table_sibling_index(retriever)
    if not index:
        return results

    query_terms = _extract_query_terms(query)
    seen = {c.chunk_id for c, _ in results}
    expanded: list[tuple[ChunkSchema, float | None]] = list(results)

    for chunk, _score in list(results):
        if not chunk.table_id or chunk.table_id not in index:
            continue
        group = index[chunk.table_id]
        positions = {c.chunk_id: i for i, c in enumerate(group)}
        pos = positions.get(chunk.chunk_id)
        if pos is None:
            continue

        candidates = []
        for other in group:
            if other.chunk_id == chunk.chunk_id or other.chunk_id in seen:
                continue
            metric_match = bool(query_terms) and any(
                term in hint for term in query_terms for hint in other.metric_hints
            )
            distance = abs(positions[other.chunk_id] - pos)
            # metric_match 인 sibling 을 최우선(0), 그 다음 표 안 거리순으로 정렬.
            candidates.append((0 if metric_match else 1, distance, other))
        candidates.sort(key=lambda t: (t[0], t[1]))

        for _prio, _dist, other in candidates[:max_table_sibling_expansion]:
            expanded.append((other, None))
            seen.add(other.chunk_id)

    return expanded


def make_search_disclosures_tool(
    retriever, *, default_k: int = 5,
    expand_table_siblings: bool = True, max_table_sibling_expansion: int = 1,
) -> ToolDef:
    def handler(query: str, company: str | None = None, report_type: str | None = None,
                period: str | None = None, report_id: str | None = None,
                top_k: int = default_k, latest_only: bool = True) -> dict:
        if report_id:
            # 특정 문서(예: get_correction_history 로 알아낸 원본/정정본의 report_id)
            # 의 실제 본문을 그대로 가져온다 — latest_only 무관하게 그 문서 자체를 본다.
            flt = RetrievalFilter(report_ids=[report_id])
            results = retriever.search(query, k=max(top_k, 10), flt=flt)
            if expand_table_siblings:
                results = _expand_table_siblings(retriever, results, query, max_table_sibling_expansion)
            return {"results": [_evidence_dict(c, s) for c, s in results]}

        # Coarse-to-Fine (§51): 정확한 filter 로 먼저 시도하되, 0건이면 필터를
        # 하나씩 완화하며 재시도한다. HCX 가 period 포맷(우리는 "YYYY-MM"/"YYYY"만
        # 지원)을 잘못 추측해서 보내는 경우가 실측으로 흔했다 — 그때 결과가 통째로
        # 0건이 되어 버리는 것을 막는다(§7 silent 0건 방지 원칙을 tool 레벨에도 적용).
        attempts = [
            dict(companies=[company] if company else None, doc_groups=[report_type] if report_type else None,
                 periods=[period] if period else None, latest_only=latest_only),
        ]
        if period:
            attempts.append(dict(companies=attempts[0]["companies"], doc_groups=attempts[0]["doc_groups"],
                                  periods=None, latest_only=latest_only))
        if latest_only:
            attempts.append(dict(companies=attempts[0]["companies"], doc_groups=attempts[0]["doc_groups"],
                                  periods=None, latest_only=False))
        if report_type:
            attempts.append(dict(companies=attempts[0]["companies"], doc_groups=None,
                                  periods=None, latest_only=False))

        for kwargs in attempts:
            flt = RetrievalFilter(**kwargs)
            results = retriever.search(query, k=top_k, flt=flt)
            if results:
                if expand_table_siblings:
                    results = _expand_table_siblings(retriever, results, query, max_table_sibling_expansion)
                return {"results": [_evidence_dict(c, s) for c, s in results], "note": None if kwargs == attempts[0] else "일부 필터를 완화해 재검색함(원 필터로는 0건)"}
        return {"results": [], "note": "필터를 단계적으로 완화했지만 관련 근거를 찾지 못함"}

    return ToolDef(
        name="search_disclosures",
        description=(
            "공시 본문에서 질문과 관련된 근거를 검색한다. 처음에는 report_type/period 를 "
            "비워두고 query(+company)만으로 넓게 검색하는 것을 권장한다 — 결과가 관련없거나 "
            "너무 많을 때만 report_type/period 로 좁혀서 재검색하라. 필터가 결과를 0건으로 "
            "만들면 이 tool 이 자동으로 필터를 완화해 재시도한다. get_correction_history 나 "
            "get_latest_report 로 특정 문서의 report_id(doc_id) 를 이미 알고 있다면, "
            "그 문서의 실제 본문 내용을 보기 위해 report_id 파라미터로 바로 조회하라 "
            "(예: 정정 전후 내용을 비교하려면 원본/정정본 각각의 report_id 로 이 tool 을 호출)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어 (한국어)"},
                "company": {"type": "string", "description": "회사명 (DART 정식 법인명)"},
                "report_type": {"type": "string", "enum": ["periodic", "major", "exchange", "holding"]},
                "period": {"type": "string", "description": "반드시 'YYYY-MM' 또는 'YYYY' 형식만 사용 (예: '2024-12', '2024'). 날짜 범위나 다른 포맷은 지원하지 않으므로 확실하지 않으면 비워두라."},
                "report_id": {"type": "string", "description": "특정 문서의 report_id(doc_id) — 알고 있으면 이걸로 그 문서 본문을 직접 가져온다. 이 경우 company/report_type/period/latest_only 는 무시된다."},
                "top_k": {"type": "integer", "description": "검색 결과 개수 (기본 5)"},
                "latest_only": {"type": "boolean", "description": "최신 유효본만 검색할지 여부 (기본 true, 정정 분석 질문이면 false)"},
            },
            "required": ["query"],
        },
        handler=handler,
    )


_TITLE_NOISE_CHARS = str.maketrans("", "", "ㆍ· /[]()")


def _title_match(needle: str, *haystacks: str | None) -> bool:
    """report_nm 원문에는 'ㆍ' 같은 구분자가 섞여 있어(예: '단일판매ㆍ공급계약체결')
    Agent 가 자연스럽게 쓰는 doc_subtype 스타일 문자열('단일판매공급계약체결')과
    글자 그대로는 substring 매칭이 안 된다 — 실측으로 확인된 실패 케이스. 구분자를
    제거하고 비교한다. doc_subtype 도 같이 후보로 넣어 이중으로 매칭한다."""
    needle_norm = needle.translate(_TITLE_NOISE_CHARS).replace("[기재정정]", "")
    for h in haystacks:
        if h and needle_norm in h.translate(_TITLE_NOISE_CHARS).replace("[기재정정]", ""):
            return True
    return False


def make_get_correction_history_tool(manifest: list[ManifestRow], correction_index: dict[str, CorrectionRecord]) -> ToolDef:
    def handler(company: str, report_name_contains: str | None = None) -> dict:
        company_nfc = normalize_nfc(company)
        candidates = [r for r in manifest if r.corp_name == company_nfc]
        if report_name_contains:
            candidates = [r for r in candidates if _title_match(report_name_contains, r.report_nm, r.doc_subtype)]

        groups: dict[str, list[ManifestRow]] = {}
        for r in candidates:
            rec = correction_index.get(r.doc_id)
            if rec is None:
                continue
            groups.setdefault(rec.correction_group_id, []).append(r)

        out = []
        for group_id, rows in groups.items():
            rows_sorted = sorted(rows, key=lambda r: correction_index[r.doc_id].correction_order)
            out.append({
                "correction_group_id": group_id,
                "chain": [
                    {
                        "doc_id": r.doc_id, "report_nm": r.report_nm, "rcept_dt": r.rcept_dt,
                        "is_correction": r.is_correction,
                        "correction_order": correction_index[r.doc_id].correction_order,
                        "is_latest": correction_index[r.doc_id].is_latest,
                        "resolution_source": correction_index[r.doc_id].resolution_source,
                    }
                    for r in rows_sorted
                ],
            })
        return {"correction_groups": out}

    return ToolDef(
        name="get_correction_history",
        description="특정 회사의 특정 공시가 정정된 이력(원본->정정1->정정2->... 순서, 최신본 여부)을 조회한다.",
        parameters={
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "report_name_contains": {"type": "string", "description": "공시명에 포함될 키워드 (예: '사업보고서')"},
            },
            "required": ["company"],
        },
        handler=handler,
    )


def make_get_latest_report_tool(manifest: list[ManifestRow], correction_index: dict[str, CorrectionRecord]) -> ToolDef:
    def handler(company: str, report_type: str, report_name_contains: str | None = None) -> dict:
        company_nfc = normalize_nfc(company)
        candidates = [r for r in manifest if r.corp_name == company_nfc and r.doc_group == report_type]
        if report_name_contains:
            candidates = [r for r in candidates if _title_match(report_name_contains, r.report_nm, r.doc_subtype)]
        latest_rows = [r for r in candidates if correction_index.get(r.doc_id) and correction_index[r.doc_id].is_latest]
        latest_rows.sort(key=lambda r: r.rcept_dt, reverse=True)
        if not latest_rows:
            return {"found": False}
        r = latest_rows[0]
        return {
            "found": True, "doc_id": r.doc_id, "report_nm": r.report_nm,
            "rcept_dt": r.rcept_dt, "is_correction": r.is_correction,
        }

    return ToolDef(
        name="get_latest_report",
        description="특정 회사/공시유형의 가장 최근 '최신 유효본' 공시를 찾는다 (정정 체인 반영).",
        parameters={
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "report_type": {"type": "string", "enum": ["periodic", "major", "exchange", "holding"]},
                "report_name_contains": {"type": "string"},
            },
            "required": ["company", "report_type"],
        },
        handler=handler,
    )


def _calc_tool(name: str, description: str, fn: Callable[..., dict], properties: dict, required: list[str]) -> ToolDef:
    return ToolDef(
        name=name, description=description,
        parameters={"type": "object", "properties": properties, "required": required},
        handler=fn,
    )


def build_calculation_tools() -> list[ToolDef]:
    return [
        _calc_tool(
            "calculate_growth_rate", "증가율/감소율/증감액을 계산한다 (LLM 암산 대신 정확한 계산).",
            calculate_growth_rate,
            {"before": {"type": "number"}, "after": {"type": "number"}}, ["before", "after"],
        ),
        _calc_tool(
            "calculate_ratio", "두 수치의 비율(%)을 계산한다 (예: 영업이익률, 부채비율, 매출액대비).",
            calculate_ratio,
            {"numerator": {"type": "number"}, "denominator": {"type": "number"}, "label": {"type": "string"}},
            ["numerator", "denominator"],
        ),
        _calc_tool(
            "calculate_cagr", "연평균성장률(CAGR)을 계산한다.",
            calculate_cagr,
            {"begin_value": {"type": "number"}, "end_value": {"type": "number"}, "n_years": {"type": "number"}},
            ["begin_value", "end_value", "n_years"],
        ),
    ]


def build_all_tools(retriever, manifest: list[ManifestRow], correction_index: dict[str, CorrectionRecord]) -> list[ToolDef]:
    return [
        make_search_disclosures_tool(retriever),
        make_get_correction_history_tool(manifest, correction_index),
        make_get_latest_report_tool(manifest, correction_index),
        *build_calculation_tools(),
    ]


def dispatch_tool_call(tools: list[ToolDef], name: str, arguments: dict) -> dict:
    tool = next((t for t in tools if t.name == name), None)
    if tool is None:
        return {"error": f"알 수 없는 tool: {name}"}
    try:
        return tool.handler(**arguments)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
