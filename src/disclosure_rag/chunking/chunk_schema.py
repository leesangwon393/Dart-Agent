"""공통 Chunk Schema (§24) + node -> 검색용 text 렌더링 (§21, §26).

Parsing 방식은 4종 공시마다 다르지만, Retrieval 이후 사용하는 Chunk 구조는
전부 이 스키마로 통일한다. 실제 데이터에서 알 수 없는 값은 hallucinate 하지
않고 None(null) 을 쓴다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from disclosure_rag.common.doc_tree import KeyValueNode, TableNode, TextNode

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


def render_table_node(
    node: TableNode, *, max_rows_per_chunk: int = 20, max_tokens_per_chunk: int = 1000,
) -> list[str]:
    """큰 표를 Title+Header+Unit+N행 단위로 나누고, 분할된 모든 조각에 Title/Header/Unit
    을 반복 삽입한다 (§21 "다음 Chunk 에도 정보를 반복한다"). 작은 표는 그대로 1개.

    행 수뿐 아니라 token 예산도 같이 지킨다 — 재무제표처럼 행은 적어도 열이 매우
    넓은 표가 있어, 행 수 기준만으로는 chunk 하나가 §12 목표(400~800, 최대 ~1000)
    를 크게 초과할 수 있었다 (회귀 발견: 723개 표본 chunk 중 17개가 1500 토큰 초과)."""
    if not node.rows:
        return []

    header_idx = [i for i, row in enumerate(node.rows) if row and all(c.is_header for c in row if c.text)]
    if header_idx:
        header_rows = [node.rows[i] for i in header_idx]
        body_rows = [row for i, row in enumerate(node.rows) if i not in header_idx]
    else:
        header_rows = [node.rows[0]]
        body_rows = node.rows[1:]

    def fmt_row(row) -> str:
        return " | ".join(c.text for c in row)

    header_lines = [fmt_row(r) for r in header_rows]
    preamble_parts = []
    if node.title_hint:
        preamble_parts.append(node.title_hint)
    if node.unit_hint:
        preamble_parts.append(node.unit_hint)
    preamble = "\n".join(preamble_parts)

    fixed_overhead = estimate_tokens(preamble) + sum(estimate_tokens(h) for h in header_lines)

    chunks: list[str] = []
    group: list = []
    group_tokens = fixed_overhead

    def flush_group() -> None:
        if not group:
            return
        parts = []
        if preamble:
            parts.append(preamble)
        parts.extend(header_lines)
        parts.extend(fmt_row(r) for r in group)
        chunks.append("\n".join(parts))

    for row in body_rows:
        row_text = fmt_row(row)
        row_tokens = estimate_tokens(row_text)
        would_exceed_rows = len(group) >= max_rows_per_chunk
        would_exceed_tokens = group and (group_tokens + row_tokens > max_tokens_per_chunk)
        if group and (would_exceed_rows or would_exceed_tokens):
            flush_group()
            group, group_tokens = [], fixed_overhead
        group.append(row)
        group_tokens += row_tokens
    flush_group()

    if not chunks:
        # body 가 아예 없는 표 (header/preamble 만 존재) -> header-only chunk 1개는 보존
        chunks.append("\n".join([preamble] + header_lines) if preamble else "\n".join(header_lines))
    return chunks if chunks else ["\n".join(header_lines)]


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
