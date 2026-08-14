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
