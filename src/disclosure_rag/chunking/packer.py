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
    render_table_node,
    render_text_node,
)


@dataclass
class PackedUnit:
    text: str
    content_type: ContentType
    field_codes: dict[str, str] = field(default_factory=dict)


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

    def flush() -> None:
        nonlocal buf_texts, buf_tokens, buf_types, buf_codes
        if not buf_texts:
            return
        content_type: ContentType = "key_value" if "key_value" in buf_types else ("table" if "table" in buf_types else "text")
        units.append(PackedUnit(text="\n\n".join(buf_texts), content_type=content_type, field_codes=dict(buf_codes)))
        buf_texts, buf_tokens, buf_types, buf_codes = [], 0, set(), {}

    for node in nodes:
        if isinstance(node, TableNode):
            table_texts = render_table_node(node)
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
                if buf_tokens >= target_tokens:
                    flush()
            else:
                flush()  # 큰 표는 그 자체로 독립 chunk(들)로 분리
                # 단순화: 분할된 각 조각에 표 전체의 field_codes 를 동일하게 붙인다
                # (완벽하게 조각별로 scoping 하는 대신, 값을 누락시키지 않는 쪽을 택함).
                for t in table_texts:
                    units.append(PackedUnit(text=t, content_type="table", field_codes=dict(table_codes)))
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
