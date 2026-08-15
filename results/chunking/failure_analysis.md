# Stage 1 — Chunking: Failure Analysis (pooled across 3 candidates, 27 instances ≥ 20 요건 충족)

30개 validation query × 3 chunking variant 조합 중 recall@10<1.0(부분/완전 실패)인
(query, variant) 쌍은 27건 (distinct query 14개). 유형별로 분류:

## 유형 A — Periodic 문서가 다른 유형을 압도 (12건, 가장 흔함)
질의: #15("지분 변동 추이"), #17("자기주식취득 규모 비교"), #19, #25, #27, #30, #31, #33 등.
공통 원인: 이 실험 코퍼스는 periodic 2건이 leaf chunk 의 절대다수(1411개 중
~1000개 이상)를 차지한다. "지분", "변동", "취득" 같은 일반 어휘가 periodic
문서(사업보고서 VII.주주에 관한 사항 등)에도 등장해 BM25 가 periodic chunk 를
과다하게 상위 랭크시키고, 정답인 holding/major 문서가 top-10 밖으로 밀린다.
예: #15 는 gold 10건이 전부 holding 인데 top-10 전부 periodic 만 뽑힘(recall=0,
모든 변형 동일).
→ **corpus 불균형 문제**이지 chunking 만으로는 못 고친다. Stage 4(Fusion)/
Metadata Filter 로 report_type 을 좁히면 해결될 가능성이 높음 — Stage 4/11 에서
재확인 필요.

## 유형 B — 정정 체인 중 한쪽만 발견 (correction_analysis, 6건)
질의: #9, #10, #11, #22. gold 가 [원본, 정정본] 두 건인데 BM25 가 텍스트 유사도가
높은 한쪽만 상위에 올림(정정본은 본문이 거의 동일 + "[기재정정]" 몇 글자만 다름
이라 종종 원본과 정정본이 서로를 밀어냄). Recall 0.5(2건 중 1건만) 케이스가 다수.
→ Correction Resolver(정정 그래프)가 이미 이 문제를 구조적으로 푸는 컴포넌트다
(§29) — 이 순수 BM25 실험은 Retrieval Plan/Correction Resolver 를 안 쓴 상태라
당연히 겪는 한계. Stage 11(E2E)에서 Agent+Correction Resolver 를 붙이면
개선되는지 확인 필요.

## 유형 C — chunking 전략별 차이가 뚜렷한 케이스 (9건)
- fixed_500 은 ownership_analysis(#25,#27,#30)에서 유독 0.0 — 고정 500토큰 절단이
  "보유목적", "특별관계자" 같은 핵심 키워드를 앞뒤 문맥과 분리시켜 매칭 약화.
- section_aware(전체 section=1 chunk)는 #19,#31,#33 에서 recall 은 있지만 매우
  낮음(0.1~0.2대) — 큰 chunk 안에 정답 정보가 묻혀 BM25 스코어가 희석됨.
- section_aware_parent_child 는 #29 에서 recall=0.9 로 셋 중 가장 좋음 — 작은
  child chunk 단위가 특정 문구 매칭에 유리함을 보여줌.

## 결론
Section-aware+Parent-Child 가 세 후보 중 4개 지표 전부 최고(R@5=0.706, R@10=0.802,
MRR=0.682, NDCG@10=0.667)이며, 실패 유형 C 분석과도 일관된다(작은 child 단위가
정밀 매칭에 유리). 유형 A/B 실패는 chunking 이 아니라 corpus 불균형/correction
resolution 미적용에서 오므로 이후 Stage 에서 별도로 대응한다.
