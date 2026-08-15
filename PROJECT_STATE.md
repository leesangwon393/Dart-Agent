# PROJECT_STATE.md — 금융공시 Agentic RAG 시스템

> 새 Claude 세션은 이 파일만 읽고 바로 이어서 작업할 수 있어야 한다.
> 최종 갱신: 2026-08-15 (Stage 5 진행 중)

---

## 1. 프로젝트 최종 목표

70개사 DART(한국 전자공시) 코퍼스(`corpus/`) 기반, **금융공시 특화 Agentic RAG
시스템**을 구축한다. 사용자 자연어 질의 → entity 추출 → routing → hybrid
retrieval(BM25+Dense+Fusion+Reranker) → 정정공시 버전 해석 → tool
calling(계산 등) → evidence pack 구성 → **HyperCLOVA X(HCX)** 로 근거 기반
최종 답변 생성 → 검증(validation). 단순 "질문→벡터검색→LLM" 구조가 아니다.

파이프라인 전체(§34, 원본 스펙 82절 문서 기준)는 이미 구현·검증 완료
(Phase 1~19). 현재는 **각 component 후보를 동일 eval set 으로 비교하는
rigorous ablation 실험(Stage 1~14)**을 진행 중이다. Fine-tuning 은 이번
단계에서 전부 제외.

---

## 2. 전체 아키텍처 / 파이프라인

```
공시 Corpus → Unicode Path Resolve → 유형별 Parsing → 유형별 Chunking
  → Correction Graph → 공통 Chunk Schema → [BM25 인덱스 + Dense 인덱스]
─────────────────────────────────────────────────────────────────
User Query → Entity Extraction + Query Normalize → Semantic Router(hint)
  → HCX Agent(Tool Calling loop) → search_disclosures/get_correction_history/
    get_latest_report/calculate_* → Evidence Pack 구성
  → HCX Answer Generator(evidence-only) → Validator → 최종 답변+근거
```

- **오프라인 파이프라인**: `src/disclosure_rag/pipeline.py` 가 manifest →
  parse → correction graph → chunk 전체를 오케스트레이션.
- **온라인 파이프라인**: `src/disclosure_rag/agent/ask.py` 가 진입점.

---

## 3. 확정한 설계 결정 (+이유)

| 결정 | 이유 |
|---|---|
| periodic/major/holding = parser 1개 공용 | 셋 다 동일 DART `DOCUMENT/SECTION-N` XML 스키마 (실측 확인) |
| exchange = 별도 HTML parser | `.xml` 확장자지만 실제 내용은 위장 HTML (실측 확인) |
| Unicode: 세그먼트 단위 NFC 리졸버 | `raw/` 폴더명 NFD, manifest/universe NFC — 직접 비교하면 100% 실패 |
| periodic 정정 매칭 = `(corp_name,doc_subtype,base_year,base_month)` manifest key | collision 0건, 텍스트 파싱 불필요, pdf+html 대체수집도 자동 처리 |
| major/exchange/holding 정정 매칭 = 본문 정규식 + transitive chain | "정정대상 공시서류의 최초제출일" 텍스트 99.9% 추출 성공. **단, 이 3종은 직전 정정본을 가리키는 다단 체인**이라 root 까지 chasing 필요(periodic 과 다름, §correction_graph_builder.py 주석 참고) |
| 표 파싱: rowspan/colspan 그리드 확장 + RLE dedup | colspan 중복 텍스트 버그 방지, KeyValue vs Table 그리드 정확히 분류 |
| 표 1개 TR 500행 cap | malformed XML 로 인한 lxml recover 오동작으로 TR 11,786개짜리 표가 실측 발견됨(§5 참고). 캡 없으면 chunk 하나가 수만 자로 폭주 |
| 검색 인덱스 = leaf chunk 만 | Parent(섹션 전체, 무제한 길이)를 그대로 임베딩하면 비정상적으로 느려짐(실측: CPU 30분+). `filter_leaf_chunks()` 필수 |
| HCX Agent system prompt = 짧게 유지 | **실측으로 결정적으로 재현된 버그**: system prompt 가 길면(~400자, 6줄 bullet) tool-calling 2번째 턴부터 HCX API 가 매번 400("Unsupported function") 반환. 3줄로 줄이니 해결. `agent_loop.py` 상단 주석 참고 — 새 지침 추가 시 반드시 다중 턴 테스트 확인 |
| Stage 1 winner: Section-aware+Parent-Child | 4개 지표(R@5/R@10/MRR/NDCG@10) 전부 1위, trade-off 없음 |
| Stage 2 winner: char_2gram (**잠정**, Kiwi 아님) | 실측 R@10=0.912 > Kiwi 0.802. 단, 단일회사 corpus 라 회사명 substring 매칭에 유리했을 caveat 있음 — 다회사 재검증 전까지 잠정 |
| Stage 3 winner: BGE-M3 (실무), e5-instruct(정확도 상한 참고) | e5-instruct 가 모든 정확도 지표 우위지만 6.3배 느림(591.7ms vs 3710.0ms/chunk) → 전체 코퍼스(45만 chunk) 환산 시 73시간 vs 463시간(19일). CPU-only 환경에서 e5 전체 인덱싱 불가능 |
| Stage 4 winner: Normalized Weighted Fusion(alpha=0.5) | 4개 지표 전부 1위, RRF보다 뚜렷이 우위, 구현 복잡도 거의 안 늘어남 |
| Qwen3-Embedding-0.6B, Qwen3-Reranker-0.6B 제외 | 이 환경에서 반복적으로 다운로드/로딩 실패(아래 §9 참고) — 재현 가능한 코드 버그 아니라 환경 이슈로 판단, 4회 이상 시도 후 확정 |

