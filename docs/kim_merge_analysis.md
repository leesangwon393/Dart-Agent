# Kim 브랜치 vs 우리 리포 — Phase 0 정밀 diff 분석

대상: `/Users/isang-won/Downloads/pipeline-kim`(읽기 전용, 팀원 Kim의 독립 감사/수정본)
vs `/Users/isang-won/Desktop/공시 agent`(우리 리포, 커밋 b9cfbd6 기준).

우리 쪽 "어젯밤" 변경분은 `git show b112925` / `git log -p b9cfbd6`로 직접 diff 확인.
Kim 쪽은 각 파일 docstring의 "KIM 브랜치 변경점" 섹션 + 실제 코드 diff로 확인.

## 파일별 비교

| 파일 | 우리(b9cfbd6) | Kim | 병합 방향 |
|---|---|---|---|
| `parsing/xml_sanitizer.py` | 없음 | 신규. bare `&`(무죄)/bare `<`(구조붕괴 원인)/속성값 여분 따옴표(문서절단 원인) 3종을 bytes 레벨에서 정리. B+C 동시 수정 필수(상호작용 발견). | Kim 파일을 그대로 이식 (변경 없음). |
| `parsing/dart_xml_parser.py` | `_escape_bare_special_chars`(bare &/< 만), `_cell_indent`(어젯밤 추가), `_MAX_TABLE_ROWS=500`, TABLE/TABLE-GROUP 후 `last_text=None` 리셋, TE에서만 ACODE/TU에서만 AUNIT 읽음 | `xml_sanitizer.sanitize_dart_xml` + `_parse_with_sanitizer`(손해 시 원본으로 폴백) 사용, `_MAX_TABLE_ROWS=3000`, TABLE 뒤 `last_text` 유지(표 여러 개 이어질 때 title_hint 보존, 실측 표의 31.1%만 title_hint 보유하던 문제 개선), `_cell_to_raw`가 태그 무관하게 ACODE/AUNIT/AUNITVALUE 읽음 | Kim 로직 전체 채택 + 우리 `_cell_indent` 유지·병합 |
| `parsing/table_parser.py` | `expand_grid`(RLE 축약 저장), `classify_grid`(1열 행 `continue`로 폐기), 어젯밤 추가된 `detect_semantic_blocks`/`strip_numbering_prefix`(TableCell.indent/origin_id 신호) | `expand_grid`(dup_left/dup_up 비-RLE, 정규 그리드 그대로 저장, `_GridCell` 래퍼로 origin_id 관리), `classify_grid`(1열 행 `TextNode(from_table_row=True)`로 보존, `_scan_hints`로 unit/period hint 실채움) | Kim의 grid/classify 채택. `_GridCell` 래퍼 제거하고 origin_id를 `TableCell` 필드로 직접 유지(우리 `detect_semantic_blocks`가 이미 그 모양을 기대하므로) + `detect_semantic_blocks`/`strip_numbering_prefix` 그대로 이식, dup 칸을 "새 라벨"로 오인하지 않도록 `_label_info` 보강 |
| `common/doc_tree.py` | `TableCell`에 `indent`/`origin_id`(어젯밤), `TableNode`에 `table_id`(어젯밤) | `TableCell`에 `row`/`col`/`dup_left`/`dup_up`, `TableNode`에 `period_hint`, `KeyValueNode.pairs`를 4-tuple→`KVPair`(unit_value 포함), `TextNode.from_table_row` | 전부 병합: `TableCell`(text/is_header/field_code/unit_code/unit_value/row/col/dup_left/dup_up/indent/origin_id), `TableNode`(+period_hint, +table_id), `KVPair`, `TextNode.from_table_row` |
| `chunking/chunk_schema.py` | `render_table_node_fragments`(semantic block 우선 패킹, TableFragment), ChunkSchema에 table_id/semantic_groups/metric_hints/table_chunk_index·count/prev·next_table_chunk_id | `FieldRef`(list 기반 구조화 필드, 위치 보존), `estimate_tokens` 주입 가능(`set_token_counter`), `render_table_node`가 정규 그리드 열 정렬 보존 + `style="grid"/"kv"` + header_labels + period_hint preamble, `table_field_refs`/`kv_field_refs` | `render_table_node_fragments`를 Kim의 렌더링 디테일(dup-aware fmt_row, header_labels, style, preamble에 period_hint 포함) 기반으로 재작성 + semantic block 우선순위 유지. `FieldRef`/`set_token_counter`/`table_field_refs`/`kv_field_refs` 그대로 채택. `field_codes: dict→list[FieldRef]`. `unit_hint`/`period_hint`를 ChunkSchema에도 추가 |
| `chunking/packer.py` | table_id 등 메타데이터 전파, 표 fragment는 flush 후 독립 unit(sibling expansion 대상), 일반 노드는 `if buf_texts and ...` 가드 버그(오버사이즈 첫 노드 미분할) | `split_long_text`(문단>줄>문장>어절 재귀분할), `FieldRef` 기반 `field_refs`(+`_refs_in`으로 조각별 필터), `_resolve_content_type`(문자 비중 기준), `PackedUnit.unit_hint/period_hint` | `split_long_text` 이식해 flush 가드 버그 수정. `FieldRef`/`_refs_in`/문자비중 content_type 채택. 우리 table_id/semantic_groups/metric_hints/table_chunk_index·count 전파 + "다중 fragment 표는 독립 unit" 동작 유지 |
| `chunking/chunkers.py` | `_link_table_chunk_siblings`, ChunkSchema에 table_id 등 전달, 파라미터 하드코딩(`CHILD_TARGET_TOKENS` 등 모듈 상수) | `ChunkConfig`(target/max/whole_doc tokens, table_max_rows, `table_style="kv"` 채택 — 587문서/314질의 실험으로 grid보다 hit@5 우세 확인) dataclass로 파라미터화, `unit_hint`/`period_hint`를 ChunkSchema로 전달 | `ChunkConfig` 도입(기존 함수 시그니처는 `cfg` 옵션 인자로, `pipeline.py` 3-positional-arg 호출과 하위호환), `_link_table_chunk_siblings` 유지, unit_hint/period_hint 전달 추가 |
| `agent/tools.py` | `expand_table_siblings`/`max_table_sibling_expansion`(sibling expansion, 어젯밤 추가) | 동일한 `_evidence_dict`/sibling 관련 코드 **없음** 대신 `TOP_K_BY_ROUTE`/`top_k_for_route`(라우트별 top_k 실험, 코드베이스 어디에서도 호출되지 않는 dead code) | 변경 없음 — 우리 sibling expansion 로직이 이미 Kim에 없는 걸 갖고 있고, Kim의 `TOP_K_BY_ROUTE`는 미배선 dead code + 라우팅 튜닝은 이번 병합 범위(파싱/표 정합성) 밖이라 이식하지 않음(§9에 기록) |

