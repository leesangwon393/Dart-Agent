"""공통 Chunk Schema (§24) + node -> 검색용 text 렌더링 (§21, §26).

Parsing 방식은 4종 공시마다 다르지만, Retrieval 이후 사용하는 Chunk 구조는
전부 이 스키마로 통일한다. 실제 데이터에서 알 수 없는 값은 hallucinate 하지
않고 None(null) 을 쓴다.

=== Kim 브랜치 감사 결과 병합 (2026-08-25) ===

[A] field_codes 를 dict[str,str] -> list[FieldRef] 로 재설계했다.
    기존은 `{셀 텍스트: 코드}` 평면 dict 라 (1) 위치 정보가 없고 (2) 같은 텍스트면
    덮어써졌다. Kim 실측 소실률: major 80.1% / holding 40.5% / periodic 33.9%.
    AUNITVALUE(=DART 가 이미 정규화해준 값, 예 "20230421")는 100% 폐기됐다.
    이제 (code, unit, unit_value, text, row, col) 을 리스트로 보존한다.

[B] estimate_tokens 를 주입 가능하게 만들었다(set_token_counter). 기존 상수 2.0
    은 "실측 아님"이라 스스로 인정한 값이었다 — 주입하지 않으면 여전히 heuristic
    을 쓰지만, 필요하면 실제 tokenizer 를 꽂을 수 있는 인터페이스만 열어둔다.

[C] render_table_node/render_table_node_fragments 가 **열 정렬을 보존**한다.
    (dup_left/dup_up 칸을 렌더링에서만 빈칸으로 내는) 정규 그리드를 그대로
    " | " 로 내되, 여러 헤더 행을 열별로 합친 header_labels, style="kv"(구조화
    chunking, arXiv 2605.00318 STC 근거) preamble 에 title_hint/unit_hint/
    period_hint 전부 포함 — Kim 의 렌더링 디테일을 기반으로 삼았다.

=== 어젯밤 변경분(b112925, 2026-08-24)은 유지, semantic-block-first 로 재작성 ===
render_table_node_fragments() 의 body row 순회는 Kim 의 순수 row/token count
방식이 아니라, **detect_semantic_blocks() + block 단위 패킹**을 쓴다 —
"1. 매출액 계"와 "2. 영업이익 계"가 chunk 경계에서 갈라지던 회귀(SK하이닉스
사업보고서 재현) 수정. semantic 구조가 없는 표에서만 기존 row/token-count
fallback 을 쓴다. ChunkSchema 의 table_id/semantic_groups/metric_hints/
table_chunk_index·count/prev·next_table_chunk_id 도 그대로 유지한다
(sibling expansion, agent/tools.py).
"""

from __future__ import annotations

import logging
from typing import Callable, Literal

from pydantic import BaseModel, Field

from disclosure_rag.common.doc_tree import KeyValueNode, TableNode, TextNode

logger = logging.getLogger(__name__)

ContentType = Literal["text", "table", "key_value"]


class FieldRef(BaseModel):
    """DART 의 기계판독 속성 1건. 위치를 함께 보존해 덮어쓰기 소실을 막는다."""

    code: str | None = None        # TE[ACODE]
    unit: str | None = None        # TU[AUNIT]
    unit_value: str | None = None  # TU[AUNITVALUE] — 정규화된 값 (예: "20230421")
    text: str | None = None        # 셀 원문 텍스트
    row: int | None = None
    col: int | None = None
    key: str | None = None         # key-value 표에서의 key (해당 시)


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

    # DART TE[ACODE]/TU[AUNIT/AUNITVALUE] 구조화 필드 — 위치까지 보존 (변경점 [A])
    field_codes: list[FieldRef] = Field(default_factory=list)

    # 이 chunk 가 어떤 단위/기준기간 아래에 있었는지 (변경점 [C] 의 부산물).
    unit_hint: str | None = None
    period_hint: str | None = None

    # --- 표 semantic chunking 메타데이터 (b112925, 2026-08 표 chunk 분리 회귀 수정) ---
    # 전부 기본값을 둬서 표가 아닌(text) chunk 나 기존 데이터에 영향이 없다.
    # 이 chunk 가 표(TableNode)에서 왔으면, 같은 원본 표에서 나온 다른 조각들과
    # 공유하는 식별자. sibling expansion(agent/tools.py)의 조인 키로 쓴다.
    table_id: str | None = None
    # 이 chunk 에 실제로 포함된 semantic block 라벨들 (예: ["1. 매출액", "2. 영업이익"]).
    semantic_groups: list[str] = Field(default_factory=list)
    # semantic_groups 에서 번호/계층 prefix 를 제거한 지표명 후보 (예: ["매출액", "영업이익"]).
    metric_hints: list[str] = Field(default_factory=list)
    # 표가 여러 chunk 로 나뉜 경우의 순번/총개수 (1-based). 나뉘지 않았으면 1/1.
    table_chunk_index: int | None = None
    table_chunk_count: int | None = None
    # 같은 table_id 를 가진 인접 chunk 의 실제 chunk_id (조립 후 채워짐).
    prev_table_chunk_id: str | None = None
    next_table_chunk_id: str | None = None