---

## 4. 완료된 작업

### 4-A. 핵심 시스템 (Phase 1~19, 전부 구현+테스트 완료, 실제 HCX API 검증됨)
- Phase 1: Unicode/Path Resolver — 4,204건 100% resolve
- Phase 4: Parser 4종 (periodic/major/holding 공용 + exchange 전용)
- Phase 5+6: Chunker + 공통 Chunk Schema (Parent-Child / flat)
- Phase 7: Correction Graph Builder (transitive chain resolution)
- Phase 8: BM25S + Kiwi baseline
- Phase 9: BGE-M3 + Qdrant dense retrieval
- Phase 10+11: RRF Fusion + bge-reranker-v2-m3
- Phase 12: Entity Extraction (universe.csv 기반 alias 자동 해결)
- Phase 13+14: Semantic Router 6종 + 평가 하네스
- Phase 15~19: HCX Agent(Tool Calling) + Evidence Pack + Answer Generator +
  Validator — **실제 HyperCLOVA X API 로 end-to-end 검증됨** (예: "삼성전자
  단일판매공급계약체결 정정 전후" 질문에 3턴 tool-calling 으로 정확히
  "계약상대 글로벌 대형기업→테슬라(Tesla, Inc.)" 답변, 원문 XML 대조 100% 일치)

테스트: `tests/` 70개 중 62개 fast 통과 + 8개 slow(HCX API/모델 필요) 통과.

### 4-B. Rigorous 실험 (Stage 1~4 완료, Stage 5 진행 중)
`results/{chunking,bm25,embedding,fusion}/` 에 각각 `config.json`,
`metrics.json`, `results.csv`, `failure_cases.jsonl`, `summary.md`,
`failure_analysis.md`, `comparison.json` 저장 완료. 상세 수치는 §8 참고.

### 4-C. 부가 산출물
- `results/qualitative_50q/`: 정식 Stage 체계 이전에 돌린 BM25-only Agent
  50문항 정성평가 (PASS 24, FAIL_UNGROUNDED 20, HONEST_NO_EVIDENCE 3, ERROR 3)
- GitHub 연동 완료: https://github.com/leesangwon393/Dart-Agent.git
  (origin/main, Stage 완료마다 커밋+push 하는 흐름으로 진행 중)

---

## 5. 현재 진행 중인 작업

**전체 Ablation 실험(Stage 1~14) + 최종 요약 완료.** 이후 사용자가 대회 참고
질의를 보여줘서 시작된 **"회사 일반화 검증"** 트랙이 진행 중이다(Stage 체계와
별개, `results/generalization_check/` 참고):
- 대회 예시 6개 질의를 실제 회사명(삼성전자/삼성SDI/LG에너지솔루션/한미반도체)
  으로 치환해 production `ask()`에 직접 태워보는 방식으로 검증 — Stage 1~14
  의 report-level Recall/NDCG 지표로는 못 잡았을 실제 버그 3개(TOC chunk
  오염/카운팅 오분류/validator 오탐 2건)를 발견하고 전부 수정+회귀테스트
  완료함(§10 참고).
- **전체 70개사 코퍼스를 GPU 서버에서 임베딩 진행 중**(로컬 CPU는 76.8시간
  추정이라 불가능 — `scripts/embed_full_corpus_gpu.py`, `~/Desktop/embedding/`
  번들로 전달함). 서버 쪽에서 macOS AppleDouble(`._*`) 메타데이터 파일이
  섞여 XML로 오인되는 문제가 발생했으나 서버 측(Codex)에서 원인 규명 및
  격리 완료, 재개 진행 중 — **아직 로컬로 결과 안 받아옴**.
- 방법론: 대회가 제시한 "검색추출/비교연산/복합추론 × Closed/Open" 6개
  카테고리를 좌표축으로 회사×카테고리 매트릭스(`matrix.csv`)를 채워나가는
  방식으로 진행하기로 사용자와 합의함 — 무작위 질문 던지기가 아니라 커버리지
  추적 가능하게. GPU 임베딩 도착하면 이 매트릭스를 다른 업종(금융지주/바이오/
  조선 등)으로 확장할 예정.
