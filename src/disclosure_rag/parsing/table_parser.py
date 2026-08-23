"""공통 Table 처리 (§21).

DART XML <TABLE> (TH/TD/TE/TU 셀) 과 exchange 의 위장 HTML <table> (<td>/<span>) 은
태그 어휘는 다르지만, 둘 다 "rowspan/colspan 이 섞인 2차원 그리드"라는 점은 동일하다.

그래서 두 원본 모두 먼저 RawCell 의 2차원 리스트(행 단위)로 변환한 뒤,
동일한 expand_grid() 로 rowspan/colspan 을 채워 넣은 정규 그리드를 만들고,
동일한 classify_grid() 로 KeyValueNode / TableNode 를 결정한다.

절대 금지: Table 을 그냥 순서대로 이어붙여 plain text 로 flatten 하는 것 (금지 사항 #3).
Column-Value 관계, rowspan 으로 묶인 상위 라벨 관계를 반드시 보존한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from disclosure_rag.common.doc_tree import KeyValueNode, TableCell, TableNode

KEY_VALUE_MAX_COLS = 3  # 이보다 열이 많으면 grid(table)로 취급


@dataclass
class RawCell:
    text: str
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False
    field_code: str | None = None
    unit_code: str | None = None
    unit_value: str | None = None
    # 원본 셀 텍스트의 leading whitespace 길이. strip 전에 계산해서 넘겨받는다
    # (semantic block 들여쓰기 판단용, Phase 1). 계산할 수 없는 원본(예: exchange
    # 의 위장 HTML)은 0 으로 둔다 — 없어도 번호/계층 패턴 신호만으로 동작한다.
    indent: int = 0


@dataclass
class _GridCell:
    origin_id: int
    cell: TableCell


def expand_grid(raw_rows: list[list[RawCell]]) -> list[list[_GridCell]]:
    """rowspan/colspan 을 채워 넣어 모든 행의 길이가 같은 정규 그리드로 만든다."""
    grid: list[list[_GridCell]] = []
    pending: dict[int, tuple[int, _GridCell]] = {}  # col -> (remaining_rowspan, gridcell)
    next_id = 0

    for raw_row in raw_rows:
        row: list[_GridCell] = []
        col = 0
        idx = 0
        while idx < len(raw_row) or col in pending:
            if col in pending:
                remaining, gc = pending[col]
                row.append(gc)
                if remaining - 1 > 0:
                    pending[col] = (remaining - 1, gc)
                else:
                    del pending[col]
                col += 1
                continue
            raw = raw_row[idx]
            idx += 1
            gc = _GridCell(
                origin_id=next_id,
                cell=TableCell(
                    text=raw.text.strip(),
                    is_header=raw.is_header,
                    field_code=raw.field_code,
                    unit_code=raw.unit_code,
                    unit_value=raw.unit_value,
                    indent=raw.indent,
                    origin_id=next_id,
                ),
            )
            next_id += 1
            for c in range(max(raw.colspan, 1)):
                row.append(gc)
                if raw.rowspan > 1:
                    pending[col + c] = (raw.rowspan - 1, gc)
            col += max(raw.colspan, 1)
        grid.append(row)

    max_cols = max((len(r) for r in grid), default=0)
    for row in grid:
        while len(row) < max_cols:
            row.append(_GridCell(origin_id=-1, cell=TableCell(text="")))
    return grid


def _rle(row: list[_GridCell]) -> list[_GridCell]:
    """같은 origin_id(=같은 원본 셀)가 rowspan/colspan 으로 반복된 것을 1개로 축약."""
    out: list[_GridCell] = []
    for gc in row:
        if not out or out[-1].origin_id != gc.origin_id:
            out.append(gc)
    return out


def classify_grid(
    grid: list[list[_GridCell]],
    *,
    title_hint: str | None = None,
    unit_hint: str | None = None,
    acode_group: str | None = None,
) -> list[TableNode | KeyValueNode]:
    """정규 그리드를 KeyValueNode(들) 또는 TableNode 로 분류한다.

    휴리스틱 (§21):
    - 열 수가 적고(<=KEY_VALUE_MAX_COLS) 행마다 의미가 완결되는 표(대부분의 major/
      exchange 표, periodic/holding 의 소규모 요약표) -> 행 단위 KeyValueNode.
      rowspan 으로 묶인 첫 컬럼은 group_label 로 별도 보존한다.
    - 열이 많은 grid(재무제표, 임원현황처럼 헤더+다수 행) -> TableNode 그대로 보존.
    """
    # rowspan/colspan 로 같은 원본 셀이 여러 grid 칸에 중복 채워진 것을 먼저
    # 행 단위로 축약(RLE)한다. 이걸 먼저 하지 않으면 colspan=3 짜리 값 셀 하나가
    # 3열로 보여 실제로는 3칸짜리 key-value 표인데 grid(TableNode)로 잘못
    # 분류되고, 렌더링 시 같은 텍스트가 반복 출력되는 문제가 있었다 (회귀 발견).
    rle_rows = [_rle(row) for row in grid]
    max_cols = max((len(r) for r in rle_rows), default=0)
    if max_cols == 0:
        return []

    if max_cols > KEY_VALUE_MAX_COLS:
        rows = [[gc.cell for gc in row] for row in rle_rows]
        return [TableNode(rows=rows, title_hint=title_hint, unit_hint=unit_hint, acode_group=acode_group)]

    nodes: list[KeyValueNode] = []
    prev_group_origin: int | None = None
    current_kv: KeyValueNode | None = None

    for row in rle_rows:
        cells = [c for c in row if c.cell.text or c.origin_id != -1]
        if not cells or all(not c.cell.text for c in cells):
            continue  # 빈 spacer row (실측: 표 첫 행에 흔함)

        if len(cells) >= 3:
            group_cell, key_cell, *value_cells = cells
            value_text = " / ".join(c.cell.text for c in value_cells if c.cell.text)
            if group_cell.origin_id != prev_group_origin or current_kv is None:
                current_kv = KeyValueNode(group_label=group_cell.cell.text or None, acode_group=acode_group)
                nodes.append(current_kv)
                prev_group_origin = group_cell.origin_id
            current_kv.pairs.append(
                (key_cell.cell.text, value_text, key_cell.cell.field_code or value_cells[-1].cell.field_code if value_cells else key_cell.cell.field_code, value_cells[-1].cell.unit_code if value_cells else None)
            )
        elif len(cells) == 2:
            key_cell, value_cell = cells
            if current_kv is None or current_kv.group_label is not None:
                current_kv = KeyValueNode(group_label=None, acode_group=acode_group)
                nodes.append(current_kv)
                prev_group_origin = None
            current_kv.pairs.append(
                (key_cell.cell.text, value_cell.cell.text, value_cell.cell.field_code, value_cell.cell.unit_code)
            )
        else:
            # 1열짜리 행(구분선/타이틀류) -> 텍스트로 흘려보내되 별도 처리 없이 스킵
            continue

    return nodes if nodes else []


# ---------------------------------------------------------------------------
# Semantic Block Detector (Phase 1, chunk_schema.render_table_node 회귀 수정)
# ---------------------------------------------------------------------------
#
# 실제 재현 사례(SK하이닉스 사업보고서 20260317000635.xml, "192,972,588" 검색):
#
#     계          | 192,972,588 | 129,960,534 | 67,573,636    <- "1. 매출액" 그룹의 합계
#     2. 영업이익  |             |             |
#         연결조정 |      32,476 |    (182,030)|      86,230
#         계       |  47,206,319 |  23,467,319 | (7,730,313)  <- "2. 영업이익" 그룹의 합계
#
# 기존 render_table_node() 는 max_rows_per_chunk/max_tokens_per_chunk 같은
# 순수 행count/토큰 기준으로만 body_rows 를 잘라, "1. 매출액" 그룹과
# "2. 영업이익" 그룹이 서로 다른 chunk 로 갈라질 수 있었다(정답이 다음 chunk
# 에 있어 단일 chunk 검색으로는 못 찾음). 이 함수는 그 전에 "의미적으로 같이
# 있어야 하는 행 묶음(semantic block)"을 먼저 식별해, packer 가 block 단위로
# chunk 를 나눌 수 있게 한다.
#
# 판단 신호(전부 deterministic, LLM 사용 안 함 — §12 원칙 9):
#   (a) 각 행의 "라벨 셀"(첫 번째 비어있지 않은 셀) 텍스트
#   (b) rowspan 확장으로 같은 원본 셀이 여러 행에 반복되면(origin_id 동일)
#       무조건 같은 block (예: "2. 투자내역"이 rowspan=4 로 4행에 걸친 경우)
#   (c) "1." / "1)" / "(1)" / "가." / "(가)" / "I." 류 번호·계층 표기가 라벨
#       셀에 나타나는지
#   (d) 라벨 셀의 들여쓰기 폭(TableCell.indent) — 번호 표기가 없는 하위 행도
#       상위 항목보다 더 들여써져 있는 경우가 실측으로 흔하다(연결조정/계)
#   (e) 완전히 빈 라벨 셀(spacer row)은 직전 block 에 그대로 붙인다
#
# 번호도 들여쓰기도 없는 평평한 표(예: 삼성SDI 손익계산서처럼 각 행이 완결된
# 항목)는 모든 행을 각자 독립 block 으로 반환해 기존 동작(행 단위 처리)과
# 동일하게 유지한다 — "정규식 하나로 모든 표를 해결하려 하지 않는다"(§12
# 원칙 10)는 원칙에 따라, 신호가 전혀 없는 표는 억지로 묶지 않는다.

_KOREAN_ENUM_SYLLABLES = "가나다라마바사아자차카타파하"

_NUMBERING_PATTERNS = [
    re.compile(r"^\d{1,3}[.)](?=\s|[^\d]|$)"),          # "1. " / "1)" / "12."
    re.compile(r"^\(\d{1,3}\)"),                          # "(1)"
    re.compile(rf"^[{_KOREAN_ENUM_SYLLABLES}][.)](?=\s|[^0-9]|$)"),  # "가." / "나)"
    re.compile(rf"^\([{_KOREAN_ENUM_SYLLABLES}]\)"),      # "(가)"
    re.compile(r"^[IVXLCDM]{1,4}[.)](?=\s|$)"),           # "I." / "IV)" (ASCII 로마 숫자)
    re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ][.)]"),                  # "Ⅰ." (유니코드 로마 숫자, 재무제표에 흔함)
]


def _is_numbered_label(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    return any(p.match(t) for p in _NUMBERING_PATTERNS)


def strip_numbering_prefix(text: str) -> str:
    """metric_hint 추출용: "1. 매출액" -> "매출액". 매칭 안 되면 원문 그대로."""
    t = text.strip()
    for p in _NUMBERING_PATTERNS:
        m = p.match(t)
        if m:
            return t[m.end():].strip(" .)")
    return t


def _label_info(row: list[TableCell]) -> tuple[str, int, int]:
    """행의 "라벨 셀"(첫 번째 비어있지 않은 셀) 텍스트/들여쓰기/origin_id 를 반환.
    행 전체가 빈 텍스트면 ("", 0, -1)."""
    for c in row:
        if c.text.strip():
            return c.text.strip(), c.indent, c.origin_id
    return "", 0, -1


def detect_semantic_blocks(body_rows: list[list[TableCell]]) -> list[list[int]]:
    """body_rows(헤더 제외 본문 행들)를 semantic block(행 index 묶음)으로 나눈다.

    반환값은 body_rows 에 대한 0-based row index 의 리스트의 리스트 — 원소
    순서는 원본 행 순서를 그대로 보존한다(재정렬 없음).
    """
    n = len(body_rows)
    if n == 0:
        return []

    infos = [_label_info(r) for r in body_rows]
    numbered_flags = [_is_numbered_label(t) for t, _i, _o in infos]
    has_numbering = any(numbered_flags)
    indents = [i for _t, i, _o in infos]
    has_indent_signal = len(set(indents)) > 1

    if not has_numbering and not has_indent_signal:
        # 구조 신호가 전혀 없는 평평한 표 -> 기존 동작과 동일하게 행 단위 그대로.
        return [[i] for i in range(n)]

    blocks: list[list[int]] = [[0]]
    block_baseline_indent = infos[0][1]

    for idx in range(1, n):
        text, indent, origin = infos[idx]
        _prev_text, _prev_indent, prev_origin = infos[idx - 1]

        if text == "":
            # 완전히 빈 행(spacer) -> 직전 block 에 그대로 붙인다.
            blocks[-1].append(idx)
            continue

        if origin != -1 and origin == prev_origin:
            # rowspan 으로 반복된 동일 원본 셀 -> 번호/들여쓰기 판단과 무관하게
            # 항상 같은 block (예: "2. 투자내역"이 rowspan 으로 여러 행에 반복).
            blocks[-1].append(idx)
            continue

        starts_new = False
        if has_numbering:
            if numbered_flags[idx] and indent <= block_baseline_indent:
                starts_new = True
        else:  # has_indent_signal 만 있는 경우 (번호 표기가 아예 없는 표)
            global_min_indent = min(indents)
            if indent == global_min_indent and any(
                infos[j][1] > global_min_indent for j in blocks[-1]
            ):
                starts_new = True

        if starts_new:
            blocks.append([idx])
            block_baseline_indent = indent
        else:
            blocks[-1].append(idx)

    return blocks
