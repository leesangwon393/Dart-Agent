# Kim 브랜치에 semantic block table chunking 이식 요청

> 상원님 → Kim: 이 문서 전체를 Claude Code(또는 쓰시는 AI 코딩 에이전트)에 그대로
> 붙여넣고 시작하시면 됩니다. 실제 우리 리포에서 검증까지 끝낸 참조 구현이 아래에
> 그대로 들어있습니다.

## 배경

`pipeline-kim`(RLE→dup_left/dup_up 그리드 보존, 1열 행 보존, field_codes→FieldRef,
xml_sanitizer 등)을 상원님 쪽에 병합해서 잘 쓰고 있습니다. 근데 병합하면서 확인해보니
**`pipeline-kim`의 `render_table_node()`가 여전히 순수 `max_rows_per_chunk=20`/
`max_tokens_per_chunk=1000` 행count 기준으로만 표를 자르고 있어서**, 표 안에서
의미상 붙어있어야 할 상위항목(예: "1. 매출액...계")과 다음 항목("2. 영업이익...계")이
chunk 경계로 갈라지는 버그가 그대로 있습니다.

## 실측 재현 (Kim 코드를 수정 없이 그대로 돌려서 확인함)

SK하이닉스 사업보고서(`corpus/raw/periodic/SK하이닉스/20260317000635_annual_2025_12/
20260317000635.xml`)의 "지역별 재무 정보" 표(47행)를 파싱해서, **동일한 TableNode
객체**를 두 코드에 각각 넣어봄:

```
[우리(semantic block 적용) 코드]
조각 개수: 1
  조각 1: 매출액계=O  영업이익계=O   <- 둘 다 같은 chunk

[pipeline-kim 코드, 수정 없이 그대로]
조각 개수: 3
  조각 1: 매출액계=X  영업이익계=X
  조각 2: 매출액계=O  영업이익계=X   <- "1.매출액 계"(192,972,588)
  조각 3: 매출액계=X  영업이익계=O   <- "2.영업이익 계"(47,206,319, 정답)는 딴 조각
```

"SK하이닉스 2025년 영업이익은 얼마야?" 같은 질문에서 정답이 있는 조각이 top-k 검색
결과 밖으로 밀려서 답을 못 찾는 실제 원인이었습니다.

## 요청

`pipeline-kim`의 `chunk_schema.render_table_node()`를 아래 참조 구현(우리 리포에서
136개 테스트 전부 통과 + 위 재현 케이스로 검증 완료)을 참고해서 **semantic
block 우선 패킹**으로 바꿔주세요. 우선순위는:

1. semantic block(상위항목+하위행 묶음) 보존 — 안 찢기
2. `max_tokens_per_chunk` 예산
3. (block 구조를 못 찾은 평평한 표에서만) 기존 `max_rows_per_chunk` fallback

pipeline-kim의 `dup_left`/`dup_up`/`style="kv"/"grid"`/`header_labels`/
`title_hint`+`unit_hint`+`period_hint` preamble은 전부 그대로 유지하시면 됩니다 —
바뀌는 건 body row를 "행 단위"가 아니라 "block 단위"로 순회하는 부분뿐입니다.

## pipeline-kim 쪽에 추가로 필요한 것

1. **`TableCell.indent: int = 0`** 필드 추가. `dart_xml_parser.py`에서 셀 텍스트를
   strip 하기 *전에* leading whitespace 길이를 재서 넣어주세요(strip 후엔 이미
   소실됨). 우리 쪽 예시:
   ```python
   def _cell_indent(raw_text: str) -> int:
       return len(raw_text) - len(raw_text.lstrip())
   ```
   `RawCell.text`를 만드는 지점에서 strip 전 원본에 대해 호출해서 `RawCell.indent`로
   보존하고, `expand_grid()`가 `TableCell.indent`로 그대로 넘기면 됩니다.

2. **`TableCell.origin_id`**: pipeline-kim은 이미 `_GridCell.origin_id`로 개념이
   있습니다(rowspan 확장 시 같은 원본 셀 추적). 이걸 `_GridCell` 래퍼에만 두지 말고
   `TableCell` 필드로도 하나 복사해주세요(`detect_semantic_blocks`가 `TableCell`
   리스트만 받게 만들었습니다 — `_GridCell` 안 씀).