def filter_leaf_chunks(chunks: list["ChunkSchema"]) -> list["ChunkSchema"]:
    """BM25/Dense 인덱스에 넣을 "검색 대상" chunk 만 남긴다.

    §13/§20: Parent 는 Context 확장용이지 검색 대상이 아니다 — "이 chunk_id 가
    다른 chunk 의 parent_chunk_id 로 나타나면 parent" 라는 규칙으로 leaf 를
    가려낸다 (parent 자체 텍스트는 section 전체를 이어붙인 것이라 매우 길 수
    있어, 그대로 임베딩하면 비정상적으로 느려지고 검색 품질도 떨어진다).
    major/exchange 처럼 애초에 Parent-Child 를 안 쓰는 chunk 는 parent_chunk_id
    가 None 이면서 아무도 참조하지 않으므로 자동으로 leaf 로 남는다.
    """
    referenced_as_parent = {c.parent_chunk_id for c in chunks if c.parent_chunk_id}
    return [c for c in chunks if c.chunk_id not in referenced_as_parent]


# ---------------------------------------------------------------- 토큰 추정
TOKEN_CHARS_PER_TOKEN = 2.0  # heuristic 기본값 (실측 아님)

_token_counter: Callable[[str], int] | None = None


def set_token_counter(fn: Callable[[str], int] | None) -> None:
    """실제 토크나이저를 주입한다. 예:

        from transformers import AutoTokenizer
        tk = AutoTokenizer.from_pretrained("BAAI/bge-m3")
        set_token_counter(lambda s: len(tk.encode(s, add_special_tokens=False)))

    주입하지 않으면 heuristic 으로 동작한다.
    """
    global _token_counter
    _token_counter = fn


def token_counter_is_exact() -> bool:
    return _token_counter is not None


def estimate_tokens(text: str) -> int:
    """실제 토크나이저가 주입돼 있으면 그것을, 아니면 heuristic 을 쓴다. 여기서는
    semantic boundary 를 token 숫자보다 우선한다는 원칙(§12) 아래, "너무 길다/
    작다" 판단용 추정치만 필요하다."""
    if _token_counter is not None:
        return max(1, _token_counter(text))
    return max(1, int(len(text) / TOKEN_CHARS_PER_TOKEN))


# ---------------------------------------------------------------- 렌더링
def render_text_node(node: TextNode) -> str:
    return node.text


def render_kv_node(node: KeyValueNode) -> str:
    lines: list[str] = []
    if node.group_label:
        lines.append(f"[{node.group_label}]")
    for p in node.pairs:
        # AUNITVALUE 가 셀 텍스트와 다른 정보를 담고 있으면 함께 노출한다.
        # 예: 텍스트 "2023년 04월 21일" / unit_value "20230421"
        if p.unit_value and p.unit_value not in p.value:
            lines.append(f"- {p.key}: {p.value} ({p.unit_value})")
        else:
            lines.append(f"- {p.key}: {p.value}")
    return "\n".join(lines)


def _cell_text(cell) -> str:
    """span 으로 복제된 칸은 빈칸으로 낸다 — 열 수는 유지하고 텍스트만 생략."""
    if cell.dup_left or cell.dup_up:
        return ""
    return cell.text


TableStyle = Literal["grid", "kv"]


