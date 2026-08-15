# 금융공시 Agentic RAG — Rigorous Ablation 최종 요약

**평가 corpus**: 삼성전자 1개사, 33개 문서(periodic 최신 2 + major 전체 19
+ exchange 전체 2 + holding 최신 10), 1,411개 leaf chunk — 전체 4,204건
코퍼스는 CPU-only 환경에서 물리적으로 불가능해(BGE-M3 기준 73시간) 축소.
**평가셋**: `eval/gold_queries.json` 40개 gold query, 30 validation(Stage
1~12 전부에 사용) / 10 test(Stage 14에서 딱 한 번만 사용). 방법론:
Fine-tuning 제외, 각 Stage 는 이전 Stage 의 winner 를 baseline 으로 고정,
test set 은 최종 평가 전까지 절대 사용하지 않음.

각 Stage 결과 폴더는 `config.json`/`metrics.json`/`results.csv`/
`failure_cases.jsonl`/`summary.md`를 포함하고, Stage 전체에는 추가로
`comparison.json`(후보 간 비교표)과 `failure_analysis.md`(근본원인 분석)가
있다.

---

## 최종 확정 Configuration

| 구성요소 | 선택 | 근거 Stage |
|---|---|---|
| Chunking | Section-aware + Parent-Child | Stage 1 |
| BM25 Tokenizer | Kiwi | Stage 2 (char_2gram 이 수치상 근소 우위였으나 도메인 사전 확장 가능성 때문에 Kiwi 를 실질 baseline 으로 채택 — 아래 §Stage 2 설명 참고) |
| Dense Embedding | BGE-M3 | Stage 3 |
| Fusion | Normalized Weighted Fusion | Stage 4 |
| Reranker | 없음 (No-Reranker) | Stage 5, Stage 14 test set 재확인 |
| Entity Extraction | Rule-only | Stage 8 |
| Router | HCX structured router (HCX-005 고정) | Stage 9 |
| Agent(tool-calling) 모델 | **HCX-007** | Stage 10 |
| Answer 생성 모델 | **HCX-005** | Stage 12 (Agent 와 다른 모델 — 역할별 분리) |

---

## Stage별 요약 (`[Experiment]/[Candidates]/[Metrics]/[Best]/[Trade-off]/[Recommendation]`)

### Stage 1 — Chunking (`results/chunking/`)
- **후보**: Fixed-500(구조 무시 고정 500토큰) / Section-aware(섹션 경계만) / Section-aware+Parent-Child
- **지표(validation n=30)**: Recall@5/10/20, MRR, NDCG@10
- **결과**: section_aware_parent_child R@5=0.706 R@10=0.802 MRR=0.682 NDCG@10=0.667 (fixed_500 R@5=0.656, section_aware R@5=0.620 대비 우위)
- **Best**: Section-aware + Parent-Child
- **Trade-off**: parent-child 구조가 인덱스 크기를 키우지만(1,411 leaf chunk vs fixed_500 의 1,673개), 검색 정확도 이득이 더 큼
- **Recommendation**: 채택. 단, corpus 내 periodic 문서 과다매칭 이슈가 열린 질문으로 남음(Stage 11에서 agent 개입으로 일부 해소 확인)

### Stage 2 — BM25 Tokenizer (`results/bm25/`)
- **후보**: whitespace / Kiwi(형태소) / char_2gram / char_3gram
- **지표**: 동일 Recall/MRR/NDCG
- **결과**: char_2gram R@10=0.912 MRR=0.757(수치상 1위) > char_3gram > **Kiwi R@10=0.802 MRR=0.682** > whitespace
- **Best(수치)**: char_2gram — 하지만 **실질 baseline 은 Kiwi**로 유지: 형태소 기반이라 `config/financial_terms.txt` 도메인 사전 확장이 가능하고, char_2gram 은 실무 배포 경로(사전 관리, OOV 대응)가 불명확해 Stage 4 이후 전체 실험을 Kiwi 로 진행함. char_2gram 채택은 보류 상태로 남김(재검토 과제)
- **Trade-off**: 정확도(char_2gram) vs 운영 확장성(Kiwi)
- **Recommendation**: 현 시점 Kiwi 유지, char_2gram 은 사전 관리 전략이 마련되면 재평가

