# 정성 평가 — 실제 HCX Agent 50문항 (3개사 corpus)

기존에 진행한 사전 정성평가(정식 실험 Stage 체계 이전, BM25-only Agent, 3개사
113문서 corpus). 정식 Stage 1~14 실험과는 별도 트랙이며, 최종 결과 폴더에
참고자료로 포함한다.

## 결과 분포 (n=50)

| grade | count | 의미 |
|---|---|---|
| PASS | 24 | 근거 기반으로 답변, Validator 통과 |
| FAIL_UNGROUNDED | 20 | Validator 가 근거 없는 숫자/인용 문제를 감지 |
| HONEST_NO_EVIDENCE | 3 | 근거 부족을 정직하게 인정(할루시네이션 없음) |
| ERROR | 3 | API 호출 실패(HCX rate-limit 성 오류, 재시도로 대부분 해결되나 3건은 재시도 한도 내 미해결) |

## 해석
FAIL_UNGROUNDED 비율(40%)이 높은 것은 **BM25 단독 검색의 recall 한계** 때문으로
보인다 — Stage 1/2 정식 실험(§results/chunking, §results/bm25)에서도 동일한
근본 원인(corpus 내 periodic 문서 압도, 정정 체인 미해결)이 재현됨을 확인했다.
Dense/Hybrid/Reranker 를 연결한 뒤(Stage 11 E2E) 재평가가 필요하다.

## 파일
- `results.csv`: 50문항 전체 (질문/답변/등급/tool 호출 수/근거 수/validator 경고)
- `metrics.json`: 등급 분포 집계
