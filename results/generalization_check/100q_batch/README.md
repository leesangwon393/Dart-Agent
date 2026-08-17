# 100문항 일반화 테스트 — 진행 상황 (2026-08-17, 컴퓨터 재시작 전 저장)

사용자 요청: "질문 100개 생성해서 잘 파악하는지 확인해봐" — 기존 matrix.csv의
36개 샘플 스모크테스트를 100문항 규모로 확장.

## 지금까지 완료된 것

1. **38개사 대상 선정**: 기존 검증된 10개사(삼성전자/삼성SDI/LG에너지솔루션/
   한미반도체/KB금융/알테오젠/HD현대중공업/현대자동차/현대건설/SK텔레콤) +
   업종 다양화한 신규 28개사(반도체/IT플랫폼/금융/바이오/엔터/게임/로봇/
   방산/유통/화장품/철강/비철금속/해운/물류/건설/통신/증권/2차전지소재/
   자동차부품/전자부품 등).

2. **전체 70개사 GPU 임베딩(`gpu_embeddings/`, 24 shard, 467,043 chunks)에서
   위 38개사만 스트리밍 필터링 완료** → `/tmp/hundred_q_vectors.pkl`
   (237,212 chunks, 벡터는 numpy float32로 변환해 메모리 절약).
   - 스크립트: `build_100q_embedding_cache.py` (재실행 시 **22초**밖에 안
     걸림 — `gpu_embeddings/`가 원본이라 매번 새로 만들어도 무방, 굳이
     `/tmp/hundred_q_vectors.pkl`을 영구 보관할 필요 없음).
   - 회사별 chunk 수: `cache_build.log` 참고(KB금융 22,928 ~ 하이브 2,332).

3. **질문 100개 생성 완료** → `questions.json` (label/category/query/companies
   필드). 카테고리 분포: 검색추출_Closed 20 / 검색추출_Open 15 /
   비교연산_Closed 15 / 비교연산_Open 10 / 복합추론_Closed 20 /
   복합추론_Open 20 = 100. 스크립트: `generate_100_questions.py`
   (결정론적 템플릿×회사 조합, HCX 호출 없음 — 재실행하면 항상 동일 결과).

## 아직 안 한 것 (다음 세션이 이어받을 지점)

**100문항을 실제 `ask()` 파이프라인에 돌리는 것 자체는 아직 시작 안 함.**

다음 단계:
1. `.venv/bin/python results/generalization_check/100q_batch/
   build_100q_embedding_cache.py` 재실행해서 `/tmp/hundred_q_vectors.pkl`
   재생성(22초).
2. `questions.json`을 읽어서 각 질문을 `ask()` (agent=HCX-007, answer=
   HCX-005, fusion retriever, 기존 `matrix_fill_round2.py`/
   `verify_parsing_fix.py` 패턴 그대로 재사용 가능)로 순차 실행. **100건이면
   HCX API 호출 특성상(질문당 15~30초+ratelimit 대비 pacing) 총 40~60분+
   소요 예상** — 반드시 백그라운드로 돌리고 중간 체크포인트(예: 10건마다
   JSON에 append)로 저장할 것. rate-limit(429) 대비 `max_retries=6` +
   질문 사이 `sleep` 유지.
3. 결과를 route/n_citations/grounded/citation/warnings/elapsed 필드로
   저장(JSON) → 자동 집계(grounded율/citation율/route별 분포) +
   **일부(15~20건) 샘플은 반드시 원문 대조로 직접 검증**(자동 grounded=True
   가 항상 정답이라는 뜻은 아님 — 알테오젠 10배 오류 사례처럼 validator를
   통과해도 실제로는 틀릴 수 있음, 반대로 정직한 실패도 grounded=True로
   나옴).
4. `results/generalization_check/matrix.csv`에 100행 추가(기존 포맷과
   호환), `100q_batch/results.json` + 요약 리포트 작성, 필요시 Artifact로
   대시보드 발행.
5. 커밋/푸시.

## 참고
- 이 배치는 기존 `results/generalization_check/matrix.csv`(36개 샘플)와는
  별도 라운드다 — 완료되면 matrix.csv에 합쳐서 커버리지를 키운다.
- 프로젝트 전체 현황은 `PROJECT_STATE.md` 참고(이 100문항 작업도 §5에
  기록해둠).
