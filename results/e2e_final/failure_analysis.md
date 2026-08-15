# Stage 14 — Final E2E: Failure Analysis

## ⚠️ 표본 크기 경고
**n=10 (test set 전체) — 통계적으로 매우 약하다.** 이 Stage 의 결론은
"방향성 참고용"으로만 해석해야 하며, 단일 질의 하나의 성패가 지표를
±10%p 흔든다. validation set(n=30, Stage 1~12)에서 이미 반복 검증된
결론(예: reranker 가 랭킹 품질 지표는 개선한다)과 이 Stage 의 결과가
어긋나는 부분이 있다면, 표본 크기 차이가 원인일 가능성을 우선 고려해야
한다.

## 핵심 관찰 (test set, 최초/유일 사용)

| Config | R@5 | R@10 | MRR | NDCG@10 | Task Success | Pass Rate | Total Latency |
|---|---|---|---|---|---|---|---|
| **efficiency**(reranker off, 현재 production) | 0.533 | 0.558 | 0.323 | 0.386 | **0.700** | **1.000** | **23.1s** |
| best_quality(reranker on) | 0.481 | 0.574 | **0.454** | **0.461** | 0.667 | 0.889 | 31.1s |

랭킹 지표(MRR/NDCG@10)는 validation set(Stage 11)과 같은 방향으로
best_quality 가 우세하다 — 이는 일관된 신호로 신뢰할 만하다. 하지만
**최종 산출물 지표(task_success, pass_rate)는 오히려 efficiency 가 근소
우위**이고, 지연은 efficiency 가 8초 더 빠르다. best_quality 는 test set
10건 중 1건(id=12)에서 HCX 400("Unsupported function") 재시도 4회가 모두
실패해 통째로 드롭됐다(n=9) — reranker 로 인해 tool 호출 라운드트립이
늘어나면서 rate-limit 에 더 자주 부딪힌 것으로 추정(Stage 10/12
failure_analysis 에서도 HCX-007 이 다른 모델보다 429/400 을 더 자주
겪는다는 관찰과 일관됨).

## 두 config 공통 실패 (모델/reranker 무관 — 진짜 corpus/질의 난이도)
- **id=20**("취득예정금액이 취득예정주식수 대비 1주당 얼마인지 계산해줘"):
  두 config 모두 recall@10=0.0 — 애초에 관련 근거를 못 찾아 계산 자체가
  불가능. Stage 10 failure_analysis 의 calculation route 취약점과 같은
  계열.
- **id=32**("자기주식 보유 비율 알려줘"): 두 config 모두 recall@10=0.0 —
  Stage 1/4/10/11 에서 반복 관찰된 **ownership/보유비율 관련 질의의 corpus
  retrieval 난이도**가 test set 에서도 동일하게 재현됨. 4개 독립적
  스테이지·2개 독립적 split(validation/test) 에서 일관되게 나타나는
  패턴이므로, 우연이 아니라 실제 corpus 특성(관련 정보가 흩어져 있거나
  질의 어휘와 문서 어휘의 lexical/semantic gap)으로 봐야 한다 — 향후
  개선 시 최우선 조사 대상.

## best_quality 에서만 실패한 케이스
- **id=28**("삼성물산 보유목적이 뭐야?"): efficiency 는 recall@10=1.00 으로
  성공했지만 best_quality(reranker 적용)는 0.0 으로 실패 — **reranker 가
  정답을 top-k 밖으로 밀어낸 구체적 사례**. Stage 5 에서는 reranker 가
  평균적으로 랭킹을 개선한다고 결론 냈지만, 개별 질의 단위로는 역효과가
  날 수 있다는 반례. 표본이 1건뿐이라 일반화할 수 없지만, "reranker 는
  항상 개선"이라는 가정을 무비판적으로 받아들이면 안 된다는 근거로 기록.
- **id=12**: 위에서 설명한 client exception(reranker 로 인한 추가 라운드
  트립 → rate limit).

## 결론 / 권고
**baseline(=efficiency, reranker off)을 유지한다.** 근거:
1. Task Success/Pass Rate 등 사용자가 실제로 체감하는 최종 지표에서
   근소하게 더 우수하거나 최소한 뒤지지 않는다.
2. 지연이 8초(23.1s vs 31.1s, 약 26%) 더 짧다.
3. reranker 로 인한 추가 API 라운드트립이 신뢰성 저하(client exception
   1건)로 이어지는 구체적 사례가 관측됐다.
4. MRR/NDCG 같은 순수 랭킹 지표는 best_quality 가 낫지만, 이는 Stage 11
   에서 이미 확인된 방향성이고, 이번 test set 재확인은 "그 개선이
   최종 답변 성공률로 항상 이어지지는 않는다"는 추가 통찰을 준다.

**다만 n=10 이라는 한계 때문에 이 결론을 최종 확정으로 못박지는 않는다.**
더 큰 test set(예: 다른 회사로 confirmatory set 을 추가 구축)으로 재검증
하기 전까지는, 사용자 지침("정확도 차이가 미미하면 latency/resource 로
결정")을 그대로 적용해 **reranker OFF(현재 production baseline)를 유지**
하는 것이 데이터에 기반한 합리적 선택이다.

## 최종 확정 Configuration
- Chunking: Section-aware + Parent-Child (Stage 1)
- BM25 Tokenizer: Kiwi (Stage 2)
- Dense Embedding: BGE-M3 (Stage 3)
- Fusion: Normalized Weighted Fusion (Stage 4)
- Reranker: **없음** (Stage 5, Stage 14 재확인)
- Entity Extraction: Rule-only (Stage 8)
- Router: HCX structured router, HCX-005 고정 (Stage 9)
- Agent(tool-calling): **HCX-007** (Stage 10)
- Answer 생성: **HCX-005** (Stage 12)
