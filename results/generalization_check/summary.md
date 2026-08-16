# 회사 일반화 검증 (Generalization Check)

Stage 1~14 rigorous ablation은 전부 삼성전자 1개사로만 진행됐다(계산 비용
문제로 명시적으로 스코프 축소함). 이 폴더는 그 스코프 밖 — **다른 회사에서도
잘 동작하는지**를 실제 production 파이프라인(`ask()`, 현재 확정 baseline:
HCX-007 agent + HCX-005 answer + hybrid_fusion, no reranker)으로 직접
확인한 기록이다.

## 방법론
- Stage 1~14처럼 통제된 후보 비교가 아니라 **실사용 시나리오 스모크테스트**다.
- 대회 참고 질의(3.평가 및 제출방법)가 제시한 프레임을 그대로 좌표축으로 쓴다:
  `{검색추출, 비교연산, 복합추론} × {Closed, Open}` = 6개 카테고리.
- 회사 × 카테고리 매트릭스(`matrix.csv`)로 커버리지를 추적한다 — 어디를
  테스트했고 안 했는지 항상 파악 가능해야 한다.
- 버그를 찾으면: (1) 원문/manifest 대조로 정말 틀렸는지 확인 → (2) 근본원인
  코드에서 수정 → (3) 같은 질의로 재검증 → (4) 가능하면 API 호출 없는 순수
  유닛 회귀 테스트로 `tests/`에 박제.

## 현재 커버리지 (2026-08-16 기준)

| | 검색추출_Closed | 검색추출_Open | 비교연산_Closed | 비교연산_Open | 복합추론_Closed | 복합추론_Open |
|---|---|---|---|---|---|---|
| 삼성전자(IT) | PASS | PASS | – | – | – | FAIL |
| 삼성SDI(2차전지) | PASS | – | – | PASS | PASS | – |
| LG에너지솔루션(2차전지) | PASS | PASS | – | – | – | – |
| 한미반도체(반도체) | – | – | PASS(수정후) | – | PASS | PARTIAL |
| 삼성SDI+LG에너지솔루션(교차) | – | – | PASS | – | – | – |
| KB금융(금융보험) | PASS | – | – | – | – | – |
| 알테오젠(바이오제약) | – | FAIL(실제오류) | – | – | – | – |
| HD현대중공업(조선) | PASS | – | – | – | – | – |
| 현대자동차(자동차) | – | – | – | – | – | PASS |
| 현대건설(건설) | – | – | – | – | PASS | – |
| SK텔레콤+KB금융(교차업종) | – | – | PASS(비효율) | – | – | – |

**전체 70개사 GPU 임베딩 완료 및 회수됨**(467,043 leaf chunks, `gpu_embeddings/`
— shard 24개, 원본 corpus 그대로 재임베딩 없이 필요한 회사만 필터링해서
재사용 가능). 10개사, 8개 업종(IT/2차전지/반도체/금융보험/바이오제약/조선/
자동차/건설/통신) 커버.

## 발견 → 수정 → 검증 완료된 버그 3건

1. **목차(TOC) chunk 오염** (`src/disclosure_rag/chunking/chunkers.py`)
   — 별도 커밋에서 이미 수정/문서화됨.
2. **카운팅 질문 오분류 + 무의미한 반복 tool 호출**
   (`src/disclosure_rag/agent/agent_loop.py`)
   - 증상: "몇 건이야?" 질문이 route=calculation 으로 분류되면 agent 가
     `calculate_cagr`을 완전히 동일한 인자(n_years=0 등 이미 실패가 확정된
     입력)로 4번 연속 호출하다 "확인할 수 없습니다"로 포기.
   - 수정: (a) AGENT_SYSTEM_PROMPT 에 "개수를 세는 질문은 검색 결과 개수로
     직접 답하세요" 한 문구 추가(186자, 300자 임계값 안전 마진 확보), (b)
     이름+인자가 완전히 동일한 tool 호출은 재실행하지 않고 캐시된 결과 +
     안내 메시지를 돌려주는 dedup guard 추가(모든 route 에 공통 적용).
   - 검증: 동일 질의 재실행 결과 5건(정답) 정확히 답함, 반복 호출 없음.
   - 회귀 테스트: `tests/test_agent.py::test_agent_loop_skips_redundant_identical_tool_calls`