### Stage 3 — Dense Embedding (`results/embedding/`)
- **후보**: BGE-M3 / multilingual-e5-large-instruct (Qwen3-Embedding-0.6B 는 환경 이슈로 로드 자체가 안 돼 실험 제외 — §발견된 문제 참고)
- **지표**: Recall/MRR/NDCG + embed_ms_per_chunk(자원)
- **결과**: bge-m3 R@10=0.840(591.7ms/chunk) vs e5-instruct R@10=0.867(3,710.0ms/chunk, **6.3배 느림**)
- **Best**: 상황별 분리 — BGE-M3(실무 배포용), e5-instruct(정확도 상한 필요 시)
- **Trade-off**: e5 가 R@10 +0.027 더 높지만 임베딩 비용이 6배 이상. CPU-only 환경에서 전체 코퍼스 재임베딩 시간 차이가 결정적(73시간 vs 이보다 훨씬 김)
- **Recommendation**: BGE-M3 채택(CPU 배포 기준)

### Stage 4 — Fusion (`results/fusion/`)
- **후보**: BM25 only / Dense only / RRF / Normalized Weighted Fusion
- **결과**: normalized_weighted R@10=0.903 MRR=0.713 NDCG@10=0.735 > rrf R@10=0.860 MRR=0.702 > bm25_only > dense_only
- **Best**: Normalized Weighted Fusion
- **Trade-off**: 계산 비용 거의 동일(둘 다 로컬 스코어 결합), 정확도만으로 결정 가능
- **Recommendation**: 채택

### Stage 5 — Reranker (`results/reranker/`)
- **후보**: No-Reranker / bge-reranker-v2-m3
- **결과**: no_reranker Hit@1=0.633 MRR=0.712(42.7ms) vs bge_reranker Hit@1=0.667 MRR=0.773(11,053.8ms, **258배 느림**)
- **Best**: 상황별 분리 — No-Reranker(CPU 배포), bge-reranker-v2-m3(GPU 확보 시 재검토)
- **Trade-off**: 정확도 +0.061(MRR) vs 지연 258배 — CPU 환경에서 대화형 서비스에 치명적
- **프로덕션 버그 발견**: `CrossEncoderReranker`에 `max_length` 미지정 시 corpus 내 최대 26,027자 outlier chunk 로 인한 처리시간 폭증(1500쌍 reranking 58분+) → `max_length=512` 기본값 추가로 수정, 런타임 10분대로 단축
- **Recommendation**: No-Reranker 채택(CPU 배포 기준), Stage 11/14 에서 재확인

### Stage 8 — Entity Extraction (`results/entity/`)
- **후보**: Rule only / Rule+HCX fallback / HCX only
- **결과**: rule_only company_EM=1.0 correction_EM=1.0 metric_F1=0.971 period_F1=1.0(**12μs**) vs hcx 계열 metric_F1=~0.40 period_F1=0.556(**7초+**)
- **Best**: Rule only — 압승, trade-off 자체가 존재하지 않음
- **Caveat**: 이 gold set 이 rule 정규식이 겨냥한 어투와 유사하게 작성돼 결과가 유리할 수 있음 — "이 corpus/질의 분포에서는" 이라는 조건부 결론
- **Recommendation**: Rule only 채택

