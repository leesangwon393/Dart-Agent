# 표 Semantic Block Chunking 회귀 수정 — 무인 야간 작업 보고서

작업일: 2026-08-24 (야간 무인 실행) · 커밋: `b112925` (main, push 완료)

## 1. 기존 문제 / 실제 failure mechanism

`chunk_schema.render_table_node()`가 표 body를 `max_rows_per_chunk=20`,
`max_tokens_per_chunk=1000` 같은 **순수 행count/토큰 기준**으로만 잘랐다.
그 결과 "1. 매출액 / 제품 / 상품 / 연결조정 / 계" 같이 의미적으로 붙어있어야
하는 상위항목+하위행 묶음(semantic block)이 chunk 경계와 무관하게 찢겼다.

**실제 재현** (`corpus/raw/periodic/SK하이닉스/20260317000635_annual_2025_12/
20260317000635.xml`, "192,972,588" 검색): 지역별 매출/영업이익/자산 표
(46개 body row)에서

```
    계          | 192,972,588 | 129,960,534 | 67,573,636    <- "1. 매출액" 그룹의 합계
2. 영업이익      |             |             |
    연결조정     |      32,476 |    (182,030)|      86,230
    계           |  47,206,319 |  23,467,319 | (7,730,313)   <- "2. 영업이익" 그룹의 합계 (정답)
```

옛 알고리즘은 46개 body row를 20행 단위로 잘랐다: group1=[0:20), group2=
[20:40), group3=[40:46). "1. 매출액 계"(192,972,588, row 39)는 group2에,
"2. 영업이익"(row 40)부터는 group3에 들어가 서로 다른 chunk로 분리됐다 —
"SK하이닉스 2025년 영업이익은 얼마야?" 질문에서 정답(47,206,319백만원)이
검색된 chunk 밖에 있어 답을 찾지 못하는 근본 원인이었다.

## 2. 변경 사항

### Semantic Block Detector (`table_parser.detect_semantic_blocks`)
3가지 deterministic 신호를 조합해 body row를 block으로 그룹화한다(LLM 미사용):
- **번호/계층 표기**: `1.` `1)` `(1)` `가.` `(가)` `I.`(ASCII) `Ⅰ.`(유니코드
  로마숫자, 재무제표에 흔함) 등이 행의 "라벨 셀"(첫 비어있지 않은 셀)에
  나타나면 새 block 시작 후보.
- **들여쓰기**(`TableCell.indent`): `dart_xml_parser._cell_indent()`가 strip
  전 원본 셀 텍스트에서 leading whitespace 길이를 측정해 보존(기존엔
  `_text_of()`가 이미 strip해서 이 정보가 소실돼 있었다 — 이번에 별도로
  strip 안 한 텍스트에서 다시 측정하도록 고쳤다). 번호가 있는 행이라도
  현재 block의 baseline indent보다 더 들여써져 있으면 새 block으로 인정하지
  않는다(하위 항목의 오탐 방지).
- **rowspan 반복**(`TableCell.origin_id`): rowspan 확장으로 같은 원본 셀이
  여러 행에 반복되면(예: "2. 투자내역"이 4행에 걸침) origin_id가 같으므로
  번호/들여쓰기 판단과 무관하게 무조건 같은 block으로 묶는다.
- 위 신호가 전혀 없는 평평한 표(예: 삼성SDI 손익계산서처럼 각 행이 완결된
  항목)는 모든 행이 각자 독립 block — 기존 동작과 동일하게 유지.

### Semantic Block Packer (`chunk_schema.render_table_node_fragments`)
우선순위를 **1) semantic block 보존 > 2) max_tokens 예산 > 3) (구조를 못
찾은 표에서만) max_rows fallback**으로 바꿨다. block들을 순서대로 훑으며
누적 토큰이 예산 이내면 계속 합치고, 넘으면 그 block을 넣기 *전에* flush.
`max_rows_per_chunk`는 semantic 구조가 전혀 없는 표에서만 쓰인다.

