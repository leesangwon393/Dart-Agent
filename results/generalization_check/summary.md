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

## ✅ 발견 및 수정 완료: 파싱 단계에서 문서 대부분이 유실되는 심각한 버그

2라운드 매트릭스 확장 중 KB금융/현대자동차의 검색 실패를 원문 대조하다가
발견. **`corp/xml_parser.py`가 malformed XML(주로 `S&P`처럼 escape 안 된
`&`)을 만나면 lxml recover 모드가 문서 구조를 완전히 오정렬시켜서, 최상위
SECTION 대부분이 TABLE/TBODY/TR 안에 잘못 중첩되고 우리 파서가 이를
"표 안의 내용"으로 취급해 통째로 유실시킨다.**

- 현대자동차 최신 사업보고서: 원문 154개 TITLE 중 **딱 2개만** 최상위로
  파싱됨(대표이사확인 + I.회사의개요). "II.사업의내용"(매출 및 수주상황
  포함) ~ "XII.상세표"까지 전부 유실.
- KB금융 최신 사업보고서: 182개 TITLE 중 **3개만** 파싱됨(+II.사업의내용
  까지는 살아남았으나 그 하위 서브섹션과 III~XII 는 유실).
- **400개 periodic 문서 샘플 스캔 결과 116건(29%)이 영향받음** — 현대차/
  현대모비스/현대오토에버/POSCO홀딩스/현대제철/고려아연/KB금융/신한지주/
  하나금융지주/메리츠금융지주/삼성화재해상보험 등 반복 확인. 메리츠금융지주
  일부 건은 최상위 섹션이 **0개**(문서 전체 유실)인 극단 케이스도 있음.
- lxml 자체는 `recover=True`로 파싱하면 TITLE 155개를 전부 복구하지만
  (`root.iter('TITLE')`), 우리 `_walk()`가 BODY 직속 SECTION-1만 최상위
  섹션으로 인정하기 때문에 표 안에 재배치된 SECTION 은 최상위로 승격이
  안 됨.
- **삼성전자/삼성SDI/LG에너지솔루션/한미반도체는 이번 스캔에서 걸리지
  않음** — Stage 1~14 및 이전 스모크테스트가 우연히 이 버그를 피해간
  것으로 보이며, 그래서 지금까지 이 문제가 안 보였던 것.

**수정 완료(2026-08-16)**: `dart_xml_parser.py`에 `_escape_bare_special_chars()`
추가 — 파싱 전에 (a) 유효하지 않은 bare `&`를 `&amp;`로, (b) DART 태그
패턴(전부 대문자/숫자/하이픈)이 아닌 bare `<`를 `&lt;`로 사전 치환해서
애초에 lxml recover 모드가 덜 필요하게 만들었다.

- **400건 재스캔: 116건(29%) → 0건**(같은 진단 기준). 현대자동차 main
  report: 2 section → 7 section("III.재무에 관한 사항"까지 복원). KB금융:
  3 → 5.
- **End-to-end 재검증(실제 질의)**: KB금융 "사업 부문 구성을 설명해줘" →
  PASS(4개 부문 정확히 설명, 5건 근거 인용). 현대자동차 "2025년 매출액은
  얼마야?" → PASS(정직한 실패 — III.재무 섹션은 복원돼 근거는 인용되지만
  문서 자체에 2025년 매출액 단일수치가 없어 할루시네이션 없이 "확인 불가"
  로 정직하게 답함). `matrix.csv`에 [재검증] 행 반영.
- **전체 코퍼스 재스캔(2,732건, TITLE 태그 존재 XML 전부)**: 여전히
  **627건(23.0%)** 영향 — 전부 periodic, 금융지주·대기업 계열 집중.
  원문 직접 대조로 원인 확정: 이미 알려진 **세 번째 malformation
  패턴**(속성값 안 이스케이프 안 된 따옴표, 예: KB금융 종속기업 현황 표의
  `ENG="" KB Insurance Co., Ltd ""`)이 그대로 원인 — 오늘 고친 bare
  `&`/`<` 버그와는 별개다. 특수관계자/종속기업 현황처럼 영문 회사명을
  ENG 속성에 넣는 표에 집중돼 있어, 다음에 손댈 후보로는 이 패턴만 좁게
  타겟팅하는 정규식을 검토할 가치가 있다(`PROJECT_STATE.md` §12 참고).
