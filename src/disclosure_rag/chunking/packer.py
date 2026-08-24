"""ContentNode 리스트를 §12 원칙(Section/Paragraph 경계 > Token 길이)에 따라
Chunk 단위로 묶는 공통 로직. 4종 Chunker 가 전부 이 위에서 동작한다.

=== Kim 브랜치 감사 결과 병합 (2026-08-25) ===

[결함 1] 예산 초과 노드를 절대 쪼개지 않았다.
    `if buf_texts and buf_tokens + tok > max_tokens: flush()` — 버퍼가 비어 있으면
    조건 자체가 False 라, 20,000 토큰짜리 노드 하나가 그대로 청크 1개가 됐다.
    Kim 실측 최대 leaf = 41,625자, CHILD_MAX_TOKENS(1000) 초과가 4.19%. 이
    outlier 는 실제 사고를 냈다 — 26,027자 청크 때문에 reranker 가 1,500쌍에
    58분 걸린 기록이 남아 있다. (우리 쪽에서도 어젯밤 재임베딩 중 MPS OOM 으로
    같은 계열의 outlier — 표 셀 안 긴 각주 — 를 만나 clip+재시도로 우회했었다.
    근본 수정은 여기다.)
    -> split_long_text() 로 문단 > 줄 > 문장 > 어절 순으로 재귀 분할한다
       (일반 text/KeyValueNode 콘텐츠에 적용).

    표 경로 관련 회귀(병합 중 Kim 의 test_properties.py 로 실제로 잡힘): 표가
    여러 chunk 로 쪼개지는 경로(len(fragments) > 1)는 sibling expansion 순서를
    지키려고 packer.add()(=split_long_text 적용 경로)를 거치지 않고 독립
    PackedUnit 으로 바로 낸다. 그런데 render_table_node_fragments 의 block 내부
    분할은 "그 block 을 이루는 행들을 다시 행 단위로 그룹핑"만 할 뿐, **행 1개
    자체가 이미 max_tokens 를 넘는 경우**(표 셀 안에 극단적으로 긴 각주가 든
    행)는 더 쪼개지 않는다 — 정확히 이 계열의 outlier(표 셀 안 긴 각주) 때문에
    우리도 어젯밤 재임베딩 중 MPS OOM 을 겪고 clip+재시도로 우회했었다. 그래서
    표 경로도 최종적으로 split_long_text() 를 한 번 더 통과시켜 상한을 보장한다
    (아래 pack_nodes 의 표 분기 참고) — "표는 예외"라고 믿었던 게 틀렸다.

[결함 2] field_codes 가 {셀 텍스트: 코드} dict 라 덮어써졌다(major 80.1% 소실).
    -> FieldRef 리스트로 바꾸고, 표가 쪼개지면 **그 조각에 실제로 등장한 셀의
       ref 만** 붙인다(_refs_in).

[결함 3] KeyValueNode 경로에서 unit_code/unit_value 를 아예 안 담았다.
    -> kv_field_refs() 로 5필드 전부 보존.

[부가] content_type 을 "key_value > table > text" 고정 우선순위가 아니라
    **문자 수 기준 우세 타입**으로 정한다(_resolve_content_type). 기존에는
    산문 위주 청크에 작은 표가 하나 섞이면 통째로 table 로 라벨링됐다.

표 semantic chunking 메타데이터(table_id/semantic_groups/metric_hints/
table_chunk_index·count, b112925)는 그대로 유지한다 — 표가 여러 chunk 로
쪼개지면(len(fragments) > 1) sibling expansion(agent/tools.py)의 순서/조인
키가 이 경계와 일치해야 하므로, 그 경우엔 주변 텍스트와 섞지 않고 표 조각을
그대로 독립 unit 으로 낸다(기존 동작 유지). 표 전체가 1 fragment 로 예산 안에
들어오면 주변 텍스트/KeyValueNode 와 자유롭게 섞는다(Kim 의 add() 경로 재사용).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from disclosure_rag.chunking.chunk_schema import (
    ContentType,
    FieldRef,
    estimate_tokens,
    kv_field_refs,
    render_kv_node,
    render_table_node_fragments,
    render_text_node,
    table_field_refs,
)
from disclosure_rag.common.doc_tree import ContentNode, KeyValueNode, TableNode, TextNode

# 재귀 분할 시 시도하는 경계 (앞에 있을수록 의미 경계에 가깝다)
_SEPARATORS = ["\n\n", "\n", ". ", "다. ", " "]


@dataclass
class PackedUnit:
    text: str
    content_type: ContentType
    field_refs: list[FieldRef] = field(default_factory=list)
    unit_hint: str | None = None
    period_hint: str | None = None
    # 표 semantic chunking 메타데이터 (b112925). 표가 아닌 unit 은 전부 기본값
    # (None/[])으로 남아 하위호환에 영향이 없다.
    table_id: str | None = None
    semantic_groups: list[str] = field(default_factory=list)
    metric_hints: list[str] = field(default_factory=list)
    table_chunk_index: int | None = None
    table_chunk_count: int | None = None


def split_long_text(text: str, max_tokens: int) -> list[str]:
    """예산을 넘는 텍스트를 의미 경계 우선으로 쪼갠다 (결함 1).

    문단 -> 줄 -> 문장 -> 어절 순으로 내려가고, 그래도 안 되면 문자 단위로 자른다.
    어떤 경우에도 **내용을 버리지 않는다** (§7 silent 손실 금지).
    """
    if estimate_tokens(text) <= max_tokens or not text:
        return [text] if text else []

    for sep in _SEPARATORS:
        if sep not in text:
            continue
        parts = [p for p in text.split(sep) if p != ""]
        if len(parts) < 2:
            continue
        out: list[str] = []
        buf: list[str] = []
        buf_tok = 0
        sep_tok = estimate_tokens(sep) if sep.strip() == "" else estimate_tokens(sep)
        for p in parts:
            p_tok = estimate_tokens(p)
            # 구분자 비용을 예산에 포함한다. 빼먹으면 줄 수가 많은 표에서
            # 조각이 상한을 최대 50%까지 넘긴다(Kim 실측: max 2,954자 / 상한 2,000자).
            if buf and buf_tok + p_tok + sep_tok > max_tokens:
                out.append(sep.join(buf))
                buf, buf_tok = [], 0
            if p_tok > max_tokens:
                # 이 조각 하나가 이미 예산 초과 -> 더 잘은 경계로 재귀
                if buf:
                    out.append(sep.join(buf))
                    buf, buf_tok = [], 0
                out.extend(split_long_text(p, max_tokens))
                continue
            buf.append(p)
            buf_tok += p_tok + (sep_tok if len(buf) > 1 else 0)
        if buf:
            out.append(sep.join(buf))
        if len(out) > 1:
            return out

    # 경계가 하나도 없는 초장문(예: 구분자 없는 숫자열) -> 문자 단위 하드 분할
    approx_chars = max(200, int(max_tokens * max(1.0, len(text) / max(1, estimate_tokens(text)))))
    return [text[i : i + approx_chars] for i in range(0, len(text), approx_chars)]


_STRUCTURED_MIN_SHARE = 0.20


def _resolve_content_type(type_chars: dict[str, int]) -> ContentType:
    """구조화 타입(key_value > table)을 우선하되, **문자 비중이 유의미할 때만** 그렇게 한다.

    기존은 우선순위 고정이라 산문 위주 청크에 작은 표 하나가 섞이면 통째로 table 로
    라벨링됐다. 반대로 순수 우세타입으로 바꾸면 폼 문서(계약공시)가 단서조항 서술
    때문에 text 로 라벨링돼 구조 정보를 잃는다. -> 구조화 부분이 20% 이상이면
    구조화로 본다.
    """
    if not type_chars:
        return "text"
    total = sum(type_chars.values()) or 1
    for t in ("key_value", "table"):
        if type_chars.get(t, 0) / total >= _STRUCTURED_MIN_SHARE:
            return t  # type: ignore[return-value]
    return max(type_chars.items(), key=lambda kv: kv[1])[0]  # type: ignore[return-value]


def _refs_in(refs: list[FieldRef], piece: str) -> list[FieldRef]:
    """표/kv 가 여러 조각으로 쪼개졌을 때, 그 조각에 실제로 등장한 셀의 ref 만 남긴다
    (기존처럼 조각마다 전체를 복사하면 엔트리가 부풀어 소실률만 낮추고 노이즈가 는다).
    텍스트가 비어 있는 ref 는 위치를 확인할 수 없으므로 그대로 둔다."""
    out = []
    for r in refs:
        if not r.text or r.text in piece:
            out.append(r)
    return out


def pack_nodes(
    nodes: list[ContentNode],
    *,
    target_tokens: int = 600,
    max_tokens: int = 1000,
    table_max_rows: int = 20,
    table_max_tokens: int | None = None,
    table_style: str = "grid",
) -> list[PackedUnit]:
    """ContentNode 들을 chunk 후보(PackedUnit)로 묶는다.

    table_max_rows / table_max_tokens 를 크게 주면 표를 쪼개지 않는다(parent 렌더용).
    """
    if table_max_tokens is None:
        table_max_tokens = max_tokens

    units: list[PackedUnit] = []
    buf_texts: list[str] = []
    buf_tokens = 0
    buf_type_chars: dict[str, int] = {}
    buf_refs: list[FieldRef] = []
    buf_unit: str | None = None
    buf_period: str | None = None
    # 버퍼에 병합된(=자기 혼자 예산 안에 들어가 다른 content 와 섞인) 표의 메타데이터.
    # 버퍼 하나에 서로 다른 표가 둘 이상 섞이는 드문 경우는 마지막 표 기준으로
    # 덮어쓴다 — 어차피 그 표들은 table_chunk_count=1(형제가 없는) 표들이라
    # sibling expansion 대상이 아니므로 실질적 정보 손실은 없다.
    buf_table_id: str | None = None
    buf_semantic_groups: list[str] = []
    buf_metric_hints: list[str] = []

    def flush() -> None:
        nonlocal buf_texts, buf_tokens, buf_type_chars, buf_refs, buf_unit, buf_period
        nonlocal buf_table_id, buf_semantic_groups, buf_metric_hints
        if not buf_texts:
            return
        units.append(
            PackedUnit(
                text="\n\n".join(buf_texts),
                content_type=_resolve_content_type(buf_type_chars),
                field_refs=list(buf_refs),
                unit_hint=buf_unit,
                period_hint=buf_period,
                table_id=buf_table_id,
                semantic_groups=list(buf_semantic_groups),
                metric_hints=list(buf_metric_hints),
                table_chunk_index=1 if buf_table_id else None,
                table_chunk_count=1 if buf_table_id else None,
            )
        )
        buf_texts, buf_tokens, buf_type_chars, buf_refs = [], 0, {}, []
        buf_unit, buf_period = None, None
        buf_table_id, buf_semantic_groups, buf_metric_hints = None, [], []

    def add(
        text: str, ctype: str, refs: list[FieldRef], unit: str | None = None, period: str | None = None,
        table_id: str | None = None, semantic_groups: list[str] | None = None, metric_hints: list[str] | None = None,
    ) -> None:
        """예산을 지키며 버퍼에 넣는다. 한 조각이 예산을 넘으면 먼저 쪼갠다(결함 1 수정)."""
        nonlocal buf_tokens, buf_unit, buf_period, buf_table_id, buf_semantic_groups, buf_metric_hints
        if not text.strip():
            return
        for piece in split_long_text(text, max_tokens):
            tok = estimate_tokens(piece)
            if buf_texts and buf_tokens + tok > max_tokens:
                flush()
            buf_texts.append(piece)
            buf_tokens += tok
            buf_type_chars[ctype] = buf_type_chars.get(ctype, 0) + len(piece)
            buf_refs.extend(_refs_in(refs, piece))
            if unit and buf_unit is None:
                buf_unit = unit
            if period and buf_period is None:
                buf_period = period
            if table_id:
                buf_table_id = table_id
                buf_semantic_groups = list(semantic_groups or [])
                buf_metric_hints = list(metric_hints or [])
            if buf_tokens >= target_tokens:
                flush()

    for node in nodes:
        if isinstance(node, TableNode):
            refs = table_field_refs(node)
            fragments = render_table_node_fragments(
                node, max_rows_per_chunk=table_max_rows, max_tokens_per_chunk=table_max_tokens,
                style=table_style,  # type: ignore[arg-type]
            )
            if not fragments:
                continue
            table_texts = [f.text for f in fragments]
            if len(fragments) == 1 and buf_tokens + estimate_tokens(table_texts[0]) <= max_tokens:
                # 작은 표(전체가 1 fragment) 는 주변 텍스트/KeyValueNode 와 자유롭게 섞는다.
                add(
                    table_texts[0], "table", refs, node.unit_hint, node.period_hint,
                    table_id=node.table_id, semantic_groups=fragments[0].semantic_groups,
                    metric_hints=fragments[0].metric_hints,
                )
            else:
                # 표가 여러 chunk 로 쪼개진 경우: 그 자체로 독립 chunk(들)로 분리한다
                # (sibling expansion 의 순서/조인 키가 되는 table_chunk_index/count 가
                # 이 경계와 일치해야 하므로, 주변 텍스트와 섞지 않는다).
                #
                # 안전망: render_table_node_fragments 는 "block 을 이루는 행들의
                # 묶음" 단위로만 예산을 지키므로, 행 1개 자체가 이미 max_tokens 를
                # 넘는 극단적 케이스(표 셀 안의 긴 각주)는 fragment 안에서 더
                # 쪼개지 않는다. 그래서 각 fragment 를 여기서 한 번 더
                # split_long_text() 에 통과시킨다 — 상한이 '상한'이 되도록 보장
                # (같은 fragment 에서 나온 조각들은 같은 table_chunk_index/count 를
                # 공유한다; sibling 연결은 table_id + chunks 리스트 순서로만
                # 이뤄지므로(_link_table_chunk_siblings) 정확성에 영향 없다).
                flush()
                table_pieces: list[tuple[str, object]] = [
                    (piece, frag) for frag in fragments for piece in split_long_text(frag.text, max_tokens)
                ]
                total = len(table_pieces)
                for idx, (piece, frag) in enumerate(table_pieces, start=1):
                    units.append(
                        PackedUnit(
                            text=piece, content_type="table",
                            field_refs=_refs_in(refs, piece),
                            unit_hint=node.unit_hint, period_hint=node.period_hint,
                            table_id=node.table_id, semantic_groups=list(frag.semantic_groups),
                            metric_hints=list(frag.metric_hints), table_chunk_index=idx, table_chunk_count=total,
                        )
                    )
        elif isinstance(node, KeyValueNode):
            add(render_kv_node(node), "key_value", kv_field_refs(node))
        elif isinstance(node, TextNode):
            add(render_text_node(node), "text", [])

    flush()
    return units