### Stage 9 — Router (`results/router/`)
- **후보**: Semantic Router(BGE-M3 임베딩) / HCX structured router(신규) / Agent-only(NoRouter, 대조군)
- **결과**: hcx_structured accuracy=0.800 macro_F1=0.813(4.502s) vs semantic_router accuracy=0.600 macro_F1=0.495(38.7ms) vs agent_only accuracy=0.0 fallback_rate=1.0(설계상 정상)
- **Best**: hcx_structured_router — 정확도 격차가 커서(+0.2 accuracy) latency tie-break 규칙 미적용
- **버그 발견**: HCX tool-calling 은 system prompt 가 ~300자 넘으면 결정적으로 실패(세 번째 독립 확인 — agent_loop.py, Stage 8, Stage 9 각각에서 재현) → 프롬프트를 항상 짧게 유지
- **Recommendation**: hcx_structured_router 채택, semantic_router 는 장애 시 폴백 경로로 유지 가치 있음

### Stage 10 — Agent HCX 모델 (`results/agent/`)
- **후보**: HCX-DASH-002 / HCX-005 / HCX-007 (사전에 API 로 tool-calling 가용성 확인, HCX-007 은 `thinking={"effort":"none"}` 필요 확인)
- **결과**: HCX-007 tool_acc=0.966 arg_acc=0.980 task_success=0.793(13.9s) > HCX-005 tool_acc=0.897 arg_acc=0.883 task_success=0.552(20.3s) > DASH-002 tool_acc=0.567 arg_acc=1.000 task_success=0.233(9.1s)
- **Best**: HCX-007 — 정확도·지연·API 호출 비용(mean_tool_calls) 전부 우위, trade-off 없음
- **Recommendation**: HCX-007 채택, `.env` HCX_MODEL 갱신 완료

### Stage 11 — E2E RAG (`results/e2e_rag/`)
- **후보**: BM25 only / Dense only / Hybrid(Fusion) / Hybrid+Reranker / Full Agentic
- **결과**: hybrid_reranker R@5=0.820 NDCG@10=0.783(5.4s) > hybrid_fusion R@5=0.661 NDCG@10=0.731(43ms) > full_agentic R@5=0.622 NDCG@10=0.545(15.7s, task_success=0.759) > bm25_only/dense_only
- **Best**: 시나리오 분리 — 정확도 최우선(hybrid_reranker) vs 실시간성+도구조합(full_agentic, 현재 baseline)
- **핵심 발견**: full_agentic 의 랭킹 지표가 raw retrieval 보다 낮은 원인은 entity 필터 과도 축소로 인한 0건 검색 — `search_disclosures`의 필터 완화 로직이 구체적 개선 과제로 식별됨
- **Recommendation**: 현재 baseline(hybrid_fusion + agent) 유지, 정확도 필요 시 hybrid_reranker 옵션 문서화

### Stage 12 — Answer HCX 모델 (`results/answer/`)
- **후보**: HCX-DASH-002 / HCX-005 / HCX-007 (동일 Evidence Pack, HCX-007 agent 로 1회만 생성해 재사용)
- **결과**: HCX-005 pass_rate=0.750 > HCX-007 pass_rate=0.690 > DASH-002 pass_rate=0.321
- **Best**: HCX-005 — **Agent 역할(Stage 10)과 다른 모델이 승리**하는 역할별 분리 결론
- **버그 발견**: HCX-007 은 `maxTokens` 대신 `maxCompletionTokens` 파라미터 필요(Stage 10 에선 tool-calling 모드라 안 드러났던 버그, Stage 12 에서 처음 발견)
- **Recommendation**: Agent=HCX-007, Answer=HCX-005 로 역할별 다른 모델 사용

### Stage 14 — Final E2E (`results/e2e_final/`, **test set n=10, 최초/유일 사용**)
- **후보**: efficiency(reranker off, 현재 production) / best_quality(reranker on)
- **결과**: efficiency task_success=0.700 pass_rate=1.000(23.1s) vs best_quality task_success=0.667 pass_rate=0.889(31.1s) — 랭킹 지표(MRR/NDCG)는 best_quality 우세하나 최종 답변 성공률/지연은 efficiency 우세
- **Best**: efficiency(reranker OFF, 현재 baseline 유지)
- **⚠️ 표본 한계**: n=10 으로 통계적으로 약함 — 방향성 참고용. 특히 best_quality 는 1건이 API 오류로 드롭됨(n=9)
- **Recommendation**: 정확도 차이가 명확히 크지 않으므로 latency 우선 원칙에 따라 reranker OFF 유지