- 미해결로 남은 것: "복합추론_Open"(다년도/장문 비교, 예: "2023 vs 2025
  사업보고서 비교") 유형이 top_k=5 retrieval breadth 부족으로 반복 실패 —
  다음 우선순위.

**부가 트랙: Router 파인튜닝 데이터 준비** (GPU 임베딩과 별개로 진행 가능,
`results/router_tuning/` 참고):
- 대회 측이 CLOVA Studio 튜닝을 방법론으로 제공 → 어디를 튜닝할지 검토한 결과
  **Router(route 분류)가 1순위**로 결론남(이유: 닫힌 분류 문제라 튜닝이 잘
  먹히는 유형, 학습데이터 있음, 오늘 찾은 카운팅 오분류 버그가 정확히 이
  지점). Agent(tool 선택)는 2순위(잠재력 크지만 리스크 큼), Answer 생성은
  3순위(지금 문제들이 대부분 답변 품질이 아니라 retrieval/라우팅 문제라
  우선순위 낮음)로 정리함.
- CLOVA Studio 분류 튜닝 요구사항 웹 조사 완료: **카테고리당 최소 200행**
  (6개 route × 200 = 최소 1,200건 필요), 라벨은 한 단어·띄어쓰기/특수문자
  금지(언더스코어 포함 route 이름이 통과되는지 미확인, 콘솔에서 재확인
  필요), CSV/JSONL 형식(C_ID/T_ID/Text/Completion/System_Prompt 필드),
  행당 8,000자 이내. 분류 태스크엔 HCX-DASH-001 권장이라는 출처도 있었으나
  불확실 — 재확인 필요.
- **HCX로 학습데이터 생성하는 파일럿(150개) 완료 및 사용자와 함께 검토**:
  `scripts/generate_route_pilot.py` 로 6개 route × 25개 생성 → 실제로
  칼럼간 오분류 3~5건(전체의 ~3%) 발견 → 사용자와 함께 경계 규칙 2가지 확정
  (①기간별 비율/지표 비교→multi_compare, ②두 회사 정정이력 비교→
  correction_analysis, `results/router_tuning/rubric.md` 참고) → 오분류
  수정 반영한 최종 파일럿 149건(`results/router_tuning/pilot_v1_reviewed.json`)
  확보. **아직 1,200건까지 확대는 안 함** — 사용자 승인 대기 중.

---

## 6. 주요 파일과 역할

```
src/disclosure_rag/
  common/unicode_utils.py       NFC 정규화 + 세그먼트 단위 path resolver
  common/doc_tree.py             Parser 공통 중간 표현
  parsing/dart_xml_parser.py    periodic/major/holding 공용 (TR 500행 cap 포함)
  parsing/exchange_parser.py    exchange 전용 (위장 HTML)
  parsing/table_parser.py       rowspan/colspan grid 확장 + RLE dedup
  chunking/chunk_schema.py      공통 Chunk Schema + filter_leaf_chunks()
  chunking/chunkers.py          Parent-Child(periodic/holding) / flat(major/exchange)
  correction/correction_graph_builder.py   transitive chain resolution 핵심 로직
  retrieval/{tokenizers,bm25_retriever,embeddings,qdrant_store,dense_retriever,
             fusion,reranker,hybrid_retriever,metadata_filter}.py
  entity/{entity_extractor,query_normalizer}.py
  router/{routes,encoder_adapter,semantic_router_wrapper,eval,eval_dataset}.py
  agent/{hcx_client,tools,calculation,agent_loop,evidence,answer_generator,
          validator,ask}.py      온라인 파이프라인 전체
  experiments/
    metrics.py                   Recall/MRR/NDCG(report-level dedup 버그 수정됨)
    chunking_variants.py         Stage 1 전용 ablation 청커(Fixed-500/Section-aware)
  pipeline.py                    오프라인 오케스트레이터

config/financial_terms.txt      Kiwi 사용자 사전
config/metric_terms.txt          Entity Extraction 지표 키워드
eval/gold_queries.json           40개 gold query(validation 30/test 10, report_id 단위)
results/{stage}/                 각 Stage 실험 결과 (§4-B 형식)
.env                              HCX_API_KEY, HCX_MODEL (git 제외됨)
```

---

## 7. 중요한 명령어

```bash
# venv
uv venv --python 3.11 .venv && uv pip install -p .venv/bin/python -e .

# corpus 검증
.venv/bin/python -m disclosure_rag.common.corpus_validator corpus

# 전체 코퍼스 파싱+청킹 (4,204건, ~9분, 표 cap 수정 후 더 빠를 수 있음)
.venv/bin/python -m disclosure_rag.pipeline corpus

# 테스트
.venv/bin/python -m pytest tests/ -m "not slow"   # 빠른 것만(~20초)
.venv/bin/python -m pytest tests/                  # 전체(HCX API+모델 필요, 수 분)

# git
cd "/Users/isang-won/Desktop/공시 agent"
git add -A && git commit -m "..." && git push
```

**실험용 eval 코퍼스**: 삼성전자 1개사, 33개 문서(periodic 2 + major 19 +
exchange 2 + holding 10) 선정, doc_id 목록은 `/tmp/stage_eval_doc_ids.json`
(세션 임시 경로 — 사라졌으면 아래 §11 재생성 코드 참고). 이유: 전체
4,204건×3 embedding 모델 비교는 CPU-only 환경에서 물리적으로 불가능(실측
BGE-M3 591.7ms/chunk 기준 전체 코퍼스 임베딩 73시간).

**BGE-M3 임베딩 캐시**: `/tmp/bgem3_chunks_vectors.pkl` (1411 leaf chunks +
벡터, pickle) — 재임베딩(14분) 없이 재사용 가능. 세션 재시작 시 사라졌으면
`cache_bgem3_vectors.py` 류 스크립트로 재생성 필요(§11 참고).

---

## 8. 실험 결과 요약 (validation set n=30, 상세는 각 results/{stage}/)

| Stage | Winner | 핵심 수치 |
|---|---|---|
| 1 Chunking | Section-aware+Parent-Child | R@5=0.706 R@10=0.802 MRR=0.682 NDCG@10=0.667 |
| 2 BM25 Tokenizer | char_2gram (잠정) | R@10=0.912 MRR=0.757 (Kiwi: R@10=0.802 MRR=0.682) |
| 3 Dense Embedding | BGE-M3(실무) / e5-instruct(정확도상한) | bge-m3 R@10=0.840(591.7ms) vs e5 R@10=0.867(3710.0ms) |
| 4 Fusion | Normalized Weighted Fusion | R@10=0.903 R@20=0.940 MRR=0.713 NDCG@10=0.735 |
| 5 Reranker | No-Reranker(CPU 배포용) / bge-reranker-v2-m3(GPU면 재검토) | no_reranker Hit@1=0.633 MRR=0.712(42.7ms) vs bge_reranker Hit@1=0.667 MRR=0.773(11,053.8ms, **258배 느림**) |
| 8 Entity Extraction | Rule only | rule company_EM=1.0 correction_EM=1.0 metric_F1=0.971 period_F1=1.0(12μs) vs hcx_only metric_F1=~0.40 period_F1=0.556(7s+) — 압승, trade-off 자체 없음 |
| 9 Router | hcx_structured_router | hcx accuracy=0.800 macro_F1=0.813(4.502s) vs semantic_router accuracy=0.600 macro_F1=0.495(38.7ms) vs agent_only(NoRouter) accuracy=0.0 fallback_rate=1.0(설계상 정상) |
| 10 Agent HCX 모델 | HCX-007 | tool_acc=0.966 arg_acc=0.980 task_success=0.793(13.9s) vs HCX-005 tool_acc=0.897 arg_acc=0.883 task_success=0.552(20.3s) vs HCX-DASH-002 tool_acc=0.567 arg_acc=1.000 task_success=0.233(9.1s) — HCX-007 이 정확도·지연·API호출비용 전부 우위 |
| 11 E2E RAG | 시나리오별 분리(§5 참고) | hybrid_reranker R@5=0.820 NDCG@10=0.783(5.4s) > hybrid_fusion R@5=0.661 NDCG@10=0.731(43ms) > full_agentic R@5=0.622 NDCG@10=0.545(15.7s) task_success=0.759 > bm25_only/dense_only |
| 12 Answer HCX 모델 | HCX-005(answer 역할, Agent 역할과 다름) | pass_rate: HCX-005=0.750 > HCX-007=0.690 > DASH-002=0.321(citation 누락이 주원인) |
| 14 Final E2E(TEST SET n=10, 최초/유일 사용) | efficiency(reranker off, 현재 baseline) | efficiency task_success=0.700 pass_rate=1.000(23.1s) vs best_quality(reranker on) task_success=0.667 pass_rate=0.889(31.1s) — 랭킹지표(MRR/NDCG)는 best_quality 우세하나 최종 답변 성공률/지연은 efficiency 우세, n=10 통계적으로 약함 |

**최종 확정 baseline (Stage 14 결론, 전체 실험 종료)**:
Section-aware+Parent-Child chunking + **Kiwi** tokenizer(Stage 2 는
char_2gram 을 수치상 잠정 1위로 기록했지만, Stage 4 이후 모든 실험은
실제로는 Kiwi 로 진행됐다 — Kiwi 가 형태소 기반이라 도메인 사전
확장(`config/financial_terms.txt`)이 가능하고 char_2gram 은 실무 적용
경로가 불명확해 실질적 baseline 은 계속 Kiwi 였다는 뜻. char_2gram 채택은
보류된 상태로 남겨둔다) + BGE-M3 dense + Normalized Weighted Fusion +
No-Reranker(Stage 5 최초 결론, Stage 14 test set 재확인) + Rule-only Entity
Extraction + HCX structured Router(HCX-005 고정) + **Agent=HCX-007**(Stage
10) + **Answer=HCX-005**(Stage 12, Agent 와 다른 모델). `.env` 의
`HCX_MODEL`은 agent 기본값 HCX-007 로 설정돼 있고, answer 전용 모델
분리는 아직 프로덕션 코드에 반영 안 됨(`ask.py`가 단일 client 재사용 —
§12 향후 과제로 문서화됨). Stage 11 은 이 baseline(=full_agentic)을
유지하되 "정확도 최우선" 대안으로 hybrid_reranker 를 별도 옵션으로 문서화.

**Stage 5 에서 발견한 프로덕션 버그(수정 완료)**: `CrossEncoderReranker` 가
`max_length` 미지정이라 매우 긴 outlier chunk(최대 26,027자) 만나면 처리
시간이 폭증(1500쌍 reranking 이 58분+ 걸림) → `retrieval/reranker.py` 에
`max_length=512` 기본값 추가로 수정, 프로덕션 코드에 반영됨.

---

## 9. 실패했던 접근 — 다시 하면 안 되는 것

1. **HCX Agent system prompt 를 길게 쓰지 말 것.** 6줄 bullet(~400자) system
   prompt 는 tool-calling 2턴째부터 결정적으로 400 에러. 3줄 이내로 유지.
2. **HCX tool-calling 요청에 `tools`+`maxTokens` 동시 사용 금지.** 400 에러.
3. **Qwen3-Embedding-0.6B / Qwen3-Reranker-0.6B 를 이 환경에서 그냥
   재시도하지 말 것.** 4차례 이상 시도(CDN 403, HF_HUB_DISABLE_XET=1 재시도,
   개별 파일 다운로드, 로드 테스트 10분+ 무응답) 후 전부 실패 확정. 재시도
   전에 `HF_TOKEN` 설정 여부, 네트워크 상태, sentence-transformers 버전을
   먼저 점검할 것 — 무작정 재실행은 시간 낭비.
4. **전체 코퍼스(4,204건/45만 chunk)로 Dense 임베딩 비교 실험을 시도하지
   말 것.** CPU-only 환경에서 비현실적(모델당 최소 70시간+). 반드시 축소
   corpus(현재 33개 문서/1,411 chunk)로 진행.
5. **Retrieval 인덱스에 parent chunk 를 포함시키지 말 것.** 반드시
   `filter_leaf_chunks()` 통과. 안 그러면 임베딩이 30분+ 로 폭주(실측).
6. **표 파싱에서 TR 개수 상한(500) 없이 그대로 처리하지 말 것.** malformed
   XML 로 TR 11,786개짜리 표가 실제로 존재함 — 반드시 cap 적용.
7. **NDCG 계산 시 report-level gold 인데 chunk 단위로 relevance 를 중복
   카운트하지 말 것.** `experiments/metrics.py` 의 `ndcg_at_k` 가 이미
   report-level dedup 으로 고쳐져 있음(회귀 테스트로 고정됨) — 이 로직을
   되돌리면 NDCG>1 버그 재발.
8. **HuggingFace 모델 다운로드 시 xet 가속 다운로더를 기본값으로 두지
   말 것.** 이 환경에서 xet CDN 이 403 을 반환하는 경우가 잦았다.
   `HF_HUB_DISABLE_XET=1` 을 기본으로 설정 권장.

---

## 10. 발견된 문제 (버그 픽스 완료된 것 포함, 이력 참고용)

- **[수정됨]** `LIBRARY` XML wrapper 태그를 skip 처리해서 holding 문서
  SECTION 전체가 silent 하게 유실되던 버그.
- **[수정됨]** SECTION 밖 최상위 loose content 가 조용히 버려지던 버그 →
  synthetic section 으로 보존하도록 수정.
- **[수정됨]** colspan 확장 시 같은 값이 3번 반복 출력되던 표 렌더링 버그
  (RLE dedup 미적용) → classify_grid 에서 먼저 dedup 하도록 수정.
- **[수정됨]** 표 분할이 행 수만 기준이라 컬럼이 매우 많은 표(예: 삼성전자
  특수관계자 주석)에서 chunk 가 9,000+ 토큰까지 폭주 → token 예산도 같이
  고려하도록 수정(그래도 헤더 자체가 넓은 극단 케이스는 잔여 — §9-6 참고).
- **[수정됨]** malformed XML 에서 lxml recover 모드가 문서 TR 12,184개 중
  11,786개를 표 하나에 잘못 몰아넣는 심각한 오동작 발견(현대자동차
  정관 5호 의안 표) → TR 500행 cap 으로 방어.
- **[수정됨]** Retrieval 인덱스에 parent chunk 가 섞여 있어 BGE-M3 CPU
  임베딩이 30분+ 로 폭주 → `filter_leaf_chunks()` 로 leaf 만 인덱싱.
- **[수정됨]** `search_disclosures` tool 이 HCX 가 잘못 추측한 period
  포맷("2025-08-15~2026-08-15" 같은 날짜 범위)을 그대로 필터링해 결과
  0건으로 만들던 문제 → 필터 단계적 완화 재시도 로직 추가.
- **[수정됨]** `report_name_contains` 매칭이 원문 특수문자("ㆍ")와 HCX 가
  자연스럽게 쓰는 무구분자 형태를 매칭 못 하던 문제 → 구분자 제거 후 비교.
- **[관찰됨, 미해결]** 일부 chunk 가 여전히 최대 26,027자까지 커지는 경우
  있음(§5 현재 진행 작업 원인 추정) — 표 컬럼 그룹 단위 분할은 추후 개선
  과제로 README 에 이미 기록됨.
- **[관찰됨]** HCX API 가 짧은 시간 연속 호출 시 간헐적으로 400
  ("Unsupported function")을 내는 rate-limit 성 패턴 — exponential backoff
  재시도(3s/6s/12s/24s)로 대부분 우회됨(`hcx_client.py`).
- **[수정됨]** HCX-007(reasoning 모델)은 thinking 모드가 기본 on 상태라
  `tools` 파라미터와 같이 쓰면 400("Invalid parameter: tools, thinking")이
  남 → `thinking={"effort":"none"}`을 명시하면 해결. `hcx_client.py` 의
  `HCXClient`가 모델명에 "007"이 포함되면 자동으로 이 파라미터를 붙이도록
  수정(호출부 무수정으로 HCX-007 사용 가능, Stage 10 에서 발견/수정).
- **[수정됨]** HCX-007 은 max token 제한 파라미터 이름도 다르다 —
  "maxTokens"를 주면 400("Invalid parameter: maxTokens"), 대신
  "maxCompletionTokens"를 써야 정상 동작(Stage 12 답변생성 경로에서 발견
  — agent loop 는 tool-calling 모드라 애초에 max_tokens 를 안 보내서
  Stage 10 에서는 드러나지 않았던 버그). `hcx_client.py`의
  `self._max_tokens_param`이 모델명으로 자동 분기하도록 수정.
- **[수정됨]** periodic 문서의 목차(TOC) 페이지가 표로 파싱돼 그대로
  인덱싱되는 버그 발견(2026-08-15, 대회 참고 질의를 실제 파이프라인에 태워본
  스모크테스트에서 재현 — 정식 Stage 실험 범위 밖). "SECTION 밖 loose
  content 보존" 수정(§10 위 항목) 덕에 유실은 안 되지만, [제목 | 긴 대시
  필러 | 페이지번호] 텍스트가 실제 section 제목과 키워드가 겹쳐 BM25에서
  허위로 높은 점수를 받아 진짜 본문을 top-k 밖으로 밀어냄(실제 질의
  "삼성전자 2023 vs 2025 사업보고서 비교 → 핵심 사업 변화"에서 재현:
  2023년 근거로 목차만 잡혀 "확인할 수 없음"으로 오답). `chunkers.py`에
  `_is_toc_table()` 추가해 목차 표를 chunk 후보에서 제외(파싱 트리 자체는
  보존). 1차 구현은 "-" 한 글자만 봐서 재무표 결측치 표기와 헷갈려
  오탐(내부통제 인력 현황표)이 났고, 대시 필러 최소 길이(10자) 조건을
  추가해 해결 — `tests/test_chunkers.py`에 회귀 테스트 2건 추가.
  **중요 caveat**: 삼성전자 100개 문서 중 6건, 300개 periodic 샘플 중 6건
  꼴로 존재하는 것으로 확인(약 2%) — Stage 1~14 ablation 은 이 버그가
  있는 상태로 진행됐다. Report-level Recall/MRR/NDCG 지표는 "정답 리포트의
  아무 chunk나 top-k에 있으면 성공"으로 채점하므로 목차 chunk가 걸려도
  성공으로 잡혀 **지표가 실제보다 살짝 유리하게 나왔을 가능성**이 있다
  (안전 방향은 아님 — 재실행하면 오히려 소폭 하락할 수 있음). 전체
  Stage 1~14 재실행은 하지 않았다(범위 밖) — 재검증이 필요하면 이 caveat
  부터 확인할 것.