- **회귀 테스트**: `tests/test_parsers.py`에 4건 추가(합성 malformed XML
  단위 테스트 2건 + 현대자동차/KB금융 실제 문서 회귀 테스트 2건).

## 현재 커버리지 (2026-08-16 기준, 2라운드 완료)

| | 검색추출_Closed | 검색추출_Open | 비교연산_Closed | 비교연산_Open | 복합추론_Closed | 복합추론_Open |
|---|---|---|---|---|---|---|
| 삼성전자(IT) | PASS | PASS | – | – | – | FAIL |
| 삼성SDI(2차전지) | PASS | – | – | PASS | PASS | – |
| LG에너지솔루션(2차전지) | PASS | PASS | – | – | – | – |
| 한미반도체(반도체) | – | – | PASS(수정후) | – | PASS | PARTIAL |
| 삼성SDI+LG에너지솔루션(교차) | – | – | PASS | – | – | – |
| KB금융(금융보험) | PASS | PASS(재검증) | – | – | PASS | – |
| 알테오젠(바이오제약) | PASS | FAIL(실제오류) | – | FAIL(도구오선택) | – | – |
| HD현대중공업(조선) | PASS | PASS | – | – | FAIL(도구오선택) | – |
| 현대자동차(자동차) | PASS(재검증,정직한실패) | – | – | PASS(검증오탐) | – | PASS |
| 현대건설(건설) | PASS | – | – | PASS | PASS | – |
| SK텔레콤(통신) | PASS | – | – | – | – | PASS |
| SK텔레콤+KB금융(교차업종) | – | – | PASS(비효율) | – | – | – |

**36개 셀 채움 / 72개 중 50%.** 전체 70개사 GPU 임베딩 완료 및 회수됨
(467,043 leaf chunks, `gpu_embeddings/` — shard 24개, 원본 corpus 그대로
재임베딩 없이 필요한 회사만 필터링해서 재사용 가능). 10개사, 8개 업종
(IT/2차전지/반도체/금융보험/바이오제약/조선/자동차/건설/통신) 커버.

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
4. **파싱 단계 대량 문서 유실** (`src/disclosure_rag/parsing/dart_xml_parser.py`)
   — 위 "발견 및 수정 완료" 섹션에 전체 경위 기록. 요약: bare `&`/`<`
   사전 이스케이프로 400건 재스캔 기준 116건(29%)→0건. 실제 질의(KB금융/
   현대자동차) 재검증 PASS. 전체 코퍼스(2,732건) 재스캔 결과 별개의
   3번째 malformation 패턴으로 인해 627건(23.0%)이 여전히 부분 영향
   받지만, 이는 새 버그가 아니라 기존에 알려진 채 의도적으로 미수정 상태로
   남긴 한계의 정밀한 정량화다.
   - 회귀 테스트: `tests/test_parsers.py`의
     `test_escape_bare_special_chars_preserves_valid_xml_syntax`,
     `test_parse_dart_xml_recovers_sections_despite_malformed_ampersand_and_bracket`,
     `test_hyundai_motor_periodic_report_recovers_financial_section`,
     `test_kb_financial_periodic_report_recovers_financial_section`

## 미해결로 남은 것 (별도 이슈, 이번엔 안 고침)

- **복합추론_Open(다년도/장문 비교) retrieval breadth 부족**: 삼성전자
  "2023 vs 2025 사업보고서 비교"(TOC버그 수정 후에도 실패), 한미반도체
  "해지 이력 시간순 정리"(6/7건만 나열, 조용한 누락) — 둘 다 top_k=5로는
  넓은 주제 질의의 본문을 충분히 못 찾는 동일 계열 문제. 다음 라운드
  우선순위.
- 삼성SDI 매출액 수치가 top_k 값에 따라 다른 chunk(실적치 vs 예상치로
  추정)를 집어오는 변동성 관찰됨 — 버그로 확정하진 않았으나 추가 조사 대상.
- **3번째 XML malformation 패턴(속성값 안 이스케이프 안 된 따옴표)**:
  `dart_xml_parser.py` 참고. periodic 문서의 23.0%(627/2,732건, 전체 재스캔
  기준)가 이 패턴으로 여전히 부분 영향받음, 금융지주 계열의 특수관계자/
  종속기업 현황 표에 집중. 속성값 경계 판별이 위험해 정규식으로 안전하게
  고치기 어렵다고 판단해 미수정 — 좁게 타겟팅하면 가능할 수도 있어 재검토
  후보(`PROJECT_STATE.md` §12).

