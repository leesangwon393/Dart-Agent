# Stage 5 — Reranker: Failure Analysis + 회귀 버그 기록

## Qwen3-Reranker-0.6B 제외
Stage 3 의 Qwen3-Embedding-0.6B 와 정확히 같은 패턴(로딩 시 CPU 거의 0%,
RSS 만 늘었다 줄었다 하며 10분+ 무응답)으로 실패. 이번엔 다운로드는
완료됐음(1.1GB, 로컬 파일 존재 확인)에도 `CrossEncoder(...)` 로드 자체가
멈춤. Qwen3 계열 모델이 이 환경의 sentence-transformers 로딩 경로와 구조적으로
맞지 않는 것으로 최종 판단 — 두 개의 서로 다른 Qwen3 모델(Embedding, Reranker)
이 동일한 증상을 보였으므로 우연이 아니라 환경/라이브러리 호환성 문제로 결론.

## 회귀 버그 발견: reranker max_length 미지정으로 인한 극단적 지연
최초 실행 시 `bge_reranker_v2_m3` 1개 후보 평가(30 query × 50 candidate = 1500
쌍)가 **58분+**(정상 예상 5~10분) 걸려 강제 종료 후 원인 조사:
`CrossEncoder(model_name, device=device)` 호출에 `max_length` 를 안 줘서
truncation 이 전혀 적용되지 않고 있었다. corpus 안에 malformed XML 표 파싱
잔여 문제로 여전히 최대 26,027자짜리 chunk 가 존재하는데(§results/chunking
발견, 완전히 해소 안 됨), 이런 chunk 가 reranking candidate 로 들어가면
quadratic attention 비용으로 1건 처리에 수 분씩 걸렸던 것으로 추정.
`retrieval/reranker.py::CrossEncoderReranker.__init__` 에 `max_length=512`
기본값을 추가해 수정(프로덕션 코드에도 반영됨, 회귀 테스트로 명시 안 했으나
Stage 11/14 재사용 시 자동으로 적용됨).

## 정확도 vs 지연 트레이드오프
| | Hit@1 | Hit@3 | MRR | NDCG@5 | mean latency |
|---|---|---|---|---|---|
| no_reranker | 0.633 | 0.733 | 0.712 | 0.672 | 42.7ms |
| bge_reranker_v2_m3 | 0.667 | 0.833 | 0.773 | 0.765 | **11,053.8ms** |

reranker 가 4개 지표 전부 개선(특히 Hit@3 +0.1, NDCG@5 +0.093)하지만, 지연이
**258배**(쿼리당 11초) 늘어난다. 이는 Stage 3 의 임베딩 지연(1회성 인덱싱
비용)과 성격이 다르다 — reranking 은 **매 사용자 질의마다** 발생하는 실시간
비용이라, CPU-only 환경에서 쿼리당 11초는 대화형 서비스로는 사실상 사용
불가능한 수준이다.

## 실패 사례
21건(no_reranker 11 + bge_reranker 10) 대부분 Stage 1~4 와 동일한 유형
A(periodic 과다 매칭) 재현. reranker 는 이미 검색된 후보의 "순서"만 바꾸므로,
애초에 Hybrid Top-50 안에 정답이 아예 없는 경우(예: query 15, 17 — 이전
Stage 들에서도 반복 실패)는 reranker 로도 구제 불가 — **retrieval recall
자체의 한계는 reranking 으로 해결 안 됨**, 이는 당연하지만 중요한 확인.

## 결론 / 권고
정확도만 보면 bge-reranker-v2-m3 채택이 맞지만, 지연 차이가 "미미하지 않은"
수준을 넘어 **운영상 치명적**(사용자 대화형 응답에 쿼리당 +11초)이므로,
"성능 차이가 미미하면 자원 우선"의 반대 극단(성능 차이는 있지만 자원 비용이
압도적) 케이스로 판단한다. **CPU-only 배포 baseline 은 No-Reranker(Hybrid
only)로 유지**하고, GPU 확보 시 bge-reranker-v2-m3 재도입을 권장한다
(GPU 에서는 통상 20~50배 빨라져 쿼리당 200~500ms 대로 떨어질 것으로 예상 —
실측은 아니며 향후 검증 필요).