## 참조 구현 1 — `table_parser.py`에 추가할 함수 (그대로 복사 가능)

```python
import re

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


def _label_info(row: list) -> tuple[str, int, int]:
    """행의 "라벨 셀"(첫 번째 비어있지 않은 셀) 텍스트/들여쓰기/origin_id 를 반환.
    행 전체가 빈 텍스트면 ("", 0, -1).

    dup 칸 안전성: dup_left 셀은 같은 행에서 원본(열 0)보다 항상 뒤에 오므로
    라벨 탐색 순서에 영향이 없다. dup_up 셀(rowspan 반복)이 행의 첫 셀라면
    origin_id 를 원본과 동일하게 물려받으므로, detect_semantic_blocks 의
    "origin == prev_origin -> 같은 block" 규칙이 자연스럽게 적용된다."""
    for c in row:
        if c.text.strip():
            return c.text.strip(), c.indent, c.origin_id
    return "", 0, -1


def detect_semantic_blocks(body_rows: list[list]) -> list[list[int]]:
    """body_rows(헤더 제외 본문 행들)를 semantic block(행 index 묶음)으로 나눈다.

    반환값은 body_rows 에 대한 0-based row index 의 리스트의 리스트 — 원소
    순서는 원본 행 순서를 그대로 보존한다(재정렬 없음).

    신호: (a) 번호/계층 표기(1./1)/(1)/가./Ⅰ. 등) (b) 들여쓰기 (c) rowspan 반복
    (origin_id 동일) (d) 직전 행과의 label 변화 (e) 빈 셀(spacer row)은 직전
    block 에 그대로 붙임. 번호도 들여쓰기도 없는 평평한 표(각 행이 완결된
    항목)는 모든 행을 각자 독립 block 으로 반환해 기존 동작과 동일하게 유지.
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
        return [[i] for i in range(n)]

    blocks: list[list[int]] = [[0]]
    block_baseline_indent = infos[0][1]

    for idx in range(1, n):
        text, indent, origin = infos[idx]
        _prev_text, _prev_indent, prev_origin = infos[idx - 1]

        if text == "":
            blocks[-1].append(idx)
            continue

        if origin != -1 and origin == prev_origin:
            blocks[-1].append(idx)
            continue

        starts_new = False
        if has_numbering:
            if numbered_flags[idx] and indent <= block_baseline_indent:
                starts_new = True
        else:
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
```

## 참조 구현 2 — `render_table_node()`를 대체할 함수 (pipeline-kim 렌더링 디테일 유지)

pipeline-kim의 `_cell_text`/`_header_labels`/`_fmt_row`/`_table_header_and_body`는
이름 그대로 재사용하면 됩니다(이미 있는 그 함수들). 아래는 body row 순회 부분만
block 단위로 바꾼 버전입니다:

```python
def render_table_node_fragments(
    node, *, max_rows_per_chunk: int = 20, max_tokens_per_chunk: int = 1000,
    style="grid",
) -> list:
    """우선순위: 1) semantic block 보존 > 2) max_tokens 예산 >
    3) (구조 없는 표에서만) max_rows fallback."""
    if not node.rows:
        return []

    header_rows, body_rows = _table_header_and_body(node)          # pipeline-kim 기존 함수
    header_labels = _header_labels(node, header_rows)              # pipeline-kim 기존 함수
    header_lines = [] if style == "kv" else [
        " | ".join(_cell_text(c) for c in r) for r in header_rows  # pipeline-kim 기존 함수
    ]

    preamble_parts = [p for p in (node.title_hint, node.unit_hint, node.period_hint) if p]
    preamble = "\n".join(preamble_parts)
    fixed_overhead = estimate_tokens(preamble) + sum(estimate_tokens(h) for h in header_lines)

    def fmt(row) -> str:
        return _fmt_row(row, style=style, header_labels=header_labels)  # pipeline-kim 기존 함수

    def render_lines(extra_label, row_indices) -> str:
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
        return [text]

    row_blocks = detect_semantic_blocks(body_rows)
    has_structure = any(len(b) > 1 for b in row_blocks)

    def block_label(block) -> str:
        for i in block:
            label = next((c.text.strip() for c in body_rows[i] if c.text.strip()), "")
            if label:
                return label
        return ""

    def block_tokens(block) -> int:
        return sum(estimate_tokens(fmt(body_rows[i])) for i in block)

    fragments: list[str] = []

    if not has_structure:
        # 구조 신호 없는 평평한 표 -> 기존 pipeline-kim 동작(행 단위 max_rows+max_tokens)과 동일.
        group, group_tokens = [], fixed_overhead
        for block in row_blocks:
            i = block[0]
            row_tokens = estimate_tokens(fmt(body_rows[i]))
            would_exceed_rows = len(group) >= max_rows_per_chunk
            would_exceed_tokens = group and (group_tokens + row_tokens > max_tokens_per_chunk)
            if group and (would_exceed_rows or would_exceed_tokens):
                fragments.append(render_lines(None, group))
                group, group_tokens = [], fixed_overhead
            group.append(i)
            group_tokens += row_tokens
        if group:
            fragments.append(render_lines(None, group))
        return fragments or [render_lines(None, [])]

    # --- semantic block 이 있는 경우: block 단위로 패킹, max_rows 는 안 씀 ---
    group_blocks, group_tokens = [], fixed_overhead

    def flush():
        if not group_blocks:
            return
        row_indices = [i for b in group_blocks for i in b]
        fragments.append(render_lines(None, row_indices))

    for block in row_blocks:
        label = block_label(block)
        b_tokens = block_tokens(block)

        if b_tokens > max_tokens_per_chunk:
            # Oversized block: 지금까지 쌓인 group 을 먼저 flush 하고,
            # 이 block 자체를 토큰 예산 단위로 쪼갠다. 모든 조각에
            # "[block라벨 i/총개수]" 를 반복 삽입해 "계"만 남아도 항목 식별 가능하게 함.
            flush()
            group_blocks, group_tokens = [], fixed_overhead

            sub_groups, cur, cur_tokens = [], [], fixed_overhead
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
                fragments.append(render_lines(sub_label, sub))
            continue

        if group_blocks and group_tokens + b_tokens > max_tokens_per_chunk:
            flush()
            group_blocks, group_tokens = [], fixed_overhead

        group_blocks.append(block)
        group_tokens += b_tokens

    flush()
    return fragments or [render_lines(None, [])]
```

> 참고: 우리 리포의 실제 버전은 `list[str]`이 아니라 `list[TableFragment]`(text +
> semantic_groups + metric_hints + split_reason)를 반환해서 `ChunkSchema`에
> `table_id`/`semantic_groups`/`metric_hints`/`table_chunk_index`/`table_chunk_count`/
> `prev_table_chunk_id`/`next_table_chunk_id` 메타데이터를 채우고, 검색된 chunk가
> 표 조각이면 같은 표의 인접 조각을 evidence 후보에 추가하는 `search_disclosures`
> sibling expansion까지 이어집니다. 이 메타데이터/expansion까지 필요하시면 별도로
> 요청 주세요 — 이 문서는 가장 핵심인 "table chunking 자체가 안 찢어지게" 하는
> 부분만 담았습니다.

## 검증 방법 (그대로 재현해보시면 됩니다)

```python
# corpus/raw/periodic/SK하이닉스/20260317000635_annual_2025_12/20260317000635.xml 를
# 파싱해서 "지역별 재무 정보" 표(title_hint 로 찾을 수 있음, 47행)를 뽑은 뒤:
fragments = render_table_node_fragments(table_node)
texts = [f if isinstance(f, str) else f.text for f in fragments]
assert any("192,972,588" in t and "47,206,319" in t for t in texts), \
    "매출액 계와 영업이익 계가 같은 조각에 없음 — semantic block 이 안 먹힘"
```

기존 pipeline-kim 테스트(특히 `test_properties.py`의 크기 분포/커버리지 계약)가
이 변경으로 깨지지 않는지도 같이 확인해주세요 — semantic block이 있는 표는 chunk
개수가 줄어드는 방향(더 큰 chunk)으로 바뀔 수 있어서, "chunk 최대 길이" 상한
계약이 있다면 `max_tokens_per_chunk`를 존중하는지(oversized block 분할 경로)
같이 확인 부탁드립니다.