## 새로 발견한 문제(업종 확장 라운드, 미수정 — 파싱 버그는 위에서 별도 정리)

4. **다중행 표 요약 시 숫자 오독(답변 모델 자체 오류, validator 문제 아님)**:
   알테오젠 "최근 라이선스 계약 정리" 질의에서, 원문 표에는
   "ALT-L9 | ... | ₩8,000,000,000"(80억원)로 명시돼 있는데 답변이
   "800억원"으로 **10배 부풀려 답함**. 원문 대조로 실제 오류임을 확인 —
   이번엔 validator(`numbers_grounded=False`)가 **정확하게 잡아낸 사례**
   (Stage 12/오늘 앞서 고친 "괄호 재표기" 오탐과는 다른 케이스: 괄호 없이
   본문에 직접 오답이 들어감). 여러 계약이 한 표에 몰려있을 때 답변 모델이
   행을 헷갈리거나 자릿수를 잘못 읽는 것으로 추정. **미수정**.
5. **업종별 회계 용어 차이에 agent가 적응 못함**: "매출액"으로 검색했는데
   금융지주(KB금융)는 원문에 "매출액"이 아니라 "외부고객으로부터의
   영업손익/영업수익"이라는 용어를 쓴다는 걸 확인. Agent 가 검색어를
   바꿔가며 재시도하지 않고 동일 쿼리를 4번 반복하다 `max_iterations`(6)
   까지 도달(결과 자체는 최종적으로 맞았지만 비효율적). **미수정**.
6. **"정정"이라는 단어에 agent가 과도하게 반응해 엉뚱한 tool 선택**:
   "HD현대중공업이 최근 체결한 계약 중 해지되거나 **정정**된 적 있어?"
   질문에서, agent 가 `search_disclosures`(실제 계약 문서 검색) 대신
   `get_correction_history`(기재정정 이력 조회 전용)만 두 번 호출 —
   질문의 핵심은 이벤트성 계약 해지이지 기재정정이 아닌데도 "정정"이라는
   단어 하나에 반응해 도구를 잘못 골랐다. 결과적으로 관련 문서를 전혀
   못 찾고 "확인할 수 없습니다"로 정직하게 실패(할루시네이션은 아님).
   **미수정**.
7. **비교 질문에서 검색 대신 메타데이터 조회 tool 을 잘못 고름**: 알테오젠
   "당기와 전기 매출액을 비교해줘" 질문에서 agent 가 `search_disclosures`
   대신 `get_latest_report`(문서 메타데이터만 반환, 본문 없음)를 호출 —
   같은 문서에 당기/전기 매출액이 나란히 있는 게 이미 확인됐는데도
   검색을 안 해서 못 찾음. **미수정**.

## 검증 완료된 validator 정확성 사례 (참고용)

8. 현대자동차 "당기와 전기 영업이익 비교" 답변에서 나온 "368,794백만원"
   (당기−전기 차이)을 validator가 ungrounded로 표시했으나, 직접 검산
   결과 2,164,043−1,795,249=368,794로 **정확한 계산**임을 확인 — 답변
   모델이 evidence 의 두 원시 숫자로부터 스스로 뺄셈한 값이 문자 그대로
   evidence 에 없어서 생기는 익히 알려진 validator 한계(오탐)이며, 위
   4번(알테오젠 10배 오류, 진짜 오류)과는 성격이 다름 — 둘을 구분해서
   봐야 함을 재확인.

## 파일
- `matrix.csv`: company × category 전체 기록(질의/판정/근거)
- `raw_sector_expansion_queries.json`: 업종 확장 라운드(6개사, 8개 업종)
  원본 실행 결과.
- `gpu_embeddings/`(git 미포함, 로컬 전용, 2.8GB): 전체 70개사 임베딩.
  특정 회사만 필요하면 shard를 한 번씩 훑으며 `report_id` 로 필터링해서
  재사용(재임베딩 불필요) — `scripts/`에 예시 패턴 없음, 세션 스크립트
  참고(scratchpad, 세션 종료 시 사라짐 — 재현 코드는 PROJECT_STATE.md 참고).