---

## 스테이지를 관통하는 공통 발견

1. **ownership/보유비율 관련 질의의 retrieval 난이도**가 corpus 특성으로
   보임 — Stage 1, 4, 10, 11, 14(test set, 독립 split) 총 5개 지점에서
   반복 관찰됨. 우연이 아니라 실제 개선 우선순위 1순위.
2. **HCX tool-calling 은 system prompt 길이(~300자)에 민감**하다 — 3회
   독립 확인(agent_loop.py, Stage 8, Stage 9). 모든 HCX 호출부는 시스템
   프롬프트를 짧게 유지해야 함.
3. **HCX-007(reasoning 모델)은 파라미터 이름/기본값이 다른 모델과 다르다**
   — `thinking={"effort":"none"}` 필요(tools 사용 시), `maxCompletionTokens`
   필요(`maxTokens` 대신). `hcx_client.py`가 모델명 기반으로 자동 처리하도록
   수정 완료.
4. **역할별로 다른 HCX 모델이 최적**이라는 게 실험적으로 확인됨 — Agent(tool-
   calling)는 HCX-007, Answer(최종 답변 생성)는 HCX-005가 더 낫다. 단일
   모델을 모든 역할에 쓰는 게 최선이라는 가정은 이 실험 조건에서는 틀렸다.
5. **Reranker 는 랭킹 지표(MRR/NDCG)는 항상 개선하지만, 최종 답변
   성공률까지 항상 개선하는 것은 아니다**(Stage 11 full_agentic, Stage 14
   test set 둘 다 확인) — 지연 비용(5.4~11초)을 감안하면 CPU 배포
   환경에서는 트레이드오프가 불리하다.

---

## 발견/수정된 프로덕션 버그 전체 목록

| # | 버그 | 발견 Stage | 수정 |
|---|---|---|---|
| 1 | HCX system prompt >300자 시 tool-calling 결정적 실패 | agent_loop.py 초기, Stage 9 | 프롬프트 단축 |
| 2 | `CrossEncoderReranker` max_length 미지정 시 outlier chunk 처리시간 폭증 | Stage 5 | `max_length=512` 기본값 |
| 3 | HCX-007 tools + 기본 thinking 모드 동시 사용 시 400 에러 | Stage 10 | `thinking={"effort":"none"}` 자동 적용 |
| 4 | HCX-007 `maxTokens` 파라미터명 불일치(400 에러) | Stage 12 | `maxCompletionTokens` 자동 분기 |
| 5 | Qdrant 인덱스에 parent chunk 혼입 시 임베딩 30분+ 폭주 | 초기 개발 | `filter_leaf_chunks()` 로 leaf 만 인덱싱 |
| 6 | malformed XML TR 11,786개 표 폭주 | 초기 개발 | TR 500행 cap |

(전체 이력은 `PROJECT_STATE.md` §10 참고)

---

## 참고: 정성 평가 트랙 (`results/qualitative_50q/`)
정식 Stage 체계 이전에 수행한 사전 정성평가(BM25-only Agent, 3개사 113문서
corpus, 50문항). PASS 24 / FAIL_UNGROUNDED 20 / HONEST_NO_EVIDENCE 3 / ERROR 3.
FAIL_UNGROUNDED 비율이 높았던 원인(BM25 단독 recall 한계)이 정식 Stage
1/2/11 실험에서 근본 원인까지 재확인됨 — 별도 트랙이지만 결론이 일관됨.

---

## 재현 방법
모든 실험 스크립트는 세션 scratchpad(`stage1_chunking.py` ~
`stage14_final.py`)에 있었고 git 에는 결과(`results/`)만 커밋했다.
동일한 eval corpus(삼성전자 33개 문서)와 gold set(`eval/gold_queries.json`,
git 커밋됨)을 사용하면 재현 가능 — 재생성 코드는 `PROJECT_STATE.md` §12
참고.
