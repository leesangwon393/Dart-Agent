# Stage 11 — E2E RAG: Failure Analysis

## 핵심 관찰
5개 후보를 **같은 report-level Recall/MRR/NDCG 공식**으로 비교한 결과,
raw retrieval 관점에서는 `hybrid_reranker`(R@5=0.820, R@10=0.910,
MRR=0.765, NDCG@10=0.783)가 가장 강하다 — Stage 5 결론(CPU 배포엔
No-Reranker)과 모순되지 않는다, 여긴 "정확도만" 볼 때의 순위이고 지연은
여전히 5.4초/질의로 258배 비싸다는 사실은 그대로다.

**가장 중요한 발견**: `full_agentic`(HCX-007 agent, Stage 8/9/10 winner
조합)은 raw retrieval 4개 후보 중 어느 것보다도 랭킹 품질 지표가
낮다(R@5=0.622, NDCG@10=0.545 — `bm25_only`보다도 낮음). 반면
`task_success_rate`(결국 정답 근거를 찾았는가, k 무제한)는 0.759 로
받아들일 만한 수준이다. 즉 **agent 는 "언젠가는 찾는다"에는 강하지만,
"한 번에 좋은 순서로 찾는다"에는 약하다** — 여러 차례 tool 호출로
누적된 결과의 첫-발견 순서를 랭킹으로 재구성했을 때 그 순서 자체가
retrieval 엔진의 스코어 정렬만큼 정교하지 않기 때문(§ 아래 근본원인).

| Variant | R@5 | R@10 | MRR | NDCG@10 | Latency | Task Success |
|---|---|---|---|---|---|---|
| bm25_only | 0.706 | 0.802 | 0.682 | 0.667 | 0.2ms | – |
| dense_only | 0.594 | 0.840 | 0.644 | 0.663 | 135.5ms | – |
| hybrid_fusion | 0.661 | 0.900 | 0.711 | 0.731 | 42.6ms | – |
| **hybrid_reranker** | **0.820** | **0.910** | **0.765** | **0.783** | 5,391.9ms | – |
| full_agentic | 0.622 | 0.640 | 0.586 | 0.545 | 15,733.8ms | 0.759 |

## Full Agentic 이 raw retrieval 보다 랭킹이 나쁜 이유
- **필터 과도 축소로 인한 0건**(가장 큰 원인, event_analysis 4건:
  id=37,38,39 + ownership_analysis id=26,27,30): entity extractor 가 뽑은
  period/report_type 필터를 `search_disclosures` 가 그대로 걸어 검색하는데,
  Coarse-to-Fine 완화 재시도 로직(§tools.py)이 있음에도 여전히 0건으로
  끝나는 케이스가 있다 — 순수 hybrid_reranker(필터 없이 raw query 텍스트만
  사용)는 같은 질의(id=30,19 제외)에서 대부분 성공했다는 점이 이를
  뒷받침한다. **필터가 없는 raw retrieval 이 필터 있는 agent 검색보다
  이 corpus/질의 분포에서는 더 안전하다**는 역설적 결과.
- **여러 tool 호출의 first-seen 순서가 진짜 랭킹이 아님**: agent 가
  검색어를 바꿔가며 2~4번 검색하면 각 호출의 top-5 결과가 이어붙는
  형태가 되는데, 두 번째 호출에서 나온 진짜 정답이 첫 호출의 오답들보다
  뒤에 오게 되어 MRR/NDCG 가 raw single-shot 검색보다 나빠진다 — Stage
  10 failure_analysis 에서 지적한 "tool 은 맞게 불렀지만 근거 실패"의
  랭킹판 버전.
- **correction_analysis 질의의 낮은 recall**(id=9,10 recall_at_10<1.0인데
  task=True): `get_correction_history`가 메타데이터만 반환하고
  `search_disclosures`를 원본/정정본 각각의 report_id 로 재호출하는
  단계까지는 가지만, gold 가 요구하는 두 문서 전부를 커버하기 전에
  `no_more_tool_calls`로 멈추는 경우가 있음(부분 recall).

## Full Agentic 이 그래도 이기는 지점(raw retrieval 이 못하는 것)
raw retrieval 4개는 "정답 문서가 top-k 안에 있는가"만 잴 뿐, 실제로
문서 내용을 읽고 계산(calculate_*)하거나 정정 이력을 재구성
(get_correction_history)하는 것은 하지 못한다. Stage 10 에서 확인했듯
HCX-007 agent 는 tool_accuracy=0.966, argument_accuracy=0.980 로 이 역할
자체는 잘 수행한다 — 이번 Stage 11 의 랭킹 지표 열세는 "검색을 못해서"가
아니라 "여러 근거를 조합하는 과정에서 랭킹이 흐트러져서"이므로, 최종
답변 품질(Stage 12 에서 평가)에는 랭킹 순서보다 "정답 근거가 evidence
pack 안에 존재하는가"(task_success_rate=0.759)가 더 직접적인 결정 요인일
가능성이 높다.

## 결론 / 권고
**단일 winner 를 고르지 않는다** — 두 축이 다른 것을 잰다:
- **정확도 최우선/지연 무관 시나리오**: `hybrid_reranker`. 5.4초는
  agent 의 15.7초보다도 짧고 랭킹 품질도 가장 좋다.
- **CPU 배포/실시간성 중시(현재 baseline)**: `hybrid_fusion`(No-Reranker,
  Stage 5 결론과 동일) 을 retrieval 엔진으로 유지하되, tool-calling
  계산/정정이력 조합이 필요한 질문에는 `full_agentic` 경로를 쓴다 —
  즉 현재 아키텍처(hybrid_fusion 기반 tools + HCX-007 agent)가 이미
  합리적인 절충이라는 게 재확인됐다.
- **개선 과제로 남김**: `search_disclosures` 의 필터 완화 로직을 한 단계
  더 완화(예: period 필터를 먼저 빼고 재시도하는 순서를 재검토)하면
  Full Agentic 의 0건 실패(6/29)를 상당수 줄일 수 있을 것으로 보임 —
  Stage 1/4/10 에서 반복 관찰된 "ownership/event 관련 질의의 corpus
  난이도"와 겹치는 부분이 있어, 다음 개선 시 이 필터 로직부터 점검할 것.