3. **Validator 오탐 2건** (`src/disclosure_rag/agent/validator.py`)
   - (a) `get_correction_history`만 호출돼 `evidence_pack.citations`가
     비어있으면, 답변이 정확히 근거를 인용해도 무조건 `has_citation=False`
     — `tool_results_summary`에서 report_id 패턴을 스캔해 반영하도록 수정.
   - (b) "7조 6,615억원"처럼 같은 숫자를 조/억 단위로 재표기한 괄호
     `(약 ...)` 안 숫자가 글자 그대로 안 겹친다는 이유로 "근거 없는 숫자"로
     오탐 — 괄호를 grounding 검사에서 제외하도록 수정.
   - 검증: 두 케이스 모두 재실행 후 정상(citation=True, ungrounded=set()).
   - 회귀 테스트: `test_validator_has_citation_from_correction_history_tool_result`,
     `test_validator_ignores_approx_paren_restatement`

## 미해결로 남은 것 (별도 이슈, 이번엔 안 고침)

- **복합추론_Open(다년도/장문 비교) retrieval breadth 부족**: 삼성전자
  "2023 vs 2025 사업보고서 비교"(TOC버그 수정 후에도 실패), 한미반도체
  "해지 이력 시간순 정리"(6/7건만 나열, 조용한 누락) — 둘 다 top_k=5로는
  넓은 주제 질의의 본문을 충분히 못 찾는 동일 계열 문제. 다음 라운드
  우선순위.
- 삼성SDI 매출액 수치가 top_k 값에 따라 다른 chunk(실적치 vs 예상치로
  추정)를 집어오는 변동성 관찰됨 — 버그로 확정하진 않았으나 추가 조사 대상.

## 새로 발견한 문제 2건 (업종 확장 라운드, 미수정)

4. **다중행 표 요약 시 숫자 오독(답변 모델 자체 오류, validator 문제 아님)**:
   알테오젠 "최근 라이선스 계약 정리" 질의에서, 원문 표에는
   "ALT-L9 | ... | ₩8,000,000,000"(80억원)로 명시돼 있는데 답변이
   "800억원"으로 **10배 부풀려 답함**. 원문 대조로 실제 오류임을 확인 —
   이번엔 validator(`numbers_grounded=False`)가 **정확하게 잡아낸 사례**
   (Stage 12/오늘 앞서 고친 "괄호 재표기" 오탐과는 다른 케이스: 괄호 없이
   본문에 직접 오답이 들어감). 여러 계약이 한 표에 몰려있을 때 답변 모델이
   행을 헷갈리거나 자릿수를 잘못 읽는 것으로 추정. **미수정** — 다음
   우선순위 후보.
5. **업종별 회계 용어 차이에 agent가 적응 못함**: "매출액"으로 검색했는데
   금융지주(KB금융)는 원문에 "매출액"이 아니라 "외부고객으로부터의
   영업손익/영업수익"이라는 용어를 쓴다는 걸 확인. Agent 가 검색어를
   바꿔가며 재시도하지 않고 동일 쿼리를 4번 반복하다 `max_iterations`(6)
   까지 도달(결과 자체는 최종적으로 맞았지만 비효율적). **미수정**.

## 파일
- `matrix.csv`: company × category 전체 기록(질의/판정/근거)
- `raw_sector_expansion_queries.json`: 업종 확장 라운드(6개사, 8개 업종)
  원본 실행 결과.
- `gpu_embeddings/`(git 미포함, 로컬 전용, 2.8GB): 전체 70개사 임베딩.
  특정 회사만 필요하면 shard를 한 번씩 훑으며 `report_id` 로 필터링해서
  재사용(재임베딩 불필요) — `scripts/`에 예시 패턴 없음, 세션 스크립트
  참고(scratchpad, 세션 종료 시 사라짐 — 재현 코드는 PROJECT_STATE.md 참고).