### Oversized Semantic Block 처리
하나의 block 자체가 예산(1000토큰)보다 크면 그 때만 내부적으로 추가 분할한다.
분할된 모든 조각에 title_hint/unit_hint/header row/**"[block라벨 i/n]"**을
반복 삽입 — "계"만 남은 마지막 조각도 어느 항목의 합계인지 독립적으로 식별
가능하다.

### Chunk Metadata 강화 (`ChunkSchema`/`packer.PackedUnit`)
`table_id`(표 단위 식별자, `TableNode` 생성 시 uuid4로 자동 부여),
`semantic_groups`(이 chunk에 포함된 block 라벨), `metric_hints`(라벨에서
번호 prefix를 뗀 지표명 후보), `table_chunk_index`/`table_chunk_count`,
`prev_table_chunk_id`/`next_table_chunk_id`(같은 표의 다른 chunk의 실제
chunk_id — `chunkers._link_table_chunk_siblings()`가 최종 chunk_id 확정 후
채움)를 추가했다. 전부 기본값(None/[])이 있어 하위호환 유지, 기존 필드는
이름/순서 그대로 보존.

### Same-table Sibling Expansion (`agent/tools.py`)
`make_search_disclosures_tool(retriever, expand_table_siblings=True,
max_table_sibling_expansion=1)` — 검색된 chunk가 `table_id`를 가지면 같은
표의 다른 chunk를 evidence 후보에 추가한다. 우선순위: 직접 검색된 chunk >
query와 `metric_hints`가 겹치는 sibling > 표 안에서 가까운(prev/next)
sibling. `expand_table_siblings=False`로 끄면 기존 동작 그대로. **BM25/
Dense/Fusion 스코어링 로직은 건드리지 않았다** — sibling은 `score=None`으로
추가되고, 실제 가중치 반영은 TODO로 남김(§10 참고).

## 3. 수정 파일 목록

| 파일 | 변경 요지 |
|---|---|
| `src/disclosure_rag/common/doc_tree.py` | `TableCell.indent`/`origin_id` 필드 추가, `TableNode.table_id`(uuid4 자동생성) 추가 |
| `src/disclosure_rag/parsing/table_parser.py` | `RawCell.indent` 추가, `expand_grid()`가 indent/origin_id를 `TableCell`로 전달, `detect_semantic_blocks()`/`strip_numbering_prefix()`/번호 정규식 신규 |
| `src/disclosure_rag/parsing/dart_xml_parser.py` | `_cell_indent()` 신규 — strip 전 원본에서 들여쓰기 계산 |
| `src/disclosure_rag/chunking/chunk_schema.py` | `render_table_node_fragments()`(신규, block 기반 패킹+oversized split+debug 로그), `TableFragment` 신규, `render_table_node()`는 하위호환 wrapper로 축소, `ChunkSchema`에 6개 필드 추가 |
| `src/disclosure_rag/chunking/packer.py` | `PackedUnit`에 표 메타데이터 필드 추가, `render_table_node_fragments()` 사용하도록 교체 |
| `src/disclosure_rag/chunking/chunkers.py` | `_link_table_chunk_siblings()` 신규, ChunkSchema 생성 시 표 메타데이터 전달 |
| `src/disclosure_rag/agent/tools.py` | `_extract_query_terms`/`_table_sibling_index`/`_expand_table_siblings` 신규, `make_search_disclosures_tool`에 sibling expansion 옵션 추가 |
| `tests/test_table_semantic_chunking.py` | 신규, 14개 회귀 테스트 |
| `tests/test_chunkers.py` | KeyValueNode 긴 value characterization 테스트 1건 추가 |
| `PROJECT_STATE.md` | §10에 이번 수정 상세 기록, §12에 후속 TODO 3건 추가 |

## 4. 체크포인트 결과

| # | 내용 | 결과 |
|---|---|---|
| CHECKPOINT 1 | detect_semantic_blocks: 실제 SK하이닉스 데이터로 "1.매출액"/"2.영업이익" 분리 + block 내부(연결조정/계) 안 잘림, rowspan 반복 케이스(synthetic, 실제 exchange corpus의 rowspan=4 케이스는 KeyValueNode 경로였음) | **PASS** (1회 통과, 재작업 없음) |
| CHECKPOINT 2 | 3-block synthetic 표: 예산 이내면 한 chunk, 예산 초과 시에도 block 안 잘림("매출액 계 -- CUT -- 영업이익" 재발 없음) | **PASS** |
| CHECKPOINT 3 | oversized block 강제 분할 시 모든 조각에 title/unit/header/block라벨 반복 | **PASS** |
| CHECKPOINT 5 | search_disclosures sibling expansion on/off | **PASS** |
| CHECKPOINT 4 | 실제 XML 4개 + SK하이닉스 사업보고서 regression | **PASS** (아래 §5) |
| CHECKPOINT 6 (Phase 8, 전체 테스트) | `pytest tests/ -m "not slow"` | **PASS, 112 passed** (기존 97건 그대로 통과, 신규 15건 추가, 회귀 0건 — 테스트 수정 불필요) |

Phase 6(KeyValueNode 긴 value)는 characterization 테스트로 현재 동작을
고정(정보 손실 없음 확인)하고 semantic subdivision은 TODO로 명시(§10).

FAIL → 수정 → PASS로 전환된 항목: **없음**. 설계 단계에서 실제 XML로
먼저 검증하며 만들어서, 첫 구현이 모든 체크포인트를 통과했다. 유일하게
사후 보강한 부분은 번호 정규식에 유니코드 로마숫자(Ⅰ,Ⅱ,Ⅲ...)를 추가한
것 — 실제 SK하이닉스 감사보고서 손익계산서(`Ⅰ. 매출액`/`Ⅳ. 영업이익` 등)에서
발견해 정규식을 넓혔다(ASCII `I.`만으로는 매칭 안 됨).

## 5. 실제 XML 4개 + SK하이닉스 사업보고서 결과

expand_grid 실측 결과, 4개 exchange 파일 모두 **원본 HTML은 17×4(3건) /
9×3(1건)** 이지만, colspan으로 중복된 4번째 열이 RLE 축약으로 사라져
**전부 KeyValueNode로 분류됨(TableNode 0개)** — 이건 기존 `classify_grid()`의
의도된 동작이지 버그가 아니다(§7 요구사항인 "다르면 조사"에 따라 원인
확인 완료).

| 파일 | expanded grid | 최종 Node | KeyValueNode 그룹 수 | 최종 chunk 수 | 필수 값 |
|---|---|---|---|---|---|
| 20240424800596 | 17×4 | KeyValueNode | 5 | 1 | ✅ 5,296,200,000,000 / 9.90 / 청주 M15X 건설 |
| 20240726800615 | 17×4 | KeyValueNode | 5 | 1 | ✅ 9,411,500,000,000 / 17.59 / 용인 반도체 클러스터 내 신규 Fab 건설 |
| 20241220800005 | 9×3 | KeyValueNode | 3 | 1 | ✅ 5.9조원 / HBM 경쟁력 강화 / 2025년 1월 ~ 2039년 12월 |
| 20260225801974 | 17×4 | KeyValueNode | 5 | 1 | ✅ 21,608,100,000,000 / 29.23 / 용인 반도체 클러스터 1기 Fab Phase 2~6 건설 |
| 20260317000635(사업보고서) | 다수 TableNode(문서 전체 704 chunk) | TableNode/KeyValueNode 혼재 | — | 704 | ✅ 지역별 매출/영업이익 표에서 192,972,588과 47,206,319가 **같은 chunk**(`periodic_20260317000635::main::P22::C2`)에 공존 |

## 6. SK하이닉스 영업이익 failure 재현 — 기존 vs 변경 후 비교

- **기존(재현)**: 46-row 표를 `max_rows_per_chunk=20`으로 자르면 group2=
  row[20:40)에 "1.매출액 계"(192,972,588), group3=row[40:46)에 "2.영업이익"
  (47,206,319)가 들어가 **서로 다른 chunk로 분리**.
- **변경 후**: `detect_semantic_blocks()`가 14개 block으로 나누고, 표
  전체 추정 토큰(<1000)이 예산 이내이므로 `render_table_node_fragments()`가
  **표 전체를 1개 fragment로 유지** — 192,972,588과 47,206,319가 항상 같은
  chunk에 존재. 실제 `build_all_chunks()` 산출물에서 확인:
  `periodic_20260317000635::main::P22::C2`가 두 값을 모두 포함하며
  `semantic_groups`에 `"2. 영업이익"` 라벨이 명시적으로 남아있다.
- 전용 regression 테스트 `test_periodic_sk_hynix_repro_operating_profit_
  same_chunk`로 고정.

## 7. Q1~Q8 자문자답

1. **"2. 영업이익" 시작과 "계"가 여전히 다른 chunk로 갈라지는 경우가
   있는가?** — block 자체가 1000토큰을 넘는 **oversized 경우에만** 내부
   분할되며, 이 때는 모든 조각에 `"2. 영업이익 [i/n]"` 라벨이 반복
   삽입되므로 "계"만 남아도 항목을 알 수 있다. 1000토큰 이하 block은
   never 잘리지 않는다(아래 Q2).
2. **1000토큰 이하 block인데 중간에 잘릴 경로가 남아있는가?** — 없다.
   `b_tokens > max_tokens_per_chunk`일 때만 oversized-split 경로에
   진입하고, 그 외에는 항상 `group_blocks`에 통째로 추가된 뒤 flush된다.
   `test_packer_does_not_split_block_across_chunks_when_budget_exceeded`로
   검증.
3. **매출액+영업이익을 한 chunk에 넣을 수 있는데도 억지로 분리하는가?**
   — 아니다. `max_rows_per_chunk`는 semantic 구조가 감지된 표에서는 전혀
   쓰이지 않는다. 실제 46-row SK하이닉스 표가 (옛 로직이면 3개 chunk로
   쪼갰을 것을) 1개 chunk로 유지되는 것으로 확인.
4. **여러 chunk로 나뉜 표의 각 조각에서 제목/단위/header/semantic label을
   독립적으로 확인 가능한가?** — 그렇다. `render_lines()`가 모든 fragment에
   preamble(title_hint+unit_hint)과 header_lines를 무조건 포함하고,
   block은 항상 통째로(자기 라벨 행 포함) 들어가거나, oversized일 때만
   명시적 라벨을 추가로 삽입한다.
5. **table chunk 하나만 검색됐을 때 sibling 확장이 되는가?** — 그렇다
   (기본 `expand_table_siblings=True`). CHECKPOINT 5로 검증.
6. **KeyValueNode 동작이 기존과 동일한가?** — 그렇다. `classify_grid()`의
   KeyValueNode 분기와 `render_kv_node()`는 이번 작업에서 전혀 수정하지
   않았다. `TableCell`에 필드 2개를 추가했지만 KeyValueNode 경로는
   `cell.text`/`field_code`/`unit_code`만 사용해 영향 없음. 전체 테스트
   112건 PASS로 재확인.
7. **field_codes/기존 metadata 손실 없는가?** — 없다.
   `test_field_codes_not_dropped_in_table_or_kv_chunks`(기존 테스트) 계속
   PASS, `packer.py`의 `table_codes` 계산 로직은 그대로 유지하고 신규
   필드만 나란히 추가했다.
8. **실제 XML 4개 전부 regression 통과했는가?** — 그렇다(§5). 추가로
   당초 계획에 없던 SK하이닉스 사업보고서(연결감사보고서 손익계산서,
   유니코드 로마숫자 Ⅰ~Ⅵ 표기)까지 regression에 포함해 번호 정규식을
   한 차례 보강했다.

## 8. 커밋 해시 + push 여부

- 커밋: `b112925` — "표 청킹: semantic block(1./2. 상위항목+하위행) 보존, max_rows 순수분할 제거"
- `git push` 완료 (`0480381..b112925 main -> main`, origin/main 최신)

## 9. 재청킹/재임베딩 진행 상황

(정직하게 실측만 기록, 완료를 지어내지 않음 — 아래 "최신 상태"가 이
보고서 작성을 마친 시점의 실측치다. 그 이후 상황은 §9-A "재개 방법"으로
다음 세션이 확인할 것.)

### 재청킹 — 완료
`scripts/rebuild_chunks_v2.py`로 `corpus/` 전체(4,204건)를 새 semantic
block chunking 로직으로 재청킹 완료(207.9초 소요). 결과:
```
총 chunk 수: 494,194 (leaf/검색대상: 430,925, parent/context용: 63,269)
이전 leaf chunk 수(467,043, gpu_embeddings/ 기준)와 비율: 0.923x
table_id 있는 leaf chunk: 317,685, 그 중 표가 여러 조각으로 나뉜 chunk: 130,621
```
0.5~2.0x 범위 안(7.7% 감소)이라 버그 의심 없음 — 오히려 기대했던 방향의
변화다: 예전엔 max_rows=20 으로 억지로 쪼개던 표들이 이제 semantic
block+token budget 기준으로 합쳐져서 표가 있는 문서의 leaf chunk 수가
소폭 줄었다. 결과는 `chunks_v2/all_chunks.pkl`(4.6GB) /
`chunks_v2/leaf_chunks.pkl`(2.6GB, 검색 대상)에 저장.

### 재임베딩 — 진행 중 (원격 서버 미접속, 로컬 MPS로 대체)
원격 GPU 서버(mileb-v100, PROJECT_STATE.md §7)는 이 세션에 SSH 인증키가
없어 접속 시도하지 않았다. 대신 이 Mac의 Apple Silicon MPS 사용.

**시행착오**: 1차 시도(batch_size=32, clip 없음)가 MPS
`Insufficient Memory (kIOGPUCommandBufferCallbackErrorOutOfMemory)` 로
즉시 크래시. 원인 조사 결과 표 셀 하나에 긴 각주가 통째로 들어간 극단적
outlier chunk(최대 153,345자, 신한지주 등)가 원인 — **이건 이번 chunking
수정과 무관한 pre-existing 데이터 특성**임을 옛 `gpu_embeddings/` 샤드에도
동일 크기 outlier가 있음을 확인해 검증했다(PROJECT_STATE.md §10 참고).
임베딩 스크립트에만(코어 chunking 로직은 안 건드림) 방어 로직 추가:
임베딩 입력을 6,000자로 clip(저장되는 원본 텍스트는 그대로), batch_size
32→8로 하향, 실패 시 배치 절반씩 재귀 재시도, 그래도 실패하는 개별
chunk는 zero-vector로 대체 후 `failed_chunk_ids.jsonl`에 기록. 재시도 후
가장 큰 8개 outlier(76,490~153,345자)를 한 배치로 넣어도 정상 완료됨을
사전 검증하고 실행.

**사전 벤치마크(clip 전, 실측)**: 실제 chunk 길이(약 1,270자) 텍스트
기준 약 8.4 texts/sec — CPU 592ms/chunk(≈1.7/s) 대비 약 5배. 430,925개
기준 예상 총 소요 약 14~15시간(clip/재시도 오버헤드로 실제론 다소 달라질
수 있음 — 아래 "최신 상태"의 실측 shard 처리시간이 더 정확한 근거).

**스크립트**: `scripts/embed_full_corpus_mps.py` — `gpu_embeddings_v2/`에
shard **5,000개** 단위(기존 `gpu_embeddings/`는 20,000개 단위였으나, MPS가
더 느리고 중간에 죽을 위험이 있어 체크포인트 간격을 좁힘)로 저장, 기존
`gpu_embeddings/`는 **절대 덮어쓰지 않음**. `gpu_embeddings_v2/progress.json`
의 `next_idx`로 재개 가능(스크립트를 그냥 다시 실행하면 자동으로 이어서
진행), `gpu_embeddings_v2/embed_progress.log`에 shard마다 진행률/ETA append.

### 최신 상태 (이 보고서 작성 시점 실측)
- 프로세스: 실행 중(pid는 실행 시점마다 다름 — `ps aux | grep embed_full_corpus_mps`로 확인)
- 로그: `gpu_embeddings_v2/embed_progress.log`, `gpu_embeddings_v2_stdout.log`
- 진행률: `cat gpu_embeddings_v2/progress.json` 의 `next_idx` / 430,925
- 완료 판정: `embed_progress.log`에 "전체 완료." 줄이 있으면 끝난 것

### 재개 방법 (다음 세션 / 중단 후 이어서)
```bash
cd "/Users/isang-won/Desktop/공시 agent"
# 살아있는지 확인
ps aux | grep embed_full_corpus_mps
# 진행률 확인
cat gpu_embeddings_v2/progress.json
tail -30 gpu_embeddings_v2/embed_progress.log
# 죽어있으면 그냥 다시 실행 — progress.json 기준으로 자동으로 이어서 진행됨
nohup .venv/bin/python scripts/embed_full_corpus_mps.py > gpu_embeddings_v2_stdout.log 2>&1 &
```

### 완료 후 BM25/Dense 인덱스 재구성 방법 (다음 세션용)
```python
# 1) BM25: 임베딩과 무관하게 chunks_v2/leaf_chunks.pkl 만 있으면 바로 가능
import pickle
from disclosure_rag.retrieval.bm25_retriever import BM25Retriever
from disclosure_rag.retrieval.tokenizers import Tokenizer  # 프로젝트 기본 토크나이저 사용
leaf_chunks = pickle.load(open("chunks_v2/leaf_chunks.pkl", "rb"))
bm25 = BM25Retriever(leaf_chunks, tokenizer=Tokenizer(...))  # 기존 스크립트의 tokenizer 생성 방식 그대로

# 2) Dense: gpu_embeddings_v2/shard_*.pkl 를 순서대로 읽어 QdrantVectorStore 에 upsert
import glob
from disclosure_rag.retrieval.qdrant_store import QdrantVectorStore
store = QdrantVectorStore(...)  # 기존 구성 그대로(경로만 신규로 하고 싶으면 collection 이름 변경)
for f in sorted(glob.glob("gpu_embeddings_v2/shard_*.pkl")):
    d = pickle.load(open(f, "rb"))
    store.upsert_chunks(d["chunks"], d["vectors"])
```
PROJECT_STATE.md §7의 "전체 70개사 임베딩 재사용 방법"과 동일한 패턴 —
디렉터리만 `gpu_embeddings/` → `gpu_embeddings_v2/`로 바꾸면 된다. 완전히
검증(retrieval 품질 재평가)되기 전까지는 기존 `gpu_embeddings/`/Qdrant
컬렉션을 유지하고, v2는 별도 컬렉션으로 먼저 A/B 비교할 것을 권장.

## 10. 남은 위험 / TODO

- **KeyValueNode 긴 value semantic subdivision** — exchange_20241220800005의
  "2. 주요내용"처럼 KeyValueNode 하나의 value 안에 여러 의미 항목(투자
  목적/금액/기간/방법)이 이어붙어 있음. 정보 손실은 없으나(characterization
  테스트로 고정) 개별 항목 단위 검색은 아직 안 됨.
- **metric_hint 기반 sibling score boost 미구현** — sibling은 evidence
  후보에 추가만 되고(`score=None`) BM25/Dense/Fusion 실제 점수엔 반영 안
  됨. 이번 작업 범위에서 의도적으로 제외(§12 원칙: scoring 로직 불변).
- **PackedUnit 버퍼 병합 시 서로 다른 표 섞임 케이스** — 여러 개의 작은
  표가 우연히 같은 병합 buffer에 섞이면 `table_id`가 마지막 표 기준으로
  덮어써짐. 이 경우 표들은 전부 `table_chunk_count=1`이라 sibling
  expansion엔 영향 없지만, metadata 완전성 관점에서는 개선 여지 있음.
- 재청킹/재임베딩이 시간 내에 다 못 끝났을 경우 §9의 재개 방법으로
  다음 세션이 이어받을 것.