- **[수정됨]** "몇 건이야?" 같은 카운팅 질문이 route=calculation 으로
  오분류되면, agent 가 `calculate_cagr`을 완전히 동일한 무의미한 인자
  (n_years=0 등 이미 실패가 확정된 입력)로 여러 번 연속 호출하다 포기하는
  버그 발견(2026-08-16, 회사 일반화 스모크테스트 — 한미반도체 "2024년
  체결 계약 몇 건" 질문에서 재현, 정답 5건인데 "확인 불가"로 오답).
  `agent_loop.py`에 두 가지 수정: (a) AGENT_SYSTEM_PROMPT 에 "개수를 세는
  질문은 검색 결과 개수로 직접 답하세요" 추가(186자, 300자 안전마진 유지),
  (b) 이름+인자가 완전히 동일한 tool 호출은 실제로 재실행하지 않고 캐시된
  결과+안내 메시지를 돌려주는 dedup guard 추가(모든 route 공통 적용,
  calculate_cagr 특정 문제가 아니라 구조적 방지책). 재검증: 동일 질의
  재실행 시 5건 정답, 반복 호출 없음 확인. 회귀 테스트:
  `tests/test_agent.py::test_agent_loop_skips_redundant_identical_tool_calls`.
- **[수정됨]** Validator 오탐 2건 발견(2026-08-16, 같은 스모크테스트).
  (a) `get_correction_history`만 호출돼 `evidence_pack.citations`가
  비어있으면 답변이 근거를 정확히 인용해도 무조건 `has_citation=False`로
  잡힘 — `validator.py`가 `tool_results_summary`에서 report_id 패턴
  (`_DOC_ID_PAT`)을 스캔하도록 수정. (b) "7조 6,615억원"처럼 같은 숫자를
  조/억 단위로 재표기한 괄호 `(약 ...)` 안 숫자가 evidence 원문과 글자
  그대로 안 겹친다는 이유로 "근거 없는 숫자"로 오탐 — `_extract_numbers`
  가 `(약 ...)` 괄호를 grounding 검사 전에 제거하도록 수정. 재검증: 둘 다
  재실행 후 정상(citation=True, ungrounded=set()) 확인. 회귀 테스트:
  `test_validator_has_citation_from_correction_history_tool_result`,
  `test_validator_ignores_approx_paren_restatement`.

---

## 11. 남은 TODO (Stage 순서대로)

- [x] **Stage 5 — Reranker**: 완료. `results/reranker/` 전체 커밋됨.
- [x] **Stage 8 — Entity Extraction**: 완료. Rule only 압승.
      `results/entity/` 전체(failure_analysis.md 포함) 커밋됨.
- [x] **Stage 9 — Router**: 완료. hcx_structured_router 채택.
      `results/router/` 전체(failure_analysis.md 포함) 커밋됨.
- [x] **Stage 10 — Agent HCX 모델**: 완료. HCX-007 채택, `.env` 갱신됨.
      `results/agent/`(failure_analysis.md 포함) 커밋됨.
- [x] **Stage 11 — E2E RAG**: 완료. 단일 winner 없음 — hybrid_reranker(정확도
      최우선)와 full_agentic(현재 baseline, task_success 강함이나 필터 과도
      축소로 랭킹 품질은 raw retrieval 보다 낮음)으로 시나리오 분리 권고.
      `results/e2e_rag/`(failure_analysis.md 포함) 커밋됨.
- [x] **Stage 12 — Answer HCX 모델**: 완료. HCX-005 채택(answer 역할).
      `results/answer/`(failure_analysis.md 포함) 커밋됨.
- [x] **Stage 14 — Final E2E**: 완료. test set(n=10) 최초/유일 사용,
      efficiency(reranker off) 유지 결론. `results/e2e_final/`
      (failure_analysis.md 포함) 커밋됨.
- [x] 각 Stage 리포트 형식 `[Experiment]/[Candidates]/[Metrics]/[Best]/
      [Trade-off]/[Failure Cases]/[Recommendation]`: 매 Stage 완료 시
      사용자에게 이 형식으로 보고 완료(대화 로그 참고, 별도 저장 파일 없음).
- [x] **모든 실험 완료 후 `results/` 전체를 하나의 최종 요약 문서로 정리**
      (사용자가 "이거 다 돌리고 폴더 하나 파서 잘 정리해줘" 라고 요청함) —
      완료. `results/FINAL_SUMMARY.md`(git 커밋됨) + Artifact 대시보드
      게시(claude.ai 링크, git 미포함 — 세션에서 사용자에게 전달됨).
      **모든 실험 arc 종료.**

---

## 12. 다음에 바로 해야 할 작업

**Stage 1~14 실험 arc는 완전히 종료됨.** 현재는 그 뒤에 시작된 **"회사
일반화 검증"** 트랙이 진행 중이다(§5 참고).

### 지금 막혀있는 것 (외부 의존)
- **전체 70개사 코퍼스 GPU 임베딩이 서버에서 진행 중** — 로컬로 아직
  안 받아옴. 서버에서 macOS AppleDouble 파일 오염 문제가 있었으나 해결됨
  (서버측 Codex 가 처리). 결과 도착하면:
  1. `gpu_embeddings/shard_*.pkl` 을 로컬 `~/Desktop/공시 agent/gpu_embeddings/`
     로 받아오기(각 파일이 `{"chunks":[...], "vectors":[...], "dim":1024}`
     형태 — 합칠 필요 없이 순회하며 `QdrantVectorStore.upsert_chunks()`에
     이어붙이면 전체 인덱스 완성).
  2. `results/generalization_check/matrix.csv`에 다른 업종(금융지주/바이오/
     조선/자동차 등) 회사를 추가해 커버리지 확장 — 대회 6개 카테고리
     프레임 그대로 유지.

### 지금 바로(임베딩 없이) 할 수 있는 것 — 이미 진행/완료한 것
- [x] 카운팅 오분류 + tool 중복호출 버그 수정 (§10)
- [x] Validator 오탐 2건 수정 (§10)
- [x] 회귀 테스트 3건 추가(`tests/test_agent.py`)
- [x] `results/generalization_check/` 폴더 생성(matrix.csv + summary.md +
      raw 실행 결과 2건)
- [ ] **아직 미해결**: "복합문서추론_Open"(다년도/장문 비교, 예: "2023 vs
      2025 사업보고서 비교", "해지 이력 시간순 정리") 유형이 top_k=5
      retrieval breadth 부족으로 반복 실패 — 삼성전자·한미반도체 두 회사
      모두에서 같은 계열 문제로 재현됨. 다음 우선순위 후보로 유력.
      개선 방향: (a) broad narrative 질의에 top_k 확대, (b) agent 가 하위
      키워드로 여러 번 나눠 검색하도록 유도, (c) 섹션 전체(parent chunk)를
      통째로 가져오는 전용 tool.

### 다음 세션에서 이어갈 만한 다른 후보 (사용자가 명시적으로 요청할 경우에만)
- Stage 11/14 에서 식별된 `search_disclosures` 필터 완화 로직 개선
  (ownership/보유비율 질의 실패의 근본 원인)
- `ask.py`에 answer 전용 모델(HCX-005) 분리 적용(현재는 agent client 를
  answer 생성에도 재사용 — Stage 12 결론이 프로덕션에 아직 반영 안 됨)
- char_2gram BM25 토크나이저 재검토(Stage 2 에서 수치상 1위였으나 보류)
- test set 표본 확대(Stage 14 의 n=10 한계 보완용 confirmatory set)
- TOC 버그 수정이 Stage 1~14 지표에 준 영향 재검증(§10 caveat 참고 —
  선택적, 비용이 크므로 사용자가 명시적으로 요청할 때만)

### 로컬 임시 캐시 (git 미포함, 세션 재시작 시 사라짐)
- `/tmp/bgem3_chunks_vectors.pkl`: 삼성전자 33개 문서/1,411개 청크(TOC
  수정 반영 안 됨 — 삼성전자는 원래 TOC 문제 없었던 문서들이라 무관).
- `/tmp/extra_companies_vectors.pkl`: 삼성SDI/LG에너지솔루션/한미반도체/
  삼성전자FY2023 총 21개 문서, 2,110개 청크(TOC 수정 반영됨). 재생성 코드는
  세션 스크립트 `embed_extra_companies.py` 참고(scratchpad, 세션 종료 시
  사라짐 — doc_id 목록은 대화 로그 및 `results/generalization_check/`
  raw json 파일에 남아있음).
- `/private/tmp/.../scratchpad/`(세션별 경로) 안의 `competition_check.py`,
  `other_companies_check.py`, `verify_fixes.py`: 4개사 스모크테스트/재검증
  스크립트 원본 — 세션 종료 시 사라지지만 결과는 `results/generalization_check/`
  에 이미 저장됨.
- `~/Desktop/embedding/`, `~/Desktop/embedding.tar.gz`: GPU 서버 전달용
  번들(코드+corpus). GPU 결과 받아온 뒤에는 삭제해도 무방.

참고로 `/tmp/stage_eval_doc_ids.json`, `/tmp/bgem3_chunks_vectors.pkl` 등
   `/tmp` 임시 파일들이 세션 재시작으로 사라졌다면, 아래 코드로 재생성:
   ```python
   # doc_ids 재생성 (삼성전자 33개 문서: periodic 최신 2 + major 전체 19 +
   # exchange 전체 2 + holding 최신 10)
   from disclosure_rag.common.manifest_loader import load_manifest
   manifest = load_manifest("corpus")
   samsung = [r for r in manifest if r.corp_name == "삼성전자"]
   periodic = sorted([r for r in samsung if r.doc_group=="periodic" and not r.is_correction], key=lambda r: r.rcept_dt, reverse=True)[:2]
   major = [r for r in samsung if r.doc_group=="major"]
   exchange = [r for r in samsung if r.doc_group=="exchange"]
   holding = sorted([r for r in samsung if r.doc_group=="holding"], key=lambda r: r.rcept_dt, reverse=True)[:10]
   doc_ids = [r.doc_id for r in periodic+major+exchange+holding]
   ```
   `eval/gold_queries.json` (git에 커밋되어 있음, 40개 gold query)은 그대로 사용.
5. TaskList 로 진행상황 재확인 (Task #14~25, id 25가 마지막 미완료 작업).
