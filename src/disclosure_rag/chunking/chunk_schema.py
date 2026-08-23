"""공통 Chunk Schema (§24) + node -> 검색용 text 렌더링 (§21, §26).

Parsing 방식은 4종 공시마다 다르지만, Retrieval 이후 사용하는 Chunk 구조는
전부 이 스키마로 통일한다. 실제 데이터에서 알 수 없는 값은 hallucinate 하지
않고 None(null) 을 쓴다.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from disclosure_rag.common.doc_tree import KeyValueNode, TableNode, TextNode

logger = logging.getLogger(__name__)

ContentType = Literal["text", "table", "key_value"]


class ChunkSchema(BaseModel):
    chunk_id: str
    report_id: str  # manifest doc_id
    parent_chunk_id: str | None = None

    text: str  # embedding/BM25 에 쓰는, [회사]/[공시]/[Section] 컨텍스트가 포함된 검색용 텍스트
    raw_text: str  # 컨텍스트 헤더 없는 순수 chunk 내용 (재조합/디버깅용)

    company: str | None = None
    corp_code: str | None = None

    report_type: str | None = None      # doc_group: periodic|major|exchange|holding
    report_subtype: str | None = None   # doc_subtype 또는 report_subtype(main/attachment 등)
    report_name: str | None = None      # document_name / report_nm

    period: str | None = None           # 예: "2024-12" (periodic base_year/month)
    filing_date: str | None = None      # rcept_dt YYYYMMDD

    section_path: list[str] = Field(default_factory=list)

    content_type: ContentType = "text"

    source_path: str | None = None

    is_correction: bool = False
    correction_group_id: str | None = None
    correction_order: int | None = None
    is_latest: bool | None = None

    # DART TE[ACODE]/TU[AUNIT] 구조화 필드 — 버리지 않고 보존 (§14 원칙)
    field_codes: dict[str, str] = Field(default_factory=dict)

    # --- 표 semantic chunking 메타데이터 (Phase 4, 2026-08 표 chunk 분리 회귀 수정) ---
    # 전부 기본값을 둬서 표가 아닌(text) chunk 나 기존 데이터에 영향이 없다.
    # 이 chunk 가 표(TableNode)에서 왔으면, 같은 원본 표에서 나온 다른 조각들과
    # 공유하는 식별자. sibling expansion(§Phase5, tools.py)의 조인 키로 쓴다.
    table_id: str | None = None
    # 이 chunk 에 실제로 포함된 semantic block 라벨들 (예: ["1. 매출액", "2. 영업이익"]).
    semantic_groups: list[str] = Field(default_factory=list)
    # semantic_groups 에서 번호/계층 prefix 를 제거한 지표명 후보 (예: ["매출액", "영업이익"]).
    metric_hints: list[str] = Field(default_factory=list)
    # 표가 여러 chunk 로 나뉜 경우의 순번/총개수 (1-based). 나뉘지 않았으면 1/1.
    table_chunk_index: int | None = None
    table_chunk_count: int | None = None
    # 같은 table_id 를 가진 인접 chunk 의 실제 chunk_id (조립 후 채워짐, §Phase5).
    prev_table_chunk_id: str | None = None
    next_table_chunk_id: str | None = None


def filter_leaf_chunks(chunks: list["ChunkSchema"]) -> list["ChunkSchema"]:
    """BM25/Dense 인덱스에 넣을 "검색 대상" chunk 만 남긴다.

    §13/§20: Parent 는 Context 확장용이지 검색 대상이 아니다 — "이 chunk_id 가
    다른 chunk 의 parent_chunk_id 로 나타나면 parent" 라는 규칙으로 leaf 를
    가려낸다 (parent 자체 텍스트는 section 전체를 이어붙인 것이라 매우 길 수
    있어, 그대로 임베딩하면 비정상적으로 느려지고 검색 품질도 떨어진다 —
    실측: BGE-M3 CPU 인코딩이 이 때문에 30분 이상 걸리는 것을 확인).
    major/exchange 처럼애초에 Parent-Child 를 안 쓰는 chunk 는 parent_chunk_id
    가 None 이면서 아무도 참조하지 않으므로 자동으로 leaf 로 남는다.
    """
    referenced_as_parent = {c.parent_chunk_id for c in chunks if c.parent_chunk_id}
    return [c for c in chunks if c.chunk_id not in referenced_as_parent]


TOKEN_CHARS_PER_TOKEN = 2.0  # 한글이 섞인 텍스트의 대략적인 char/token 비율 (실측 아님, rough heuristic)


def estimate_tokens(text: str) -> int:
    """정확한 tokenizer 는 Phase 9 의 embedding tokenizer 로 대체 가능하도록 인터페이스만
    분리한다. 여기서는 semantic boundary 를 token 숫자보다 우선한다는 원칙(§12) 아래,
    "너무 길다/작다" 판단용 rough 추정치만 필요하다."""
    return max(1, int(len(text) / TOKEN_CHARS_PER_TOKEN))


def render_text_node(node: TextNode) -> str:
    return node.text


def render_kv_node(node: KeyValueNode) -> str:
    lines = []
    if node.group_label:
        lines.append(f"[{node.group_label}]")
    for key, value, _code, _unit in node.pairs:
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


class TableFragment:
    """render_table_node_fragments() 의 조각 하나. Phase 4 ChunkSchema 메타데이터의
    소스가 된다 (table_id 는 node.table_id 를 그대로 쓰면 되므로 여기엔 없음)."""

    __slots__ = ("text", "semantic_groups", "metric_hints", "split_reason")

    def __init__(self, text: str, semantic_groups: list[str], metric_hints: list[str], split_reason: str):
        self.text = text
        self.semantic_groups = semantic_groups
        self.metric_hints = metric_hints
        self.split_reason = split_reason


def _table_header_and_body(node: TableNode) -> tuple[list, list]:
    header_idx = [i for i, row in enumerate(node.rows) if row and all(c.is_header for c in row if c.text)]
    if header_idx:
        header_rows = [node.rows[i] for i in header_idx]
        body_rows = [row for i, row in enumerate(node.rows) if i not in header_idx]
    else:
        header_rows = [node.rows[0]] if node.rows else []
        body_rows = node.rows[1:]
    return header_rows, body_rows


def _fmt_row(row) -> str:
    return " | ".join(c.text for c in row)


def render_table_node_fragments(
    node: TableNode, *, max_rows_per_chunk: int = 20, max_tokens_per_chunk: int = 1000,
) -> list[TableFragment]:
    """큰 표를 Title+Header+Unit+semantic block 단위로 나눈다 (Phase 1~3 회귀 수정).

    실제 재현 사례(SK하이닉스 사업보고서 20260317000635.xml, "192,972,588" 검색):
    기존엔 max_rows_per_chunk/max_tokens_per_chunk 같은 순수 행count/토큰 기준
    으로만 body_rows 를 잘라, "1. 매출액"의 "계"(192,972,588백만원) 바로 다음에
    오는 "2. 영업이익"의 "계"(47,206,319백만원, 실제 정답)가 다른 chunk 로
    갈라졌다 — 단일 chunk 검색으로는 정답을 찾을 수 없었다.

    우선순위(§12 원칙 5~7): 1순위 semantic block 보존(안 찢기) > 2순위 max_tokens
    예산 > 3순위(block 구조를 못 찾은 표에서만) 기존 max_rows fallback.
    """
    if not node.rows:
        return []

    from disclosure_rag.parsing.table_parser import detect_semantic_blocks, strip_numbering_prefix

    header_rows, body_rows = _table_header_and_body(node)
    header_lines = [_fmt_row(r) for r in header_rows]
    preamble_parts = []
    if node.title_hint:
        preamble_parts.append(node.title_hint)
    if node.unit_hint:
        preamble_parts.append(node.unit_hint)
    preamble = "\n".join(preamble_parts)
    fixed_overhead = estimate_tokens(preamble) + sum(estimate_tokens(h) for h in header_lines)

    def render_lines(extra_label: str | None, row_indices: list[int]) -> str:
        parts = []
        if preamble:
            parts.append(preamble)
        parts.extend(header_lines)
        if extra_label:
            parts.append(f"[{extra_label}]")
        parts.extend(_fmt_row(body_rows[i]) for i in row_indices)
        return "\n".join(parts)

    if not body_rows:
        text = "\n".join([preamble] + header_lines) if preamble else "\n".join(header_lines)
        return [TableFragment(text=text, semantic_groups=[], metric_hints=[], split_reason="empty_body")]

    row_blocks = detect_semantic_blocks(body_rows)
    has_structure = any(len(b) > 1 for b in row_blocks)

    def block_label(block: list[int]) -> str:
        for i in block:
            label = next((c.text.strip() for c in body_rows[i] if c.text.strip()), "")
            if label:
                return label
        return ""

    def block_tokens(block: list[int]) -> int:
        return sum(estimate_tokens(_fmt_row(body_rows[i])) for i in block)

    fragments: list[TableFragment] = []
    table_log_id = getattr(node, "table_id", None)

    if not has_structure:
        # 구조 신호가 없는 평평한 표 -> 기존 동작(행 단위 max_rows+max_tokens)과 동일.
        group: list[int] = []
        group_tokens = fixed_overhead
        for block in row_blocks:
            i = block[0]
            row_tokens = estimate_tokens(_fmt_row(body_rows[i]))
            would_exceed_rows = len(group) >= max_rows_per_chunk
            would_exceed_tokens = group and (group_tokens + row_tokens > max_tokens_per_chunk)
            if group and (would_exceed_rows or would_exceed_tokens):
                fragments.append(TableFragment(
                    text=render_lines(None, group), semantic_groups=[], metric_hints=[],
                    split_reason="no_semantic_structure",
                ))
                group, group_tokens = [], fixed_overhead
            group.append(i)
            group_tokens += row_tokens
        if group:
            fragments.append(TableFragment(
                text=render_lines(None, group), semantic_groups=[], metric_hints=[],
                split_reason="no_semantic_structure",
            ))
        logger.debug("TABLE %s: no_semantic_structure, %d rows -> %d fragments", table_log_id, len(body_rows), len(fragments))
        return fragments if fragments else [TableFragment(text=render_lines(None, []), semantic_groups=[], metric_hints=[], split_reason="no_semantic_structure")]

    # --- semantic block 이 있는 경우: block 단위로 패킹, max_rows 는 쓰지 않는다 ---
    group_blocks: list[list[int]] = []  # 이번 group 에 들어간 block(row index list) 들
    group_tokens = fixed_overhead

    def flush(reason: str) -> None:
        if not group_blocks:
            return
        row_indices = [i for b in group_blocks for i in b]
        labels = [block_label(b) for b in group_blocks if block_label(b)]
        metrics = [strip_numbering_prefix(lbl) for lbl in labels]
        logger.debug("TABLE %s: flush group labels=%s tokens=%d reason=%s", table_log_id, labels, group_tokens, reason)
        fragments.append(TableFragment(
            text=render_lines(None, row_indices), semantic_groups=labels, metric_hints=metrics,
            split_reason=reason,
        ))

    for block in row_blocks:
        label = block_label(block)
        b_tokens = block_tokens(block)
        logger.debug("TABLE %s: Block %r tokens=%d", table_log_id, label, b_tokens)

        if b_tokens > max_tokens_per_chunk:
            # Oversized block (Phase 3): 먼저 지금까지 쌓인 group 을 flush 하고,
            # 이 block 자체를 내부적으로 토큰 예산 단위로 쪼갠다. 쪼개진 모든
            # 조각에 title_hint/unit_hint/header 는 render_lines 가 이미 반복
            # 삽입하고, 여기서는 semantic block label + [i/총개수] 를 추가로
            # 반복 삽입해 "계"만 남은 조각도 어느 항목인지 알 수 있게 한다.
            flush("token_budget")
            group_blocks, group_tokens = [], fixed_overhead

            sub_groups: list[list[int]] = []
            cur: list[int] = []
            cur_tokens = fixed_overhead
            for i in block:
                rt = estimate_tokens(_fmt_row(body_rows[i]))
                if cur and cur_tokens + rt > max_tokens_per_chunk:
                    sub_groups.append(cur)
                    cur, cur_tokens = [], fixed_overhead
                cur.append(i)
                cur_tokens += rt
            if cur:
                sub_groups.append(cur)

            total = len(sub_groups)
            for idx, sub in enumerate(sub_groups, start=1):
                sub_label = f"{label} [{idx}/{total}]" if label else f"[{idx}/{total}]"
                fragments.append(TableFragment(
                    text=render_lines(sub_label, sub),
                    semantic_groups=[label] if label else [],
                    metric_hints=[strip_numbering_prefix(label)] if label else [],
                    split_reason="oversized_block",
                ))
            continue

        if group_blocks and group_tokens + b_tokens > max_tokens_per_chunk:
            flush("token_budget")
            group_blocks, group_tokens = [], fixed_overhead

        group_blocks.append(block)
        group_tokens += b_tokens

    flush("token_budget" if len(fragments) > 0 else "single_chunk")

    if not fragments:
        fragments.append(TableFragment(text=render_lines(None, []), semantic_groups=[], metric_hints=[], split_reason="empty_body"))

    return fragments


def render_table_node(
    node: TableNode, *, max_rows_per_chunk: int = 20, max_tokens_per_chunk: int = 1000,
) -> list[str]:
    """하위호환 wrapper — 텍스트 리스트만 필요한 호출부(예: chunking_variants.py
    실험 코드)를 위해 유지한다. 새 코드는 render_table_node_fragments() 를 써서
    semantic_groups/metric_hints 메타데이터까지 받는 걸 권장한다."""
    return [f.text for f in render_table_node_fragments(
        node, max_rows_per_chunk=max_rows_per_chunk, max_tokens_per_chunk=max_tokens_per_chunk,
    )]


def render_search_text(
    *,
    company: str | None,
    report_name: str | None,
    period: str | None,
    section_path: list[str],
    body_text: str,
) -> str:
    """§26: Embedding/BM25 검색용 텍스트에 [회사]/[공시]/[기간]/[Section]/[내용] 컨텍스트를 포함한다."""
    lines = []
    if company:
        lines.append(f"[회사]\n{company}")
    if report_name:
        lines.append(f"[공시]\n{report_name}")
    if period:
        lines.append(f"[기간]\n{period}")
    if section_path:
        lines.append("[Section]\n" + " > ".join(section_path))
    lines.append(f"[내용]\n{body_text}")
    return "\n\n".join(lines)
