# Stage 4 — Fusion: Failure Analysis (pooled 28 instances)

## 핵심 관찰
Normalized Weighted Fusion(alpha=0.5, min-max 정규화 후 가중합)이 4개 지표
전부 최고(R@10=0.903, R@20=0.940, MRR=0.713, NDCG@10=0.735)로, RRF(순위 기반,
raw score 무시)보다도 뚜렷하게 앞선다. BM25/Dense 단독 대비 Fusion(RRF, 가중
합 둘 다)이 항상 더 나음 — 상호보완 효과가 실측으로 확인됨.

## BM25 단독 vs Dense 단독
- BM25 는 R@20(0.917)에서 Dense(0.850)보다 강함 — 넓게 훑을 때 정확한 키워드
  매칭이 유리(고유명사/숫자 등).
- Dense 는 R@10(0.840)에서 BM25(0.802)보다 강함 — 상위 랭킹 정밀도는 의미
  매칭이 유리.
- 즉 둘의 강점이 다른 지점에 있어 **Fusion 이 두 강점을 합치는 효과**가
  뚜렷하다(RRF/가중합 둘 다 R@10, R@20 에서 단독보다 항상 우위).

## Normalized Weighted Fusion 이 RRF 보다 나은 이유(가설)
RRF 는 순위만 보고 원 점수 크기를 버리는데, 이번 corpus 는 BM25/Dense 점수
분포가 비교적 안정적(같은 corpus, 같은 gold 기준)이라 원 점수 정보를 살리는
정규화 가중합이 추가 정보를 더 활용한 것으로 보인다 — corpus 가 훨씬 크고
점수 분포가 불안정해지면(예: 전체 코퍼스 규모) 이 우위가 유지되는지는
Stage 11(E2E, 더 큰 candidate pool)에서 재확인 필요.

## 실패 유형
28건 모두 Stage 1~3 에서 반복된 유형 A(periodic 문서 과다 매칭 — 특히
holding/major gold 인 multi_compare/ownership 질의)와 겹친다. Fusion 자체가
새로운 실패 유형을 만들지는 않았고, 기존 실패를 얼마나 줄이느냐의 차이였다.

## 결론 / 권고
baseline fusion = **Normalized Weighted Fusion(alpha=0.5)**. RRF 대비 구현
복잡도가 크게 늘지 않으면서(정규화 로직만 추가) 정확도가 뚜렷이 높으므로,
"성능 차이가 미미하지 않은" 경우 정확도를 우선한다는 원칙에 부합.