## 핵심 통합 포인트 확인

- **origin_id 개념 동일성**: Kim의 `_GridCell.origin_id`와 우리의 `TableCell.origin_id`는 "rowspan 확장으로 같은 원본 셀이면 값 동일"이라는 동일 개념. 충돌 없음 — `_GridCell` 래퍼를 없애고 origin_id를 `TableCell` 필드로 흡수해 하나로 통합.
- **dup_left/dup_up vs detect_semantic_blocks**: `detect_semantic_blocks`의 `_label_info`가 "행의 첫 번째 비어있지 않은 셀"을 라벨로 판단하는데, dup_left/dup_up=True인 셀은 렌더링에서는 빈칸이지만 `cell.text`는 원본 텍스트를 그대로 갖고 있어(Kim의 `expand_grid`가 dup cell에도 `text=gc.cell.text`를 복사) 실제로는 문제가 없음을 코드 레벨에서 확인 — 다만 "새 라벨 시작"으로 오인하지 않도록 명시적으로 dup 칸은 건너뛰게 보강.
- **field_codes 하위 호환 범위**: repo 전체에서 `field_codes`를 참조하는 곳은 `chunking/*.py`와 `tests/test_chunkers.py`, `tests/test_parsers.py`뿐(grep 확인, evidence.py/validator.py/calculation.py/retrieval/*.py 전부 무관) → dict→list[FieldRef] 전환이 다른 계층에 영향 없음.