def _render_row_kv(row, header_cells) -> str:
    """행 하나를 `컬럼명: 값` 블록으로 낸다 (structure-aware chunking 계열 표현).

    arXiv 2605.00318(STC)은 표를 행 단위 key-value 블록으로 표현하면 검색이 크게
    오른다고 보고한다(MAUD 39,231건, hybrid MRR 0.358 -> 0.595, BM25 R@1 0.366 -> 0.754).
    단 같은 논문이 **KV 표현만 하고 구조 인식 분할을 안 하면 오히려 baseline 보다
    나빴다**고도 보고한다 — 표현이 아니라 "행 경계를 지키는 분할"이 이득의 본체다.
    우리는 이미 semantic block 경계를 지키므로, 여기서는 표현 축만 선택지로 둔다.
    """
    parts = []
    for j, cell in enumerate(row):
        if cell.dup_left or cell.dup_up or not cell.text:
            continue
        head = header_cells[j] if j < len(header_cells) else ""
        parts.append(f"{head}: {cell.text}" if head else cell.text)
    return " / ".join(parts)


class TableFragment:
    """render_table_node_fragments() 의 조각 하나. ChunkSchema 표 메타데이터의
    소스가 된다 (table_id 는 node.table_id 를 그대로 쓰면 되므로 여기엔 없음)."""

    __slots__ = ("text", "semantic_groups", "metric_hints", "split_reason")

    def __init__(self, text: str, semantic_groups: list[str], metric_hints: list[str], split_reason: str):
        self.text = text
        self.semantic_groups = semantic_groups
        self.metric_hints = metric_hints
        self.split_reason = split_reason


def _table_header_and_body(node: TableNode) -> tuple[list, list]:
    header_idx = {i for i, row in enumerate(node.rows) if row and all(c.is_header for c in row if c.text)}
    if header_idx:
        header_rows = [node.rows[i] for i in sorted(header_idx)]
        body_rows = [row for i, row in enumerate(node.rows) if i not in header_idx]
    else:
        header_rows = [node.rows[0]] if node.rows else []
        body_rows = node.rows[1:]
    return header_rows, body_rows


def _header_labels(node: TableNode, header_rows: list) -> list[str]:
    """여러 헤더 행이 있으면 열별로 이어붙여 하나의 라벨로 만든다 (style="kv" 용)."""
    ncol = len(node.rows[0]) if node.rows else 0
    labels: list[str] = []
    for j in range(ncol):
        parts = []
        for hr in header_rows:
            if j < len(hr) and hr[j].text and not (hr[j].dup_left or hr[j].dup_up):
                parts.append(hr[j].text)
        labels.append(" ".join(parts))
    return labels


def _fmt_row(row, *, style: TableStyle = "grid", header_labels: list[str] | None = None) -> str:
    if style == "kv":
        return _render_row_kv(row, header_labels or [])
    return " | ".join(_cell_text(c) for c in row)


