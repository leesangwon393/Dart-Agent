"""공통 Table 처리 (§21).

DART XML <TABLE> (TH/TD/TE/TU 셀) 과 exchange 의 위장 HTML <table> (<td>/<span>) 은
태그 어휘는 다르지만, 둘 다 "rowspan/colspan 이 섞인 2차원 그리드"라는 점은 동일하다.

그래서 두 원본 모두 먼저 RawCell 의 2차원 리스트(행 단위)로 변환한 뒤,
동일한 expand_grid() 로 rowspan/colspan 을 채워 넣은 정규 그리드를 만들고,
동일한 classify_grid() 로 KeyValueNode / TableNode / TextNode 를 결정한다.

절대 금지: Table 을 그냥 순서대로 이어붙여 plain text 로 flatten 하는 것 (금지 사항 #3).
Column-Value 관계, rowspan 으로 묶인 상위 라벨 관계를 반드시 보존한다.

=== Kim 브랜치 감사 결과 병합 (2026-08-25) ===

[A] **정규 그리드를 유지한다.**
    기존: expand_grid 로 펼친 뒤 _rle() 로 다시 축약해 TableNode.rows 에 저장.
          -> 본문 행의 53.1%가 헤더와 열 수 불일치(Kim 실측, 그런 표가 61.0%).
             " | " 로 렌더링하면 "3번째 필드 = 헤더 3번째 열"이 과반에서 깨진다.
    변경: TableNode.rows 는 펼친 그리드 그대로(모든 행 열 수 동일). 반복 복제된
          칸은 dup_left/dup_up 플래그만 세워두고 렌더링에서만 빈칸으로 낸다.
          KV/Table 분류 판단은 종전처럼 RLE 뷰(`_rle()`)에서 한다.
    통합: Kim 은 origin_id 를 `_GridCell` 래퍼로 따로 관리했지만, 우리는 어젯밤
          (b112925) semantic block 검출을 위해 `TableCell.origin_id` 필드를 이미
          추가해뒀다 — 개념이 동일하므로(rowspan 확장 시 같은 원본 셀이면 값이
          같음) 래퍼를 없애고 `TableCell` 필드로 흡수해 하나로 통합했다.

[B] **1열짜리 행을 더 이상 버리지 않는다.**
    기존: `else: continue` — 주석은 "구분선/타이틀류"라 했지만 DART/KRX 서식에서
          전폭 1열 행은 서술형 본문 자리다. Kim 실측 폐기율:
            periodic 18.8%(63만자) / holding 58.1% / major 17.6% / exchange 13.7%
          사라진 것: "(단위: 백만원)", "제 41 기 1분기말 2024.03.31 현재",
                     "11. 기타 투자판단에 참고할 사항 …", 보유목적 서술
    변경: TextNode(from_table_row=True) 로 문서 순서 그대로 보존한다.

[C] **unit_hint / period_hint 를 실제로 채운다.**
    기존: unit_hint 가 전 코퍼스에서 항상 None(표 23,320개 중 0개) — 단위 삽입
          로직이 죽은 코드였고, 금액 청크가 "원인지 백만원인지" 없이 인덱싱됐다.
          (호출부 dart_xml_parser.py 가 classify_grid 의 unit_hint 파라미터에
          한 번도 값을 넘기지 않아서 죽어 있었다.)
    변경: 표 안/직전의 단위·기수 표기를 정규식으로 찾아 hint 로 승격한다(_scan_hints).

=== 어젯밤 변경분(b112925, 2026-08-24) 은 그대로 유지 ===
[D] detect_semantic_blocks()/strip_numbering_prefix(): 번호/계층 표기 + 셀
    들여쓰기(TableCell.indent) + rowspan 반복(TableCell.origin_id) 3가지
    deterministic 신호로 "1. 매출액 ... 계" 처럼 상위항목+하위행 semantic block
    경계를 찾는다 — chunk_schema.render_table_node_fragments() 가 block 단위로
    패킹해 "1. 매출액 계"와 "2. 영업이익 계"가 서로 다른 chunk 로 갈라지는 회귀를
    막는다.

    dup 칸 안전성 점검(Phase 2 병합 시 재확인): `_label_info()`는 행의 "첫 번째
    비어있지 않은 셀"을 라벨로 쓴다. dup_left 셀은 항상 같은 행의 원본 셀보다
    뒤에 오므로(원본이 열 0 에 있고 colspan 으로 오른쪽에 복제) 라벨 탐색에
    영향이 없다. dup_up 셀(rowspan 반복)이 행의 첫 셀인 경우는 origin_id 를
    원본과 동일하게 그대로 이어받으므로(expand_grid 참고), detect_semantic_blocks
    의 "origin == prev_origin -> 같은 block" 규칙이 자연스럽게 적용돼 별도
    처리가 필요 없다 — 실제로 test_detect_semantic_blocks_rowspan_repeat_
    stays_one_block 이 이 경로를 검증한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from disclosure_rag.common.doc_tree import (
    ContentNode,
    KeyValueNode,
    KVPair,
    TableCell,
    TableNode,
    TextNode,
)

KEY_VALUE_MAX_COLS = 3  # 이보다 열이 많으면 grid(table)로 취급

# 표 안에서 "단위" / "기수·기준일" 표기를 식별하는 패턴 (실측 문자열 기반).
#   "(단위: 백만원)"  "(원화단위: 백만원, 외화단위: 외화 천단위)"  "단위 : 원"
_UNIT_RE = re.compile(r"(?:^|[\s(（])[가-힣]{0,4}단위\s*[::]")
#   "제 41 기 1분기말 2024.03.31 현재"  "제41기"  "2024.03.31 현재"
_PERIOD_RE = re.compile(r"(제\s*\d+\s*기)|(\d{4}[.\-]\d{2}[.\-]\d{2}\s*(현재|기준))")
_MAX_HINT_LEN = 120  # 이보다 길면 본문이지 hint 가 아니다


def looks_like_unit_line(text: str) -> bool:
    t = text.strip()
    return bool(t) and len(t) <= _MAX_HINT_LEN and bool(_UNIT_RE.search(t))


def looks_like_period_line(text: str) -> bool:
    t = text.strip()
    return bool(t) and len(t) <= _MAX_HINT_LEN and bool(_PERIOD_RE.search(t))


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
    # (semantic block 들여쓰기 판단용). 계산할 수 없는 원본(예: exchange 의 위장
    # HTML)은 0 으로 둔다 — 없어도 번호/계층 패턴 신호만으로 동작한다.
    indent: int = 0


def expand_grid(raw_rows: list[list[RawCell]]) -> list[list[TableCell]]:
    """rowspan/colspan 을 채워 넣어 모든 행의 길이가 같은 정규 그리드로 만든다.

    각 칸에 (row, col) 좌표와 dup_left/dup_up 플래그, origin_id(rowspan/colspan
    확장 전 "원본 셀" 식별자)를 심는다. dup_* 는 "같은 원본 셀이 span 때문에 이
    칸에도 복제됐다"는 표시이며, 렌더링에서 빈칸으로 처리해 **열 수는 유지하면서
    텍스트 중복만 없앤다**.
    """
    grid: list[list[TableCell]] = []
    pending: dict[int, tuple[int, TableCell]] = {}  # col -> (remaining_rowspan, origin_cell)
    next_id = 0

    for raw_row in raw_rows:
        row: list[TableCell] = []
        col = 0
        idx = 0
        while idx < len(raw_row) or col in pending:
            if col in pending:
                remaining, origin = pending[col]
                # rowspan 복제본은 원본 TableCell 을 공유하면 안 된다(좌표가 달라짐).
                dup = TableCell(
                    text=origin.text, is_header=origin.is_header,
                    field_code=origin.field_code, unit_code=origin.unit_code,
                    unit_value=origin.unit_value, indent=origin.indent,
                    origin_id=origin.origin_id, dup_up=True,
                )
                row.append(dup)
                if remaining - 1 > 0:
                    pending[col] = (remaining - 1, origin)
                else:
                    del pending[col]
                col += 1
                continue
            raw = raw_row[idx]
            idx += 1
            span = max(raw.colspan, 1)
            for c in range(span):
                cell = TableCell(
                    text=raw.text.strip(), is_header=raw.is_header,
                    field_code=raw.field_code, unit_code=raw.unit_code,
                    unit_value=raw.unit_value, indent=raw.indent,
                    origin_id=next_id, dup_left=(c > 0),
                )
                row.append(cell)
                if raw.rowspan > 1:
                    pending[col + c] = (raw.rowspan - 1, cell)
            next_id += 1
            col += span
        grid.append(row)

    max_cols = max((len(r) for r in grid), default=0)
    for r_i, row in enumerate(grid):
        while len(row) < max_cols:
            row.append(TableCell(text=""))
        for c_i, cell in enumerate(row):
            cell.row = r_i
            cell.col = c_i
    return grid


def _rle(row: list[TableCell]) -> list[TableCell]:
    """같은 origin_id(=같은 원본 셀)가 rowspan/colspan 으로 반복된 것을 1개로 축약.
    **분류 판단에만 쓴다** — 저장되는 TableNode.rows 는 정규 그리드를 유지한다."""
    out: list[TableCell] = []
    for cell in row:
        if not out or out[-1].origin_id != cell.origin_id:
            out.append(cell)
    return out


def _scan_hints(grid: list[list[TableCell]]) -> tuple[str | None, str | None]:
    """표 안에 흩어져 있는 단위/기수 표기를 찾아 hint 로 승격한다 (변경점 [C])."""
    unit_hint: str | None = None
    period_hint: str | None = None
    seen: set[int] = set()
    for row in grid:
        for cell in row:
            if cell.origin_id in seen or cell.origin_id < 0:
                continue
            seen.add(cell.origin_id)
            t = cell.text.strip()
            if not t:
                continue
            if unit_hint is None and looks_like_unit_line(t):
                unit_hint = t
            if period_hint is None and looks_like_period_line(t):
                period_hint = t
        if unit_hint and period_hint:
            break
    return unit_hint, period_hint


def classify_grid(
    grid: list[list[TableCell]],
    *,
    title_hint: str | None = None,
    unit_hint: str | None = None,
    acode_group: str | None = None,
) -> list[ContentNode]:
    """정규 그리드를 KeyValueNode / TableNode / TextNode 로 분류한다.

    휴리스틱 (§21):
    - 열 수가 적고(<=KEY_VALUE_MAX_COLS) 행마다 의미가 완결되는 표 -> 행 단위 KeyValueNode.
      rowspan 으로 묶인 첫 컬럼은 group_label 로 별도 보존한다.
    - 열이 많은 grid(재무제표, 임원현황) -> TableNode 그대로 보존(정규 그리드 유지, 변경점 [A]).
    - **1열짜리 전폭 행 -> TextNode 로 보존** (변경점 [B]. 기존에는 폐기됐다.)
    """
    # 분류 판단은 RLE 뷰에서 한다: colspan=3 짜리 값 셀 하나가 3열로 보여 실제로는
    # 3칸짜리 key-value 표인데 grid 로 잘못 분류되던 회귀를 그대로 방지한다.
    rle_rows = [_rle(row) for row in grid]
    max_cols = max((len(r) for r in rle_rows), default=0)
    if max_cols == 0:
        return []

    scanned_unit, scanned_period = _scan_hints(grid)

    if max_cols > KEY_VALUE_MAX_COLS:
        # 정규 그리드를 그대로 보존한다 (변경점 [A]).
        return [
            TableNode(
                rows=grid,
                title_hint=title_hint,
                unit_hint=unit_hint or scanned_unit,
                period_hint=scanned_period,
                acode_group=acode_group,
            )
        ]

    nodes: list[ContentNode] = []
    prev_group_origin: int | None = None
    current_kv: KeyValueNode | None = None

    def _new_kv(group_label: str | None) -> KeyValueNode:
        kv = KeyValueNode(group_label=group_label, acode_group=acode_group)
        nodes.append(kv)
        return kv

    for row in rle_rows:
        cells = [c for c in row if c.text or c.origin_id != -1]
        if not cells or all(not c.text for c in cells):
            continue  # 빈 spacer row (실측: 표 첫 행에 흔함)

        if len(cells) >= 3:
            group_cell, key_cell, *value_cells = cells
            value_text = " / ".join(c.text for c in value_cells if c.text)
            if group_cell.origin_id != prev_group_origin or current_kv is None:
                current_kv = _new_kv(group_cell.text or None)
                prev_group_origin = group_cell.origin_id
            last = value_cells[-1] if value_cells else key_cell
            current_kv.pairs.append(
                KVPair(
                    key=key_cell.text,
                    value=value_text,
                    field_code=key_cell.field_code or last.field_code,
                    unit_code=last.unit_code or key_cell.unit_code,
                    unit_value=last.unit_value or key_cell.unit_value,
                )
            )
        elif len(cells) == 2:
            key_cell, value_cell = cells
            if current_kv is None or current_kv.group_label is not None:
                current_kv = _new_kv(None)
                prev_group_origin = None
            current_kv.pairs.append(
                KVPair(
                    key=key_cell.text,
                    value=value_cell.text,
                    field_code=value_cell.field_code or key_cell.field_code,
                    unit_code=value_cell.unit_code,
                    unit_value=value_cell.unit_value,
                )
            )
        else:
            # === 변경점 [B]: 1열짜리 전폭 행을 보존한다 ===
            text = cells[0].text.strip()
            if not text:
                continue
            nodes.append(TextNode(text=text, from_table_row=True))
            # 이 줄 다음부터는 새 key-value 블록으로 본다(서술문이 블록을 가른다).
            current_kv = None
            prev_group_origin = None

    return nodes


# ---------------------------------------------------------------------------
# Semantic Block Detector (b112925, chunk_schema.render_table_node 회귀 수정)
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
#       무조건 같은 block (예: "2. 투자내역"이 rowspan=4 로 4행에 걸친 경우) —
#       Kim 의 dup_up 셀도 origin_id 를 원본과 동일하게 물려받으므로 그대로 적용된다.
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
    행 전체가 빈 텍스트면 ("", 0, -1).

    dup 칸 안전성: dup_left 셀은 같은 행에서 원본(열 0)보다 항상 뒤에 오므로
    라벨 탐색 순서에 영향이 없다. dup_up 셀(rowspan 반복)이 행의 첫 셀라면
    origin_id 를 원본과 동일하게 물려받으므로, 아래 detect_semantic_blocks 의
    "origin == prev_origin -> 같은 block" 규칙이 자연스럽게 적용된다(별도 분기
    불필요 — dup 칸을 "새 라벨"로 오인하지 않는다)."""
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
