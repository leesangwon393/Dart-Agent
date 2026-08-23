"""ContentNode 리스트를 §12 원칙(Section/Paragraph 경계 > Token 길이)에 따라
Chunk 단위로 묶는 공통 로직. 4종 Chunker 가 전부 이 위에서 동작한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from disclosure_rag.common.doc_tree import ContentNode, KeyValueNode, TableNode, TextNode
from disclosure_rag.chunking.chunk_schema import (
    ContentType,
    estimate_tokens,
    render_kv_node,
    render_table_node_fragments,
    render_text_node,
)


@dataclass
class PackedUnit:
    text: str
    content_type: ContentType
    field_codes: dict[str, str] = field(default_factory=dict)
    # 표 semantic chunking 메타데이터 (Phase 4). 표가 아닌 unit 은 전부 기본값(None/[])
    # 으로 남아 하위호환에 영향이 없다.
    table_id: str | None = None
    semantic_groups: list[str] = field(default_factory=list)
    metric_hints: list[str] = field(default_factory=list)
    table_chunk_index: int | None = None
    table_chunk_count: int | None = None


def pack_nodes(
    nodes: list[ContentNode],
    *,
    target_tokens: int = 600,
    max_tokens: int = 1000,
) -> list[PackedUnit]:
    units: list[PackedUnit] = []
    buf_texts: list[str] = []
    buf_tokens = 0
    buf_types: set[str] = set()
    buf_codes: dict[str, str] = {}
    # 버퍼에 병합된(=자기 혼자 예산 안에 들어가 다른 content 와 섞인) 표의 메타데이터.
    # 버퍼 하나에 서로 다른 표가 둘 이상 섞이는 드문 경우는 마지막 표 기준으로
    # 덮어쓴다 — 어차피 그 표들은 table_chunk_count=1(형제가 없는) 표들이라
    # sibling expansion 대상이 아니므로 실질적 정보 손실은 없다.
    buf_table_id: str | None = None
    buf_semantic_groups: list[str] = []
    buf_metric_hints: list[str] = []

    def flush() -> None:
        nonlocal buf_texts, buf_tokens, buf_types, buf_codes, buf_table_id, buf_semantic_groups, buf_metric_hints
        if not buf_texts:
            return
        content_type: ContentType = "key_value" if "key_value" in buf_types else ("table" if "table" in buf_types else "text")
        units.append(PackedUnit(
            text="\n\n".join(buf_texts), content_type=content_type, field_codes=dict(buf_codes),
            table_id=buf_table_id, semantic_groups=list(buf_semantic_groups), metric_hints=list(buf_metric_hints),
            table_chunk_index=1 if buf_table_id else None, table_chunk_count=1 if buf_table_id else None,
        ))
        buf_texts, buf_tokens, buf_types, buf_codes = [], 0, set(), {}
        buf_table_id, buf_semantic_groups, buf_metric_hints = None, [], []

    for node in nodes:
        if isinstance(node, TableNode):
            table_fragments = render_table_node_fragments(node)
            table_texts = [f.text for f in table_fragments]
            # TE[ACODE]/TU[AUNIT] 는 렌더된 텍스트 안에 값으로는 남지만, 구조화
            # metadata(field_codes) 로도 반드시 보존한다 (§14 "이 값들을 버리지 않는다").
            table_codes = {
                cell.text: cell.field_code or cell.unit_code
                for row in node.rows for cell in row
                if (cell.field_code or cell.unit_code) and cell.text
            }
            if len(table_texts) == 1 and buf_tokens + estimate_tokens(table_texts[0]) <= max_tokens:
                buf_texts.append(table_texts[0])
                buf_tokens += estimate_tokens(table_texts[0])
                buf_types.add("table")
                buf_codes.update(table_codes)
                buf_table_id = node.table_id
                buf_semantic_groups = list(table_fragments[0].semantic_groups)
                buf_metric_hints = list(table_fragments[0].metric_hints)
                if buf_tokens >= target_tokens:
                    flush()
            else:
                flush()  # 큰 표는 그 자체로 독립 chunk(들)로 분리
                # 단순화: 분할된 각 조각에 표 전체의 field_codes 를 동일하게 붙인다
                # (완벽하게 조각별로 scoping 하는 대신, 값을 누락시키지 않는 쪽을 택함).
                total = len(table_fragments)
                for idx, frag in enumerate(table_fragments, start=1):
                    units.append(PackedUnit(
                        text=frag.text, content_type="table", field_codes=dict(table_codes),
                        table_id=node.table_id, semantic_groups=list(frag.semantic_groups),
                        metric_hints=list(frag.metric_hints), table_chunk_index=idx, table_chunk_count=total,
                    ))
            continue

        if isinstance(node, KeyValueNode):
            text = render_kv_node(node)
            content_type: ContentType = "key_value"
            codes = {k: c for (k, _v, c, _u) in node.pairs if c}
        elif isinstance(node, TextNode):
            text = render_text_node(node)
            content_type = "text"
            codes = {}
        else:
            continue

        if not text.strip():
            continue

        tok = estimate_tokens(text)
        if buf_texts and buf_tokens + tok > max_tokens:
            flush()
        buf_texts.append(text)
        buf_tokens += tok
        buf_types.add(content_type)
        buf_codes.update(codes)
        if buf_tokens >= target_tokens:
            flush()

    flush()
    return units