def render_table_node_fragments(
    node: TableNode, *, max_rows_per_chunk: int = 20, max_tokens_per_chunk: int = 1000,
    style: TableStyle = "grid",
) -> list[TableFragment]:
    """큰 표를 Title+Unit+Period+Header+semantic block 단위로 나눈다.

    실제 재현 사례(SK하이닉스 사업보고서 20260317000635.xml, "192,972,588" 검색):
    기존엔 max_rows_per_chunk/max_tokens_per_chunk 같은 순수 행count/토큰 기준
    으로만 body_rows 를 잘라, "1. 매출액"의 "계"(192,972,588백만원) 바로 다음에
    오는 "2. 영업이익"의 "계"(47,206,319백만원, 실제 정답)가 다른 chunk 로
    갈라졌다 — 단일 chunk 검색으로는 정답을 찾을 수 없었다.

    우선순위(§12 원칙 5~7): 1순위 semantic block 보존(안 찢기) > 2순위 max_tokens
    예산 > 3순위(block 구조를 못 찾은 표에서만) 기존 max_rows fallback.

    렌더링 디테일(Kim 브랜치 병합): dup_left/dup_up 칸은 빈칸으로 낸다(_cell_text),
    style="kv" 로 행을 "컬럼명: 값" 블록으로도 낼 수 있다, preamble 에
    title_hint/unit_hint/period_hint 전부 포함한다.
    """
    if not node.rows:
        return []

    from disclosure_rag.parsing.table_parser import detect_semantic_blocks, strip_numbering_prefix

    header_rows, body_rows = _table_header_and_body(node)
    header_labels = _header_labels(node, header_rows)
    # kv 표현에서는 헤더 라벨이 각 행에 이미 붙으므로 헤더 행을 따로 반복하지 않는다.
    header_lines = [] if style == "kv" else [" | ".join(_cell_text(c) for c in r) for r in header_rows]

    preamble_parts = [p for p in (node.title_hint, node.unit_hint, node.period_hint) if p]
    preamble = "\n".join(preamble_parts)
    fixed_overhead = estimate_tokens(preamble) + sum(estimate_tokens(h) for h in header_lines)

    def fmt(row) -> str:
        return _fmt_row(row, style=style, header_labels=header_labels)

    def render_lines(extra_label: str | None, row_indices: list[int]) -> str:
        parts = []
        if preamble:
            parts.append(preamble)
        parts.extend(header_lines)
        if extra_label:
            parts.append(f"[{extra_label}]")
        parts.extend(fmt(body_rows[i]) for i in row_indices)
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
        return sum(estimate_tokens(fmt(body_rows[i])) for i in block)

    fragments: list[TableFragment] = []
    table_log_id = getattr(node, "table_id", None)

    if not has_structure:
        # 구조 신호가 없는 평평한 표 -> 기존 동작(행 단위 max_rows+max_tokens)과 동일.
        group: list[int] = []
        group_tokens = fixed_overhead
        for block in row_blocks:
            i = block[0]
            row_tokens = estimate_tokens(fmt(body_rows[i]))
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
            # Oversized block: 먼저 지금까지 쌓인 group 을 flush 하고, 이 block
            # 자체를 내부적으로 토큰 예산 단위로 쪼갠다. 쪼개진 모든 조각에
            # title_hint/unit_hint/period_hint/header 는 render_lines 가 이미
            # 반복 삽입하고, 여기서는 semantic block label + [i/총개수] 를
            # 추가로 반복 삽입해 "계"만 남은 조각도 어느 항목인지 알 수 있게 한다.
            flush("token_budget")
            group_blocks, group_tokens = [], fixed_overhead

            sub_groups: list[list[int]] = []
            cur: list[int] = []
            cur_tokens = fixed_overhead
            for i in block:
                rt = estimate_tokens(fmt(body_rows[i]))
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
    style: TableStyle = "grid",
) -> list[str]:
    """하위호환 wrapper — 텍스트 리스트만 필요한 호출부(예: chunking_variants.py
    실험 코드)를 위해 유지한다. 새 코드는 render_table_node_fragments() 를 써서
    semantic_groups/metric_hints 메타데이터까지 받는 걸 권장한다."""
    return [f.text for f in render_table_node_fragments(
        node, max_rows_per_chunk=max_rows_per_chunk, max_tokens_per_chunk=max_tokens_per_chunk, style=style,
    )]


def table_field_refs(node: TableNode) -> list[FieldRef]:
    """표에서 기계판독 속성을 위치까지 붙여 뽑는다 (변경점 [A]). span 으로 복제된
    칸(dup_left/dup_up)은 원본 셀에서 이미 뽑히므로 중복 방지를 위해 건너뛴다."""
    refs: list[FieldRef] = []
    for row in node.rows:
        for cell in row:
            if cell.dup_left or cell.dup_up:
                continue
            if cell.field_code or cell.unit_code or cell.unit_value:
                refs.append(
                    FieldRef(
                        code=cell.field_code, unit=cell.unit_code, unit_value=cell.unit_value,
                        text=cell.text or None, row=cell.row, col=cell.col,
                    )
                )
    return refs


def kv_field_refs(node: KeyValueNode) -> list[FieldRef]:
    refs: list[FieldRef] = []
    for p in node.pairs:
        if p.field_code or p.unit_code or p.unit_value:
            refs.append(
                FieldRef(
                    code=p.field_code, unit=p.unit_code, unit_value=p.unit_value,
                    text=p.value or None, key=p.key or None,
                )
            )
    return refs


def render_search_text(
    *,
    company: str | None,
    report_name: str | None,
    period: str | None,
    section_path: list[str],
    body_text: str,
) -> str:
    """§26: Embedding/BM25 검색용 텍스트에 [회사]/[공시]/[기간]/[Section]/[내용] 컨텍스트를 포함한다.

    문헌 근거: 문서 수준 메타데이터를 청크에 부착하면 금융문서 QA 정확도가
    50~60% -> 72~75% 로 오른다(Snowflake, SEC 23,000 PDF / 500 질의).
    """
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
