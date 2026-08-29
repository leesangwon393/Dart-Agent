# PROJECT_STATE.md — 금융공시 Agentic RAG 시스템

> 새 Claude 세션은 이 파일만 읽고 바로 이어서 작업할 수 있어야 한다.
> 최종 갱신: 2026-08-19 (100문항 일반화 배치 분석+validator 버그 수정 완료,
> 커밋 직전 — §5-0/§9~10/§12 참고)

---

## 1. 프로젝트 최종 목표

70개사 DART(한국 전자공시) 코퍼스(`corpus/`) 기반, **금융공시 특화 Agentic RAG
시스템**을 구축한다. 사용자 자연어 질의 → entity 추출 → routing → hybrid
retrieval(BM25+Dense+Fusion+Reranker) → 정정공시 버전 해석 → tool
calling(계산 등) → evidence pack 구성 → **HyperCLOVA X(HCX)** 로 근거 기반
최종 답변 생성 → 검증(validation). 단순 "질문→벡터검색→LLM" 구조가 아니다.

**진행 단계**: (1) 핵심 파이프라인 구현(Phase 1~19, 완료) → (2) 각 component
후보를 동일 eval set 으로 비교하는 rigorous ablation(Stage 1~14, 완료,
fine-tuning 제외) → (3) **회사 일반화 검증**(현재 단계 — 삼성전자 1개사로만
검증했던 스코프를 벗어나 실제 다양한 회사/업종에서 라이브로 질의해보며 버그
발견/수정) → (4, 검토 중) Router 파인튜닝.

---

## 2. 전체 아키텍처 / 파이프라인

```
공시 Corpus → Unicode Path Resolve → 유형별 Parsing → 유형별 Chunking
  → Correction Graph → 공통 Chunk Schema → [BM25 인덱스 + Dense 인덱스]
─────────────────────────────────────────────────────────────────
User Query → Entity Extraction + Query Normalize → HCX Router(hint)
  → HCX Agent(Tool Calling loop) → search_disclosures/get_correction_history/
    get_latest_report/calculate_* → Evidence Pack 구성
  → HCX Answer Generator(evidence-only) → Validator → 최종 답변+근거
```

- **오프라인 파이프라인**: `src/disclosure_rag/pipeline.py` 가 manifest →
  parse → correction graph → chunk 전체를 오케스트레이션.
- **온라인 파이프라인**: `src/disclosure_rag/agent/ask.py` 가 진입점.

---

## 3. 확정한 설계 결정 (+이유)

| 결정 | 이유 |
|---|---|
| periodic/major/holding = parser 1개 공용 | 셋 다 동일 DART `DOCUMENT/SECTION-N` XML 스키마 (실측 확인) |
| exchange = 별도 HTML parser | `.xml` 확장자지만 실제 내용은 위장 HTML — lxml.html 이라 bare `&`/`<` 에 관대해서 아래 파싱버그 영향 없음 |
| Unicode: 세그먼트 단위 NFC 리졸버 | `raw/` 폴더명 NFD, manifest/universe NFC — 직접 비교하면 100% 실패 |
| periodic 정정 매칭 = manifest key 기반 | collision 0건, pdf+html 대체수집도 자동 처리 |
| major/exchange/holding 정정 매칭 = 본문 정규식 + transitive chain | "정정대상 공시서류의 최초제출일" 텍스트 99.9% 추출 성공. 이 3종은 직전 정정본을 가리키는 다단 체인이라 root 까지 chasing 필요 |
| 표 파싱: rowspan/colspan 그리드 확장 + RLE dedup | colspan 중복 텍스트 버그 방지 |
| 표 1개 TR 500행 cap | malformed XML 로 표가 폭주하는 걸 방어(근본 원인은 §10 파싱버그와 동일 계열이나 별도 방어선으로 유지) |
| 검색 인덱스 = leaf chunk 만 | Parent(섹션 전체)를 그대로 임베딩하면 비정상적으로 느려짐. `filter_leaf_chunks()` 필수 |
| **XML 파싱 전 bare `&`/`<` 사전 이스케이프** (2026-08-16 추가) | malformed XML(`S&P`, `<신 설>` 같은 미이스케이프 특수문자)이 lxml recover 모드를 오동작시켜 문서 대부분이 표 안에 파묻혀 유실되는 버그를 근본적으로 방지. §10 참고 |
| HCX Agent/Router system prompt = 짧게 유지(~300자 이내) | **3회 독립 재현된 버그**: system prompt 가 길면 tool-calling 이 결정적으로 400 에러. `agent_loop.py`/router 코드 상단 주석 참고 |
| HCX-007 은 `thinking`/`maxCompletionTokens` 파라미터 필요 | `hcx_client.py` 가 모델명으로 자동 분기(호출부 무수정) |
| **숫자 계산은 LLM 암산에 맡기지 않고 deterministic Python + 사후 검산** (2026-08-18 강화) | `calculation.py`(tool)로 계산을 유도하되, 답변 모델이 그래도 tool 없이 암산하는 경우가 실측으로 있어(§5-B) validator가 evidence 숫자의 사칙연산 조합으로 사후 검산 + `ask()`가 검산 실패 시 최대 1회 재생성 |
| 최종 확정 baseline | §8 참고 |

---

## 4. 완료된 작업

### 4-A. 핵심 시스템 (Phase 1~19)
파싱(Unicode resolve 100%) → 청킹(Parent-Child/flat) → 정정 그래프
(transitive chain) → BM25S+Kiwi/BGE-M3+Qdrant/Fusion/Reranker → Entity
Extraction → Router → HCX Agent(Tool Calling) + Evidence Pack + Answer
Generator + Validator. 실제 HyperCLOVA X API 로 end-to-end 검증됨(예:
정정 전후 계약상대 "글로벌 대형기업→테슬라" 를 3턴 tool-calling 으로 정확히
답변, 원문 XML 대조 100% 일치).

### 4-B. Rigorous Ablation (Stage 1~14, 전부 완료)
Stage 1(Chunking)/2(BM25)/3(Embedding)/4(Fusion)/5(Reranker)/8(Entity)/
9(Router)/10(Agent 모델)/11(E2E)/12(Answer 모델)/14(Final E2E, test set)
— 6/7/13은 요청 범위 밖. 각 `results/{stage}/`에 config/metrics/results.csv/
failure_cases/summary/failure_analysis 전부 저장. 최종 요약:
`results/FINAL_SUMMARY.md` + Artifact 대시보드. 상세 수치는 §8.

### 4-C. 회사 일반화 검증 (완료, Stage 체계와 별개 트랙)
`results/generalization_check/` — 대회가 제시한 "검색추출/비교연산/복합추론
× Closed/Open" 6개 카테고리를 좌표축으로 회사×카테고리 매트릭스(`matrix.csv`)
를 채워가며 실제 production 파이프라인으로 라이브 검증. 초기 12개사 수동
매트릭스(36/72칸)에 이어 **100문항 자동 배치**(38개사×6카테고리×6route,
§5-0)까지 완료돼 `matrix.csv`는 총 138행(헤더 제외 137). 발견/수정한 버그는
§10 참고 — **이 트랙에서 Stage 1~14의 정량 지표로는 못 잡았던 진짜 버그
여러 건을 발견**(TOC chunk 오염, 카운팅 오분류, validator 오탐(연도 숫자
포함 3건), **파싱 단계 대량 문서유실**, **"확인할 수 없음"이 실제로는
검색 실패인 경우 11건**).

### 4-D. 부가 산출물
- `results/qualitative_50q/`: Stage 체계 이전 BM25-only Agent 50문항 정성평가
- `results/router_tuning/`: Router 파인튜닝 검토 — CLOVA Studio 요구사항 조사,
  HCX 생성 파일럿 데이터 149건(review 완료, `rubric.md`에 경계규칙 문서화)
- **전체 70개사 GPU 임베딩**: `gpu_embeddings/`(로컬 전용, git 제외, 2.8GB,
  467,043 leaf chunk) — §7 참고
- GitHub: https://github.com/leesangwon393/Dart-Agent.git (origin/main,
  단계 완료마다 커밋+push)

---

## 5. 현재 진행 중인 작업

### 5-A. [완료] Router v2 — Cascading Router + unclear escape hatch
사용자가 Stage 9 를 직접 감사하다 지적: "HCX 라우터가 6개 route 중 하나를
무조건 고르게 세팅돼 있어서 애매한 질문도 강제로 틀린 route 배정됨."
확인 결과 사실이었다(`RouteResult.route=None` fallback 설계는 있었지만
HCX 쪽 구현이 `tool_choice` 강제+6択 enum이라 구조적으로 발동 불가,
Stage 9의 `fallback_rate=0.0`이 증거). 추가로 `AMBIGUOUS_SET`(§47, 애매한
질문 4건 평가용)이 만들어져 있었는데 `evaluate_router()`가 아예 참조를
안 해서 죽은 코드였던 것도 발견.

**수정**: `src/disclosure_rag/router/hcx_router.py`(신규) — `route` enum에
`"unclear"` 추가한 `HCXStructuredRouter`, semantic_router를 절대
threshold가 아니라 **top1-top2 margin**으로 게이팅해 애매하면 HCX로
escalate하는 `CascadingRouter`(사용자가 직접 제안한 구조 그대로 구현).
`router/eval.py`에 `evaluate_router_ambiguous()` 추가. 테스트 12건 추가
(전부 stub, API 불필요).

**실측 1차(EVAL_SET 55건, routes.py 수정 전)**: 절대 유사도 점수는
정답/오답 구분력이 거의 없었다(정답 median=0.781 vs 오답 median=0.804 —
오답이 더 높음!). margin은 뚜렷한 구분력이 있었다(margin>=0.05 부분집합
accuracy=1.000). CascadingRouter는 pure HCX 대비 accuracy +0.111(0.796
vs 0.685), latency -41%(1.34s vs 2.26s)로 명확히 우위였지만, **예상과
다르게** pure semantic 단독(0.818)보다는 근소하게 낮았다 — escalate된
hard 케이스에서 HCX가 semantic보다 오히려 살짝 더 틀렸다(0.645 vs
0.677). 원인 분석 결과 라우팅 메커니즘이 아니라 calculation/
event_analysis ↔ single_lookup 사이 **route 정의 자체의 taxonomy
중첩**(17개 HCX 오류 중 12개가 이 패턴 하나에 집중) 문제로 판명.

**즉시 후속 조치(같은 날, routes.py 경계 재정리 완료)**: 사용자 요청으로
`routes.py`에 실패 패턴을 직접 겨냥한 utterance 19개 추가(calculation 6개
— "계산해줘" 동사 없이 증가율/성장률/증감폭을 묻는 표현, event_analysis
6개 — "~한 적 있어?" 류 이벤트-존재 질문, 나머지 4개 route에 소량씩).
HCX는 routes.py를 안 보므로(system prompt 300자 제한상 few-shot 불가)
이 수정은 semantic_router/CascadingRouter의 fast-path에만 영향 — **재측정
결과 semantic 단독 0.818→0.836, CascadingRouter 0.796→0.889로 개선**,
이제 pure semantic·pure HCX 둘 다 명확히 앞선다(HCX는 그대로 0.685,
재호출 불필요 — routes.py 변경이 HCX 결과에 영향 줄 이유가 없음). fast-path
비율도 42%→56%로 늘고 mean latency도 1.34s→1.01s로 줄었다 — 상세:
`results/router_v2/summary.md`. Stage 9 원본 "n=30" 결과(hcx=0.800,
semantic=0.600)는 이번 n=54~55 재측정과 방향이 반대로 나왔는데, git
히스토리가 스쿼시돼 있어 원래 n=30이 어떻게 뽑힌 서브셋인지 재구성
불가 — 이번 결과가 더 신뢰할 만한 수치로 봐야 한다(그래도 n이 작은 건
여전한 한계). **남은 후보**: HCX 자체의 오분류(routes.py로는 못 고침)를
줄이려면 `classify_route` tool schema에 route별 짧은 description 추가
검토(§12).

### 5-B. [완료] 숫자 계산: LLM 암산 방지 + 사후 검산 + 자동 재생성
사용자 질문: "숫자 계산하는건 llm 안시키고 직접 계산하는게 맞는거 같은데
어떤식으로 처리해야하지". `calculation.py`(`calculate_growth_rate/ratio/
cagr`)는 원래부터 "계산은 LLM이 아니라 deterministic Python으로" 원칙으로
만들어져 있었지만, **agent가 tool을 안 쓰고 두 숫자를 그냥 검색해온 뒤
답변 모델(HCX-005, tool 없는 자유 텍스트 생성)이 스스로 뺄셈/비율을
암산하는 경우**가 실측으로 있었다(matrix.csv 현대자동차 사례: 2,164,043 -
1,795,249 = 368,794 를 암산 — 이번엔 맞았지만 검산 없이는 알테오젠 10배
오류와 똑같이 "근거 없는 숫자"로만 보였다).

**3중 방어로 수정**:
1. **예방**: `agent_loop.py`의 `AGENT_SYSTEM_PROMPT`에 "차이도 calculate_*로,
   암산 금지" 문구 추가(186→199자, 300자 안전마진 안에서). `answer_
   generator.py`의 `ANSWER_SYSTEM_PROMPT`에 "계산 결과가 tool에 없으면
   암산하지 말고 원본 숫자만 제시하거나 '계산 결과 없음'이라 답하라" 규칙
   추가(tool-calling이 아니라 300자 제한과 무관).
2. **검산**(`validator.py`, 핵심): `_verify_derived_number()` 추가 — 답변에
   있지만 evidence에 문자 그대로 없는 숫자를, evidence 숫자들의 사칙연산
   (차이/합/비율)으로 설명 가능한지 직접 재계산해서 확인한다. 설명되면
   `verified_derived_numbers`로 인정(grounded 처리), 설명 안 되면(=진짜
   지어낸/틀린 숫자, 알테오젠 10배 오류 같은 케이스) 여전히
   `ungrounded_numbers`로 남는다 — "암산이지만 맞음"과 "틀림"을 최초로
   구분할 수 있게 됨.
3. **자동 교정**(`ask.py`): `validation.numbers_grounded=False`면 그냥
   경고 로그만 남기지 않고, 교정 지시를 덧붙여 `generate_answer()`를
   최대 1회(`max_answer_retries`) 재호출 — 검산 실패한 답변이 그대로
   사용자에게 나가지 않도록 최종 출력 단계에서 막는다.

회귀 테스트 5건 추가(`tests/test_agent.py`, 전부 stub 기반 — 올바른
뺄셈/비율 검산 통과, 틀린 계산은 여전히 flag, ask() 재시도가 실제로
답변을 교체하는지, AGENT_SYSTEM_PROMPT 길이 가드). 실제 다중턴 HCX
통합테스트(`test_agent_correction_analysis_uses_both_versions_and_two_
plus_turns`)로 프롬프트 길이 변경이 tool-calling을 안 깨는지도 재확인.

### 5-0. [완료] 100문항 일반화 테스트
사용자 요청("질문 100개 생성해서 잘 파악하는지 확인해봐")으로 시작. 38개사
(기존 10 + 신규 28, 업종 다양화) × 6개 카테고리 × 6개 route 100문항을 실제
`ask()` 파이프라인으로 백그라운드 실행 완료(`results/generalization_check/
100q_batch/results.json`, 2026-08-18 22:45:46 종료) → 결과 분석/원문대조/
validator 버그 수정/matrix.csv 반영/summary.md 작성까지 전부 완료
(2026-08-19). 상세: `results/generalization_check/100q_batch/summary.md`.

**최종 수치**: 100건 중 API 완전 실패 5건(5.0%, 전부 수십분~97분 hang 후
ConnectionError/ReadTimeout/HCXError 400 — 4건 네트워크 계열, 1건은 400
Bad Request로 성격이 다름). 성공 95건 중 grounded=True 81→**93건(97.9%,
validator 버그 수정 반영)**, citation=True 88건(92.6%, 불변).

**핵심 발견 1 — validator 오탐 수정**: `correction_analysis`/`single_lookup`
12건에서 evidence 원문의 "(2023.12)" 같은 날짜 표기가 "2023.12" 한 토큰으로
추출되는 바람에, 답변이 같은 연도를 점 없이("2023년 3월 12일") 따로 쓰면
"근거 없는 숫자"로 오탐됐다. `get_correction_history` 실측 데이터로 재현
확인 후 `validator.py`의 `_extract_numbers()`에 소수점 하위토큰 등록 로직을
추가해 수정, 회귀 테스트 1건 추가, fast suite 97개 전부 통과. §9-A/§10 참고.

**핵심 발견 2 — "확인할 수 없음"이 실제로는 검색 실패인 경우 11건 (신규,
미수정, 최우선 후속 조사 대상)**: "OO의 2025년 매출액/영업이익/부채비율"류
질문 11건이 "제공된 근거로는 확인할 수 없습니다"라고 답했는데, 원문 대조
결과 **전부 FY2025 사업보고서가 코퍼스에 이미 존재하고 원하는 숫자도 원문에
명확히 있었다**(예: SK하이닉스 "2025년 연결 기준... 영업이익은 47.2조원"이
본문에 그대로 있음, 기아/현대자동차/셀트리온/삼성SDI 등도 동일). grounded/
citation 자동 지표로는 전혀 안 걸린다(숫자를 안 쓰니 검산할 게 없고 "근거:"
문구는 있어서 citation도 통과). `search_disclosures`의 period 필터 완화
재시도 로직으로는 설명 안 되는 retrieval relevance 문제로 추정되나 근본
원인 미확정(HCX 재호출 없이는 tool-call 트레이스 재현 불가) — 다음 세션
최우선 조사 대상. 상세: summary.md §4-D.

**기타 발견(미수정)**: (a) 알테오젠 calculation 1건 — 뺄셈이 정확한데도
ungrounded로 남음, `_verify_derived_number()`의 O(n²) 안전장치
(`_MAX_VERIFY_NUMBERS=200`)가 숫자 밀집 evidence에서 검산 자체를 건너뛰는
것으로 추정. (b) 레인보우로보틱스 investment 1건 — validator 오탐이 아니라
**실제 10배 단위 오류**(281,784백만원이라 썼는데 원문은 28,178백만원) 재현,
알테오젠 10배오류와 동일 계열의 새 사례, validator가 정확히 잡아냄. (c)
citation=False 7건 — 전부 답변 자체는 맞는데 "근거:" 인용 문구를 안 붙인
형식 누락. (d) `has_citation`의 `"근거" in answer` 폴백이 매우 느슨해서
report_id 실제 일치 여부와 무관하게 통과시키는 기존 약점 재확인.

---

**파싱 버그 수정 + 재검증이 완료됐다.** 아래는 그 전체 경위와 최종 수치
(다음 세션은 여기부터 §12 "다음 후보"로 넘어가면 됨).

### 무슨 일이 있었는지 (순서대로)
1. 회사 일반화 검증 2라운드(매트릭스 빈 칸 채우기, 12개 질의)에서 KB금융/
   현대자동차가 아주 기본적인 질문("매출액 얼마야?", "사업 부문이 뭐야?")
   에도 실패 → 원문 대조 → **`dart_xml_parser.py`가 malformed XML(escape
   안 된 `&`, `<`)을 만나면 lxml recover 모드가 태그 구조를 오정렬시켜
   최상위 SECTION 대부분이 TABLE 안에 잘못 중첩되고 통째로 유실되는 버그**
   발견. 400개 periodic 문서 샘플 스캔 → **116건(29%) 영향**.
2. **수정**: `dart_xml_parser.py`에 `_escape_bare_special_chars()` 추가 —
   파싱 직전에 (a) 유효하지 않은 엔티티 참조 형태의 `&`를 `&amp;`로, (b)
   진짜 DART 태그(전부 대문자/숫자/하이픈) 패턴이 아닌 `<`를 `&lt;`로 사전
   치환. 현대자동차 문서: main report 파싱 section 2개 → 7개(매출 섹션
   포함 "II.사업의내용", "III.재무에 관한 사항"까지 살아남음). KB금융:
   3개 → 5개. Fast test suite(73개+신규 4개=77개) 전부 통과.
3. **End-to-end 재검증 완료**: KB금융/현대자동차 재청킹+재임베딩 →
   `/tmp/expand_sectors_vectors.pkl` 패치 → 원래 실패했던 두 질의 재실행.
   - KB금융 "사업 부문 구성을 설명해줘" → **PASS**: 위탁/자산관리·기업금융·
     자산운용·기타사업 4개 부문을 정확히 설명, `periodic_20260313001191`
     등 5건 근거 인용(수정 전엔 근거를 아예 못 찾았음).
   - 현대자동차 "2025년 매출액은 얼마야?" → **PASS(정직한 실패)**: III.재무
     섹션이 복원돼 근거 5건은 인용되지만, 문서에 2025년 매출액 단일수치
     자체가 없어("2024년 실적만 확인 가능") 할루시네이션 없이 정직하게
     "확인할 수 없음"으로 답함 — grounded/citation 정상.
   - `results/generalization_check/matrix.csv`에 "[재검증]" 행 2개 추가함.
4. **전체 코퍼스 재스캔 완료**(2,732건 — TITLE 태그가 있는 XML 문서 전부,
   ~160초): "원문 `<TITLE` 개수 대비 구조적으로 복원된 SectionNode 개수"
   비율이 50% 미만인 문서를 "영향받음"으로 카운트. **결과: 627건(23.0%)
   여전히 영향받음** — 전부 `doc_group=periodic`, KB금융/하나금융지주/
   HD현대일렉트릭/한화에어로스페이스 등 금융지주·대기업 계열에 집중.
   **직접 원문 대조로 원인 확정**: 위에서 말한 세 번째 malformation
   패턴(속성값 안 이스케이프 안 된 따옴표) 그대로였다 — 예:
   `ENG="" KB Insurance Co., Ltd ""` (KB금융 2026 사업보고서, 종속기업
   현황 표). 이 패턴은 특수관계자/종속기업 현황처럼 표 안에 영문 회사명을
   ENG 속성으로 넣는 표에 집중돼 있다. **즉 오늘 고친 bare `&`/`<` 버그는
   진단 기준상 완전히 해소됐고(400건 재스캔 116→0), 남은 23%는 별개의,
   이미 문서화된 채 의도적으로 미수정 상태로 남긴 3번째 패턴이 원인** —
   새로 발견된 버그가 아니라 기존에 알려진 한계의 정밀한 정량화다.
5. **`tests/test_dense_retriever.py::test_bge_m3_dense_retriever_finds_
   relevant_chunk` 도 실패하지만 회귀 아님**: `git stash`로 파싱 수정을
   빼고 동일 테스트를 재실행해도 **똑같이 실패**(관련성 있는 청크는
   찾지만 assertion이 요구하는 정확한 리터럴 문자열 "연구개발"이 없는
   변형 — 원래도 깨져 있던 brittle assertion). 파싱 수정과 무관함을
   확인했으므로 커밋을 막지 않음(별도 이슈로 §10에 기록).

### 아직 손 안 댄 이슈들 (§10 상세)
복합추론_Open retrieval breadth 부족, 표 다중행 숫자 오독(알테오젠 10배
오류), "정정" 단어 과민반응 tool 오선택, 비교질문에서 메타데이터 tool
오선택, 업종별 회계용어 미적응, **3번째 malformation 패턴(속성값 안
이스케이프 안 된 따옴표, 여전히 periodic 문서 23% 관련)**. 이 중 파싱버그와
무관해 보이는 것들(카운팅 오분류 등은 이미 별도로 수정 확인됨)은 재확인
결과 진짜 별개 이슈였던 것으로 보임 — 6번 항목 재확인 완료로 간주.

### 5-C. [완료] Entity Extraction 확장 — event/ownership/기간비교 신호 추가 (2026-08-26)
6개 route(single_lookup/correction_analysis/multi_compare/calculation/
ownership_analysis/event_analysis) 중 **event_analysis·ownership_analysis·
기간비교형 calculation 세 갈래는 회사명 말고 전용 entity 신호가 전혀
없었다**는 게 사용자와의 논의로 확인돼 갭을 채웠다. 원칙 그대로 유지:
전부 deterministic 정규식/사전 매칭, LLM 미사용(§12 원칙 9~10, Stage 8
ablation 에서 rule-only 가 승자였던 이유와 동일 근거).

**추가한 필드/사전 파일** (`src/disclosure_rag/entity/entity_extractor.py`,
`ExtractedEntities` 전부 기본값 있어 하위호환 유지):
- `period_type: str | None` — 기존 `_PERIOD_PAT` 5개 sub-pattern
  (연도/연월/분기/반기/최근N년) 중 매칭된 것을 `"annual"|"year_month"|
  "quarter"|"half"|"recent_n_year"` 로 분류. **정책**: 여러 개 동시 매칭
  시(예: "2025년 1분기" → annual+quarter 둘 다 매칭) "가장 구체적인 것
  하나"를 우선 채택(우선순위: year_month > quarter > half > annual >
  recent_n_year, "특정 월을 못박는 게 가장 좁고 구체적, 최근N년 범위가
  가장 넓다"는 기준). 원본 `period` 리스트는 그대로 유지되므로 여러 매칭
  사실 자체는 손실 안 됨 — 근거는 코드 주석에 남김.
- `period_comparison: bool` — "당기 대비 전기", "전기 대비", "전년 대비",
  "작년보다", "전년동기" 등 두 기간 비교 신호를 정규식으로 감지. 100문항
  배치 실패 사례("OO의 당기 대비 전기 영업이익 변화를 정리해줘" 5건이
  `period=[]`로 빈 채 넘어감, §9-0)를 실제로 잡는지 회귀 테스트로 확인.
- `event_terms: list[str]` (신규 `config/event_terms.txt`, 32개) — major/
  수시공시 하위 이벤트 유형 키워드. `corpus/raw/major/*/list_B001.json`,
  `corpus/raw/exchange/*/list_*.json` 의 실제 `report_nm` 집계(grep/python
  으로 직접 확인, 예: "자기주식처분결정" 158건, "유상증자결정" 59건,
  "단일판매ㆍ공급계약체결" 512건 등)와 routes.py event_analysis utterance
  를 참고해 채움. "단일판매·공급계약체결"은 원문에 실제 쓰이는 가운뎃점
  (ㆍ, U+318D)과 흔히 입력되는 일반 가운뎃점(·, U+00B7) 두 형태를 모두
  등록(안 그러면 문자 하나 차이로 매칭 누락).
- `ownership_terms: list[str]` (신규 `config/ownership_terms.txt`, 16개) —
  기존 `metric_terms.txt`에 섞여 있던 "지분율"/"최대주주 지분율"/
  "보유비율"을 이 필드 전용으로 이동(metric_terms.txt 에서는 제거해 중복
  매칭 방지)하고 "최대주주", "종속기업", "특수관계자", "계열회사" 등 추가.
- `comparison_axis: str | None` — `"company"`(company_count>=2) |
  `"period"`(period_comparison 이거나 period 매칭 2개 이상) | `None`.
  **정책**: 둘 다 참인 복합 케이스("A기업과 B기업의 2023년과 2025년 매출을
  비교해줘" — 실제로 만들 수 있음을 회귀 테스트로 확인)는 `"company"`를
  우선. 근거: 회사 축을 놓치면 다른 회사 데이터가 섞이는 치명적 오류가
  나지만, 기간 축을 놓쳐도 "일단 최근 기간으로 회사별 조회"까지는 절반은
  맞는 답이 나옴 — 실패 시 피해가 더 큰 축을 우선한다는 원칙.

**통합**: `agent_loop.py`의 `_route_hint_message()`에 새 필드들 반영(기간
유형/기간비교 여부/이벤트 키워드/지분 키워드/비교 축 몇 줄 추가, 길이는
기존 필드 나열 방식 그대로 유지). `results/generalization_check/100q_batch/
assemble_pipeline.py`의 `EntityExtractor` 생성부도 새 사전 경로 전달하도록
동기화.

**의도적으로 안 건드린 것**: `query_normalizer.py`의 `normalize_query()`는
이번 범위 밖(회사명 정규화만 하는 기존 범위 유지) — `[YEAR]` placeholder가
아직 없어서 "OO의 2023년 대비 2025년" 같은 질의가 회사만 `[COMPANY]`로
치환되고 연도는 그대로 노출되는 이슈는 **별도 TODO로 남김**(§12 참고).

**테스트**: `tests/test_entity_extraction.py`에 회귀 8건 추가(period_type
annual/quarter 우선순위, period_comparison, event_terms, ownership_terms,
comparison_axis company/period/복합충돌 — 파일 전체 9→17건). 전체
`pytest tests/ -m "not slow"` 146 passed, 0 failed(기존 테스트 전부 그대로
통과, 회귀 없음) — 커밋 831a388.

**후속 발견/수정(파트 2 실검증 중, 커밋 a127b13)**: event_terms가 "단일판매
ㆍ공급계약체결/해지"처럼 문서 제목 명사구로만 등록돼 있어서, 실제 대회
예시 질문("~이 체결한 계약 이후 해지된 계약이 있는가?")처럼 동사가 명사
앞에 오는 자연어 어순에서는 매칭이 안 됨을 실제 파이프라인 실행으로 발견
→ "체결"/"해지" 어근을 단독으로도 추가해 어순 무관하게 매칭(테스트
9건→18건). 아래 §5-D 참고.

### 5-D. [완료] 실제 대회 예시 질문 6개 파이프라인 검증 (2026-08-26)
사용자가 대회 공식 예시 질문 6개(유형: 검색및정보추출/다중조회및비교연산/
복합문서추론 × Closed/Open)를 스크린샷으로 제시 → 코퍼스에 실존하는 회사로
치환해 **실제 3개사(삼성전자/삼성SDI/LG에너지솔루션)만 새로 청킹+임베딩**
(4,607 leaf chunk, `build_all_chunks(rows=...)`로 필요한 문서만 필터링 —
전체 코퍼스 재임베딩 아님)한 뒤 `build_cascading_router` + `ask()`로 6개
질문을 실제 실행하고 원문(corpus/raw) 대조까지 완료.

**실행 인프라 메모**: 처음엔 3개사의 전체 doc_group(165건, leaf 21,261개)을
로컬 MPS로 임베딩 시도했으나 rate 가 5.9/s→3.1/s로 계속 저하되며 메모리
압박(PhysMem 15G 중 unused 140MB, load avg 9대) 관측 — 6개 질문에 실제
필요한 문서(삼성전자 3건: 2023/2025 사업보고서+2026Q1 분기보고서, 삼성SDI
2025 사업보고서+major 5건, LG에너지솔루션 2025 사업보고서+exchange 21건,
총 31건)로만 좁혀 재청킹(leaf 4,607개)해 19분 만에 완료.

**결과 요약 (6개 중 4개 완전 정확, 1개 부분정확 그러나 핵심 오답, 1개
안전하게 실패)**:
- **Q1(단순조회/Closed, 삼성전자 2025 연결매출)**: 완전 정확.
  333,605,938백만원 — 원문과 한 글자도 안 틀리게 일치. route=single_lookup.
- **Q2(단순조회/Open, 삼성전자 2026Q1 투자계획)**: 정확. 11.2조원 시설투자,
  DS/SDC 첨단공정, Advanced 노드 CAPA 등 원문 핵심 내용 정확히 요약(세부
  부문별 금액 breakdown은 생략했지만 오류는 없음). route=single_lookup
  (semantic margin 불충분 → HCX escalate, score=None).
- **Q3(다중비교/Closed, 삼성SDI vs LG엔솔 2025 설비투자)**: 완전 정확.
  삼성SDI 3조 2,744억원 vs LG에너지솔루션 10.5조원, 정답(LG엔솔이 큼)
  정확히 도출. comparison_axis="company" 덕에 회사별로 검색을 2번 분리
  호출 — entity 힌트가 실제로 도움이 된 명확한 사례. route=multi_compare
  (score=0.71, semantic 단독 통과).
- **Q4(다중비교/Open, 삼성SDI 2025 자금조달 유형별)**: 완전 정확(원문 대조로
  "증자비율 16.8%" 포함 모든 숫자 확인됨 — 처음엔 major 공시 5건에 안 보여
  hallucination으로 의심했으나 periodic 사업보고서의 자본금변동 표에 원문
  그대로 있었음). CB/BW/EB는 없다고 정확히 답변(실제로 없음). event_terms=
  [유상증자,CB,BW,EB] 추출 → agent가 "CB BW EB"/"EB" 로 추가 검색까지
  수행 — entity 힌트가 실제로 도움이 된 두 번째 사례. route=multi_compare.
- **Q5(복합추론/Closed, LG엔솔 2025 체결계약 중 해지 여부) — 핵심 오답,
  최우선 후속 조사 대상**: "네, 존재합니다"로 답하며 Ford/Freudenberg 배터리
  공급계약 해지 건(2025-12-17/12-26)을 근거로 들었으나, **검색된 evidence
  자체에 "정정관련 공시서류제출일: 2024-10-15"처럼 원계약이 2024년(또는
  2023년) 체결이었다는 명시적 단서가 있었는데도 답변 생성 단계에서 "해지
  시점이 2025년"과 "체결 시점이 2025년"을 혼동**함. 실제 정답은 "2025년에
  체결된 계약 중 해지된 사례는 없음(해지된 2건은 모두 2024년 체결)". 원인은
  **검색 실패가 아니라 Agent/답변생성 단계의 날짜 교차검증 실패** — validator
  는 grounded=True/citation=True/passed=True로 통과시켰다(자동판정이 이런
  논리적 오류를 못 잡는다는 게 이번에도 재확인됨, §10 기존 패턴과 동일).
  entity extraction 은 문제 없었음(companies/period 정확) — event_terms가
  파트 1 시점엔 비어 있었던 것도 원인 중 하나로 의심됐으나, 재현 확인 결과
  routes.py 자체 utterance만으로 이미 event_analysis(score=0.69)로 잘 갔음
  → 힌트 부재가 이 오답의 직접 원인은 아니고, "날짜 두 개(체결일 vs 해지일)
  를 구분해서 교차검증하라"는 지시가 없는 게 근본 원인으로 추정. 후속 조사
  후보: `ANSWER_SYSTEM_PROMPT`에 "이벤트의 발생일과 그 이벤트가 참조하는
  원본 문서의 날짜가 다를 수 있으니 구분해서 확인하라"는 원칙 추가 검토.
- **Q6(복합추론/Open, 삼성전자 2023 vs 2025 핵심사업 변화) — 검색 실패**:
  "제공된 근거로는... 비교하기 어렵습니다"로 안전하게 실패(hallucination은
  안 함). 원인은 **Agent의 검색어 선택**: "사업보고서"/"핵심 사업"라는
  일반적 쿼리 + `top_k=1`로 검색해 정관상 사업목적 나열(운동경기업/교육
  서비스업 등)이나 TOC 조각만 건짐. 직접 BM25로 "사업의 개요 부문별 매출
  비중"을 같은 인덱스에 질의해보니 정답 chunk("DX 부문이 187조9,673억원
  (56.3%), DS 부문이 130조1,282억원(39%)...")가 1위로 바로 나옴 — **데이터/
  인덱스 문제가 아니라 순수 검색어 포뮬레이션 실패**. entity extraction은
  정확했음(companies/period 2개/report_name=사업보고서/comparison_axis=
  "period" 모두 정확) — Agent가 그 힌트를 못 살리고 너무 일반적인 쿼리를
  쓴 것. 실제 정답: DX 부문 매출비중 65.7%(2023)→56.3%(2025) 축소, DS
  부문 25.7%→39.0%로 확대(AI/HBM 수요 확대 영향), 종속기업 232→308개
  증가 — 4개 사업부문(DX/DS/SDC/Harman) 구성 자체는 안 바뀜.

**entity extraction 확장이 실제로 도움이 된 사례**: Q3(comparison_axis=
"company" → 회사별 분리 검색), Q4(event_terms=[유상증자,CB,BW,EB] → CB/BW/
EB 개별 재검색까지 유도). **한계 발견**: comparison_axis="period" 오탐
(Q2/Q6 — "2026년 1분기"처럼 한 기간을 연도+분기 두 조각으로 표현한 것과
"2023년과 2025년"처럼 진짜 두 기간을 비교하는 것을 `len(period)>=2`로는
구분 못 함 — Q2에선 무해했지만 Q6에서도 route에는 영향 없었음, 후속 개선
후보로 §12에 기록).

### 5-E. [완료] 전체 코퍼스(626,497 leaf chunk) 재검증 — v1 대비 비교 (2026-08-27)

팀원 Kim이 우리 semantic block chunking 가이드(`docs/handoff_to_kim_
semantic_chunking.md`)를 반영해 **전체 70개사를 재청킹+재임베딩**(dense+
sparse, BGE-M3, MPS, 7.4시간)한 결과를 전달받음(`임베딩결과_v2_20260827.
zip`, 전송 중 일부 손상 — `sparse_0005.jsonl.gz` CRC 오류 1건, 나머지 63개
샤드+l1은 정상, 재전송 요청 없이 스킵하고 진행하기로 결정). 지금까지 5~8개
사 소규모 subset으로만 검증했던 이번 주 수정사항(Kim 파싱버그 수정 병합,
semantic block chunking, entity extraction 확장, CascadingRouter 배선,
`[YEAR]` placeholder)을 **처음으로 전체 규모에서 재검증**.

**방법**: Kim의 sparse/`normalized_weighted_fusion`은 안 쓰고(변수 통제),
우리 자체 스택(BM25+Dense+RRF, reranker 없음, §8 baseline 그대로) 그대로
써서 v1(8/18)과 최대한 공정하게 비교. 우리 `ChunkSchema`로 변환하는 호환
로더 신규 작성(`results/generalization_check/100q_batch_v2/kim_v2_
loader.py`), 같은 `questions.json` 100문항 재사용. 커밋 `8da607b`.

**결과 요약** (전체는 `results/generalization_check/100q_batch_v2/`):

| 지표 | v1(subset, 보정후) | v2(전체 코퍼스) |
|---|---|---|
| API 완전 실패 | 5/100 | **3/100** (개선) |
| grounded=True | 93/95 (97.9%) | 88/97 (90.7%) (**하락**) |
| citation=True | 88/95 (92.6%) | 91/97 (93.8%) (개선) |
| 평균 응답시간 | 26.5s | 40.5s (626K 규모 검색 비용 증가) |

**좋은 소식— 실제로 고쳐진 것 확인**:
- Q5류 날짜혼동(체결일 vs 해지일) 재발 없음 — LG에너지솔루션 계약해지
  질문에서 두 날짜를 정확히 분리(`b4e63b0` 수정 100문항 규모에서도 유지).
- 레인보우로보틱스 10배 오류(예전 버그) 재발 없음, 정확한 수치 인용.
- correction_analysis 16건 전부 정상 라우팅.
- "2025년 재무지표 확인불가" 11건 중 9건이 실제 수치를 답변(부분 개선).

**나쁜 소식 — 새로 발견되거나 여전한 문제(§10에 상세 기록)**:
1. **[완료 2026-08-29]** ~~CascadingRouter의 HCX escalation에서 간헐적
   400 "Unsupported function" 에러~~ — v1엔 없던 새 실패 유형(5회 중
   3회는 6회 재시도(최대 199~215초)로도 영구 실패)이었지만, 원인은
   새 버그가 아니라 기존에 알려진 RPM rate-limit이 CascadingRouter의
   추가 HCX 호출(100건 중 60건)로 처음 실제 발현된 것 — `hcx_client.py`
   에 pacing 추가로 해결. §12 참고.
2. **[완료 2026-08-29]** ~~연결/별도 재무제표 혼동~~(SK하이닉스) — 질문에
   기준 명시가 없으면 별도(개별)재무제표 수치를 연결기준인 양 답함,
   validator가 못 잡음(grounded=True로 통과). `ANSWER_SYSTEM_PROMPT`
   원칙 추가로 수정(§5-H).
3. **[재발] 10배 단위환산 자기모순**(아모레퍼시픽) — 같은 문장 안에서
   정답과 10배 축소값을 동시 제시. 회사를 바꿔도 계속 발생 — 알테오젠/
   레인보우로보틱스와 같은 계열의 구조적 문제로 재확인.
4. **[완료 2026-08-29]** ~~report_id 재포맷 손상~~(한미반도체) — 답변모델이
   인용 시 `periodic_20260515001572`를 `periodic_20260515_01572`로
   자릿수를 지우고 언더스코어를 삽입 — 인용 자체가 깨짐. `has_citation`의
   느슨한 폴백 제거 + 손상 탐지로 수정(§5-H).
5. **[잔여] 검색 실패 2~3건** — 셀트리온/현대건설 등 원문에 답이 명시적
   으로 존재하는데도 "확인할 수 없음" (11건 중 부분 개선됐지만 완전
   해결은 아님).
6. **[확인]** 알테오젠 "당기 대비 전기" 계산은 산술 100% 정확한데도
   `_verify_derived_number()`가 음수(손실) evidence 부호 처리를 못 해
   여전히 ungrounded로 남음 — 기존 O(n²) cap 이슈와 별개의 신규 진단.

**결론**: 이번 주 고친 것들이 실제로 효과가 있다는 게 확인됐지만(API
실패율 개선, 날짜혼동/10배오류/재청킹 관련 문제 재발 없음), **전체 규모로
가니 v1 subset에서는 안 보이던 새 문제(HCX 400, 연결/별도 혼동, report_id
손상)가 드러났고, grounded율 자체는 v1 대비 하락**했다 — "고친 게 없어진
게 아니라, 표본이 커지면서 안 보이던 문제가 보이기 시작한 것"으로 해석.
다음 우선순위는 §12 참고.

### 5-F. [완료] Router 개선 후보 5건 — provenance/threshold 재조정/comparison_axis 오탐/HCX escalation hint (2026-08-29)

사용자가 우선순위 순으로 지정한 라우터 개선 후보 5개를 전부 진행(2번
provenance를 1번 threshold 재조정보다 먼저 처리 — 후자를 제대로 측정하려면
전자가 필요).

**1) RouteResult.source(provenance) 추가**: `semantic_router_wrapper.py`의
`RouteResult`에 `source: str | None = None`(하위호환 기본값) 추가.
`SemanticRouterAdapter`/`CascadingRouter`의 fast-path는 `"semantic_fast_path"`,
`HCXStructuredRouter`는 유효 route면 `"hcx_escalation"`, unclear/무효값이면
`"hcx_unclear"`, `CascadingRouter`의 escalate 경로는 하위 라우터가 세팅한
source를 그대로 통과(새로 안 만듦). "confidence" 요구사항(후보 5)은 새
필드를 추가하지 않고 `source=="hcx_unclear"`로 흡수 — HCX tool schema를
더 건드리는 건 위험 대비 효용이 낮다고 판단(사용자에게 사전 설명한 방향
그대로 진행). `agent_loop.py`의 `AgentTrace`에도 `route_source` 필드 추가.
회귀 테스트 8건(`tests/test_router.py`).

**2) margin_threshold 재조정**: 아래 §5-F-1(별도 소절) 참고 — 재현 확인,
sweep 결과, 최종 권고를 상세 기록.

**3) comparison_axis 오탐 수정** — §12의 "[신규, 2026-08-27 재검증에서도
재확인]" 항목이 바로 이것. 완료 처리는 위 §12 해당 항목 참고(중복 기재
방지).

**4) CascadingRouter → HCXStructuredRouter escalation hint** — 아래
§5-F-2(별도 소절) 참고.

전체 회귀 스위트: 작업 1(164)/작업 3(166) 시점 각각 통과 확인, 최종
카운트는 문서 하단 커밋 로그 참고.

#### 5-F-1. margin_threshold 재조정 (개선 후보 1)

**방법론**: `EVAL_SET`(55건)에 대해 semantic router(BGE-M3, threshold=0.0)로
top1/top2/margin을 실측 추출하고, margin<0.15인 33건 전체를 실제
`HCXStructuredRouter`(HCX-007, production과 동일 client)로 분류해 캐싱한 뒤
0.00~0.15(0.01 간격)로 sweep했다. 상세: `results/router_v2/margin_threshold_resweep_2026-08-29.md`.

**재현 확인**: PROJECT_STATE가 인용하던 "margin>=0.05: 23/55(42%),
accuracy=1.000"은 **재현되지 않았다** — 이번 실측은 31/55(56.4%),
accuracy=1.000. 원인은 새 버그가 아니라, "23/55"가 `routes.py`에 19개
utterance를 추가하기 **이전**(2026-08-18 13:03, 커밋 `8c5c555`)의 1차
측정값이었기 때문이다. 같은 날 바로 이어진 utterance 추가(13:11, 커밋
`c562220`) 이후 값은 `results/router_v2/summary.md`에 이미 "31/55"로
기록돼 있었고, `routes.py`는 그 이후 변경 이력이 없다(git log 확인) — 즉
이번 재현치는 최신 코드 기준 정답과 정확히 일치한다.

**Sweep 핵심 결과**: overall accuracy가 **margin_threshold=0.03~0.04에서
1.000으로 정점**을 찍고, 현재 기본값 0.05에서는 0.927로 떨어진다. 메커니즘이
명확하다 — margin<0.0266인 예시는 semantic이 전부 틀렸는데 HCX가 escalate로
10/10 전부 구제했지만, margin>=0.0377인 예시는 semantic이 이미 100% 정답인데
그걸 다시 HCX에게 물으면(threshold를 0.05 이상으로 올리면) HCX 자체 오류가
섞여 들어온다(실측: 단순조회→ownership_analysis 오분류 등 4건). 즉 0.05는
정확도(0.927<1.000)와 escalate 비율(44%>29~33%, RPM 위험)**양쪽 다** 0.03~0.04에
지배당하는 선택이었다 — trade-off가 아니었다.

**최종 권고 및 조치**: `margin_threshold` 기본값을 **0.05 → 0.03**으로
변경(`CascadingRouter.__init__`, `build_cascading_router()` 둘 다,
`src/disclosure_rag/router/hcx_router.py`). 회귀 테스트 2건 추가(기본값
고정, `tests/test_router.py`). n=55라 정확한 경계값(0.03 vs 0.04)의
정밀도는 제한적이라는 점은 결과 문서에 명시 — 방향성(0.05보다 낮은 게
낫다)은 메커니즘이 명확해 신뢰할 만함.

**부수 발견(API 신뢰성)**: 33건 HCX 호출 중 5건이 400("Unsupported
function")을 반환했고, 그중 1건은 `HCXClient`의 6회 재시도(최대 96초 대기)
로도 못 뚫어 스크립트 레벨에서 추가 재시도가 필요했다. 격리 재현 결과 같은
질의를 몇 분 후 다시 보내면 즉시 성공해서, 특정 질의 문구 문제가 아니라
확률적/일시적 API 현상으로 판단(§12 기존 RPM 관찰과 같은 계열, 이번엔 기존
재시도 상한을 넘는 사례가 실측됨 — 후속 후보로 §12에 기록).

#### 5-F-2. CascadingRouter → HCXStructuredRouter escalation hint (개선 후보 4)

**구현**: `HCXStructuredRouter.route()`에 선택적 키워드 `hint: str | None =
None` 추가(기본값 None이면 기존과 100% 동일 — 하위호환). hint가 있으면
user message(시스템 프롬프트 300자 제약과 무관한 필드)에
`f"\n\n[참고: 로컬 임베딩 분류 후보]\n{hint}"` 형태로 덧붙인다.
`CascadingRouter.route()`는 escalate 직전에 semantic top1/top2 이름+점수로
"참고용이며 최종 판단은 직접 하세요"라는 톤의 hint를 만들어 넘긴다
(`_route_hint_message`와 동일 원칙 — 힌트에 맹종 금지). 하위 라우터가
`hint` 키워드를 지원 안 하면(TypeError) hint 없이 호출하는 fallback도 있어
기존 stub 기반 테스트는 전부 무변경 통과.

**라이브 검증**: escalate 후보 33건(margin<0.15, 작업 2와 동일 데이터)을
실제 CascadingRouter(hint 포함)로 재라우팅 — **400 에러 0건**(33/33 성공).
margin<0.05(현재 production 기본 threshold 0.03 이전 실측 시점 기준 escalate
대상, 24건)의 정확도가 **hint 없음 0.833(20/24) → hint 있음 0.958(23/24)**로
개선됐다. 개별 대조 결과 3건이 hint로 새로 정답이 됐고(HCX가 unclear로
답했거나 다른 route로 오분류했던 케이스가 semantic의 이미 옳은 추측을
참고해 정답으로 수정됨), 1건은 hint가 있어도 여전히 오답, **hint 때문에
새로 틀린 케이스는 0건**이었다 — 순수 개선, 회귀 없음.

hint를 반영해 threshold sweep을 다시 계산하면 margin>=0.05 구간의
escalate_acc가 0.833~0.846(hint 없음) → 0.958~0.970(hint 있음)으로
전반적으로 개선되지만, **그래도 0.03~0.04(정확도 1.000)가 여전히 최적**이다
— hint는 threshold 선택을 덜 민감하게 만드는 안전판이지 §5-F-1의 권고를
뒤집을 근거는 아니다. 상세 표: `results/router_v2/margin_threshold_resweep_2026-08-29.md`
"참고: HCX escalation hint 도입 시 재계산" 절.

**테스트**: `tests/test_router.py`에 hint 하위호환(6건: 없을 때 user
message 불변, 있을 때 포함 확인, CascadingRouter가 실제로 hint를 만들어
넘기는지, hint 미지원 Router에도 안전하게 fallback하는지 등) 추가.

### 5-G. [완료] 3파전 라우터 아키텍처 실험 결론 + sector/peer 선택 로직 프로덕션 이식 (2026-08-29)

`new/` 아래에서 독립적으로 구현한 3개 아키텍처(기존 CascadingRouter / `new/`
Phase 1 순수 rule 프로토타입 / Entity Resolver+Task Router+Evidence
Router+ComplexityDetector 하이브리드)를 비교한 실험의 최종 결론과, 그 실험에서
유일하게 검증된 이식 가치가 있던 부분(sector/peer 선택)을 실제 프로덕션에
반영했다.

**3파전 결론**: `EVAL_SET` 55건 전체 기준 **기존 CascadingRouter(margin=0.03+
hint) 55/55(1.000) vs 하이브리드 HCX-005 zero-shot Task Router 45/55(0.818)**
— 매번 LLM으로 분류하는 게 더 정확할 거라는 직관과 반대로, 오래 튜닝된 기존
시스템이 정확도와 호출 비용(하이브리드는 질문당 최소 2회, 기존은 평균 0.29회)
양쪽에서 압승했다. 하이브리드 오분류 10건 중 7건이 "event_analysis →
single_lookup" 한 방향으로 쏠린 체계적 오류(예: "공장 증설 계획 있어?"를
단순조회로 오인)였다 — Task/Evidence Router를 LLM으로 교체하는 안은 폐기.

유일하게 가치가 검증된 부분은 `new/app/query/entity_resolver.py` +
`peer_selector.py`의 **"주요 OO기업 N곳"** 처리 — sector filter → market_cap
내림차순 → top N을 LLM 없이 deterministic하게 계산하는 규칙. 기존 시스템은
이 개념 자체가 없어서 회사명이 명시 안 된 sector/peer 질문은
`companies=[]`로 그냥 실패했었다(§37 Case 5/6 대응 불가).

**이식 내용** (`src/disclosure_rag/entity/entity_extractor.py`,
`new/`를 import하지 않고 동일 로직을 독립적으로 재구현 — `new/`는 비교
실험용 격리 코드로 계속 남겨둠):

- `EntityExtractor.__init__`이 이미 로딩하던 `universe.csv`(`load_universe`)
  순회에서 `sector`/`sector_no`/`industry`/`market_cap` 컬럼도 함께 읽어
  `sector → [(corp_name, market_cap, sector_no), ...]`(market_cap 내림차순
  정렬 완료) 인덱스를 미리 만든다 — 별도 sector alias 사전 불필요(Company
  Master 자체가 유일한 출처, `new/`와 동일 원칙).
- `_TOP_N_PAT`("주요 OO기업/업체/회사 N곳/개/사"), `_detect_sector`("·"로
  쪼갠 부분 문자열 substring 매칭, 가장 긴 매칭 우선), `_detect_industry`
  ─ `new/app/query/entity_resolver.py`에서 그대로 이식.
- `extract()`: 명시적 회사명이 하나도 없을 때만 순서대로 시도 —
  ① top_n 패턴+sector 매칭 시 그 sector의 top N(`peer_selection=
  "market_cap_top_n"`, `requested_top_n`), ② top_n 없이 sector만 매칭되면
  sector 전체, ③ industry만 매칭되면 industry 전체, ④ 전부 실패하면
  `entity_scope="market"`. 채워진 회사 목록은 기존 `companies`/`company_count`
  필드에 그대로 들어가므로(신규 필드 `entity_scope`/`sector`/`sector_no`/
  `industry`/`peer_selection`/`requested_top_n`는 provenance 기록용 부가
  정보), `comparison_axis`(`company_count>=2`) 계산도 자동으로 올바르게
  작동한다.
- sector/industry로 채워진 회사는 원문에 리터럴로 등장하지 않으므로
  `company_spans`는 비워둔다 — `query_normalizer.normalize_query()`가
  존재하지 않는 span을 잘못 치환하는 사고를 원천 차단.
- `agent_loop.py`의 `_route_hint_message()`에 provenance 안내문 추가 —
  `entity_scope in ("sector","industry")`면 "회사명이 직접 언급되지 않아
  'OO' 업종 기준으로 자동 선정된 목록"임을 Agent에게 명시한다(사용자가
  직접 지정한 회사 목록으로 오인해 임의로 좁히거나 다른 회사를 빠뜨렸다고
  착각하는 것 방지).

**실측 확인**(실제 `corpus/universe.csv` 기준, 2026-08-29 스냅샷):
"주요 방산기업 3곳 비교해줘" → `한화에어로스페이스(504,806) >
현대로템(172,881) > LIG디펜스앤에어로스페이스(167,640)` (방산·항공우주,
sector_no=14, top 3), "2차전지 기업들 매출 비교해줘" → sector 전체
(`LG에너지솔루션/삼성SDI/에코프로비엠`, peer_selection=None), "삼성전자랑
SK하이닉스 비교해줘"는 명시적 회사가 있으므로 sector 추론을 건너뜀 —
전부 의도대로 동작.

**아직 이식하지 않은 것(범위 밖)**: `routes.py`에는 sector/peer형 질문에
대응하는 utterance가 아직 없다 — 이런 질문이 실제로 router 앞단에 들어오면
현재는 예시 부재로 margin이 낮게 나와 HCX escalation으로 넘어갈 가능성이
높다(오답은 아니지만 라우팅 비용 증가). 필요성이 실측으로 확인되면 다음
후보로 진행.

**테스트**: `tests/test_entity_extraction.py`에 7건(top N 선택, market_cap
내림차순 순서 고정, top_n 없는 sector 전체, 명시적 회사 있을 때 sector 추론
건너뜀, market scope, span 비어있음/normalize_query 불변), `tests/test_agent.py`에
2건(hint에 자동선정 안내 포함/명시적 회사엔 안내 없음) 추가. 전체 스위트
178→180 통과(`pytest tests/ -m "not slow"`).

### 5-H. [완료] §7 우선순위 1·3 — 연결/별도 재무제표 구분 원칙 + has_citation 손상 탐지 강화 (2026-08-29)

사용자가 파이프라인 아티팩트의 "다음 우선순위" 목록에서 1번과 3번을 지정해
바로 진행.

**우선순위 1 — 연결/별도 재무제표 혼동**(SK하이닉스 사례, §12 문제 2번):
`answer_generator.py`의 `ANSWER_SYSTEM_PROMPT`에 원칙 8번 추가 — 질문에
연결/별도 명시가 없으면 evidence의 Section 경로/표 제목에서 "연결" 여부를
확인해 연결기준을 우선 사용하고, 별도기준 수치를 쓸 때는 반드시
"(별도기준)"이라고 표시하며, 어느 쪽인지 확인이 안 되면 그 사실 자체를
밝히도록 지시(연결기준으로 함부로 단정 금지). `generate_answer()`가
`tools=`를 안 쓰는 경로라 300자 system prompt 제약과 무관 — 안전하게 확장.
테스트: `tests/test_agent.py::test_answer_system_prompt_requires_
consolidated_vs_standalone_distinction`.

**우선순위 3 — report_id 인용 형식 손상 + `has_citation` 느슨한 폴백**(한미
반도체 사례, §10/§12 기존 약점): `validator.py`의 `has_citation` 판정에서
`or "근거" in answer` 폴백을 완전히 제거 — 이제 evidence가 있는 한
report_id/chunk_id 문자열이 답변에 실제로 등장해야만 `has_citation=True`다.
동시에 "인용을 아예 안 함"과 "인용은 시도했는데 형식이 깨짐"을 구분하기
위해 `_citation_looks_corrupted()`를 새로 추가했다 — report_id 형식이
`{doc_group}_{YYYYMMDD}{일련번호}`로 고정이라는 점을 이용해, 답변 안에서
같은 doc_group + 같은 접수일자(앞 8자리)를 가리키는 손상된 토큰(예:
`periodic_20260515_01572`가 실제 `periodic_20260515001572`를 가리킴)을
찾으면 "형식 손상" 경고를, 그런 토큰조차 없으면 "인용 누락" 경고를 남긴다.
**주의**: 손상된 인용도 `has_citation=False`로 그대로 실패 처리한다(사용자가
실제로 그 report_id를 찾아 검증할 수 없는 건 마찬가지이므로) — 경고 문구만
구분해서 원인 추적을 돕는 것이지 pass 조건을 완화하는 게 아니다.

기존 `test_validator_catches_hallucinated_citation`(evidence 자체가 없는
경우)은 `has_any_evidence` 게이트에서 이미 False였으므로 폴백 제거의
영향을 안 받아 그대로 통과 확인. 신규 테스트 2건 추가(`tests/test_agent.py`
`test_validator_rejects_citation_word_without_matching_report_id`,
`test_validator_flags_corrupted_report_id_citation_distinctly`). 전체
스위트 180→183 통과.

### 5-I. [완료] §7 우선순위 6 — 검산기 O(n²) cap 제거 + 음수(손실) evidence 부호 처리 (2026-08-29)

알테오젠 "당기 대비 전기 영업이익" 사례(matrix.csv 실측: 산술은 100%
정확한데 ungrounded로 남음)의 근본원인 2가지를 둘 다 고쳤다.

**O(n²) → O(n log n) 재작성**: `_verify_derived_number()`가 기존엔 모든
evidence 숫자 쌍 `(v1, v2)`을 전수조사(`for s1 in parsed: for s2 in
parsed: ...`)해서, evidence 숫자가 `_MAX_VERIFY_NUMBERS=200`개를 넘는
재무제표 청크에서는 안전장치가 검산 자체를 건너뛰었다(정확한 뺄셈도
ungrounded로 남는 원인). `v1 - v2 = target`/`v1 + v2 = target`/
`v1 / v2 * 100 = target` 세 형태 모두 "v1을 고르면 필요한 v2가 대수적으로
정확히 하나로 정해진다"는 성질을 이용해, v1마다 필요한 v2 값을 O(1)로
계산하고 정렬된 evidence 값 배열에서 `bisect`로 찾는 방식으로 바꿨다 —
이러면 cap이 성능 안전장치가 아니라 병리적 입력 방어용 상한으로 의미가
바뀌므로 200 → 4000으로 크게 올렸다.

**음수(손실) evidence 부호 처리**: 기존엔 evidence 숫자를 전부 양수로만
읽었다(정규식이 부호를 안 보므로) — 그래서 "(9,736,838,487)원"처럼
한국 회계 관행상 손실을 괄호로 표기한 값이 실제로는 음수인데 양수
9,736,838,487로 취급됐다. "당기 - 전기(적자)" 단순 뺄셈은 +/− 두 후보를
모두 시도하는 기존 구조 덕에 우연히 통과했지만, 그 위에서 파생되는
흑자전환 성장률(예: "약 360.92% 증가") 같은 2차 계산은 부호를 모르면 검증
불가능했다. `_extract_signed_numbers()`를 새로 추가해 괄호로 감싸진 숫자
또는 명시적 마이너스 부호가 붙은 숫자를 실제로 음수로 인식한다(단,
"2020-2023" 같은 연도 범위의 하이픈은 바로 앞이 숫자면 범위 표기로 보고
음수 처리 안 함). 흑자/적자 전환 성장률처럼 `(v1 - v2) / |v2| * 100 =
target` 형태(3항 조합, 대수적 O(1) 역산이 target의 반올림 오차를 크게
증폭시켜 불안정)는 별도로 `_find_swing_growth_expr()`을 두어, 전방향
재계산으로 검증하는 방식을 썼다 — 이건 여전히 O(n²)이라 더 작은
`_MAX_SWING_PAIR_NUMBERS=300` 상한으로 방어한다.

**실측 확인**: 알테오젠 v2 배치 실제 사례(당기 25,403,990,856원, 전기
(9,736,838,487)원)를 그대로 재현해 `35140829343`(뺄셈)과 `360.92`(성장률)
둘 다 `verified_derived_numbers`로 통과함을 직접 실행으로 확인. 300개
더미 숫자 + 실제 계산 1건을 섞은 케이스로 cap 상향도 확인. 기존 회귀
테스트(부정확한 계산은 여전히 ungrounded로 남아야 함)는 그대로 통과.

**테스트**: `tests/test_agent.py`에 4건 추가(200개 초과 evidence에서도
검산 동작, 음수 부호 걸친 뺄셈+성장률 검증, `_extract_signed_numbers()`
괄호/마이너스 인식, 연도 범위 하이픈 오탐 방지). 전체 스위트 183→187 통과.

## 6. 주요 파일과 역할

```
src/disclosure_rag/
  common/unicode_utils.py       NFC 정규화 + 세그먼트 단위 path resolver
  common/doc_tree.py             Parser 공통 중간 표현
  parsing/dart_xml_parser.py    periodic/major/holding 공용
                                 (TR 500행 cap + bare &/< 사전 이스케이프)
  parsing/exchange_parser.py    exchange 전용 (위장 HTML, lxml.html 사용)
  parsing/table_parser.py       rowspan/colspan grid 확장 + RLE dedup
  chunking/chunk_schema.py      공통 Chunk Schema + filter_leaf_chunks()
  chunking/chunkers.py          Parent-Child / flat + _is_toc_table() 필터
  correction/correction_graph_builder.py   transitive chain resolution
  retrieval/{tokenizers,bm25_retriever,embeddings,qdrant_store,dense_retriever,
             fusion,reranker,hybrid_retriever,metadata_filter}.py
  entity/{entity_extractor,query_normalizer}.py
  router/{routes,encoder_adapter,semantic_router_wrapper,eval,eval_dataset}.py
  agent/{hcx_client,tools,calculation,agent_loop,evidence,answer_generator,
          validator,ask}.py      온라인 파이프라인 전체(dedup guard 포함)
  experiments/                   Stage 1~14 실험용 metrics/variants
  pipeline.py                    오프라인 오케스트레이터

scripts/
  embed_full_corpus_gpu.py       GPU 서버용 전체코퍼스 임베딩(체크포인트/재개 지원)
  generate_route_pilot.py        Router 튜닝용 질문 데이터 HCX 생성 스크립트

config/financial_terms.txt      Kiwi 사용자 사전
config/metric_terms.txt          Entity Extraction 지표 키워드
eval/gold_queries.json           40개 gold query(validation 30/test 10)
results/{stage}/                 Stage 1~14 실험 결과
results/generalization_check/    회사 일반화 검증(matrix.csv, summary.md, raw json들)
results/router_tuning/           Router 튜닝 검토(rubric.md, pilot 데이터)
results/FINAL_SUMMARY.md         Stage 1~14 최종 요약
tests/test_agent.py              agent_loop/validator 회귀 테스트(스텁 클라이언트, API 불필요)
tests/test_chunkers.py           TOC 필터 회귀 테스트 포함
.env                              HCX_API_KEY, HCX_MODEL=HCX-007 (git 제외)
```

---

## 7. 중요한 명령어

```bash
# venv
uv venv --python 3.11 .venv && uv pip install -p .venv/bin/python -e .

# corpus 검증
.venv/bin/python -m disclosure_rag.common.corpus_validator corpus

# 전체 코퍼스 파싱+청킹 (4,204건, ~9분)
.venv/bin/python -m disclosure_rag.pipeline corpus

# 테스트
.venv/bin/python -m pytest tests/ -m "not slow"   # 빠른 것만(~18초, 73개)
.venv/bin/python -m pytest tests/                  # 전체(HCX API+모델 필요)

# git
cd "/Users/isang-won/Desktop/공시 agent"
git add -A && git commit -m "..." && git push
```

**GPU 서버(전체 코퍼스 재임베딩용)**: `mileb-v100` (203.246.112.74:10427,
user adminuser), SSH config 에 등록돼 있으나 이 세션 환경엔 인증키 없음
(비밀번호 인증 필요 시 사용자에게 요청). `scripts/embed_full_corpus_gpu.py`
를 그대로 서버에 올려서 실행 → `gpu_embeddings/shard_*.pkl` 로 결과 나옴 →
scp/rsync 로 로컬 회수.

**전체 70개사 임베딩 재사용 방법** (재임베딩 불필요):
```python
import pickle, glob
wanted_report_ids = {"periodic_xxx", ...}  # 필요한 문서만
out_chunks, out_vectors = [], []
for f in sorted(glob.glob("gpu_embeddings/shard_*.pkl")):
    d = pickle.load(open(f, "rb"))
    for c, v in zip(d["chunks"], d["vectors"]):
        if c.report_id in wanted_report_ids:
            out_chunks.append(c); out_vectors.append(v)
```
**주의**: 467,043개 전체를 한 번에 메모리에 올리면 벡터만 ~15GB+ 로 이
머신(16GB RAM)에서 OOM 위험 — 항상 필요한 회사만 필터링해서 쓸 것.

**실험용 eval 코퍼스(Stage 1~14 용)**: 삼성전자 1개사, 33개 문서, doc_id
목록은 `/tmp/stage_eval_doc_ids.json`(세션 임시 — 사라지면 아래 재생성
코드 사용). BGE-M3 임베딩 캐시: `/tmp/bgem3_chunks_vectors.pkl`(1,411
chunks). 이유: 전체 코퍼스 임베딩 비교는 CPU-only 환경에서 불가능(BGE-M3
기준 전체 76.8시간 실측).

```python
# doc_ids 재생성 (삼성전자 33개 문서: periodic 최신 2 + major 전체 19 +
# exchange 전체 2 + holding 최신 10)
from disclosure_rag.common.manifest_loader import load_manifest
manifest = load_manifest("corpus")
samsung = [r for r in manifest if r.corp_name == "삼성전자"]
periodic = sorted([r for r in samsung if r.doc_group=="periodic" and not r.is_correction], key=lambda r: r.rcept_dt, reverse=True)[:2]
major = [r for r in samsung if r.doc_group=="major"]
exchange = [r for r in samsung if r.doc_group=="exchange"]
holding = sorted([r for r in samsung if r.doc_group=="holding"], key=lambda r: r.rcept_dt, reverse=True)[:10]
doc_ids = [r.doc_id for r in periodic+major+exchange+holding]
```

---

## 8. 실험 결과 요약 (Stage 1~14, validation set n=30 기준)

| Stage | Winner | 핵심 수치 |
|---|---|---|
| 1 Chunking | Section-aware+Parent-Child | R@5=0.706 R@10=0.802 MRR=0.682 NDCG@10=0.667 |
| 2 BM25 Tokenizer | char_2gram(수치상 잠정) / **Kiwi(실질 baseline)** | char_2gram R@10=0.912 MRR=0.757 vs Kiwi R@10=0.802 MRR=0.682 — Kiwi가 도메인사전 확장 가능해 Stage4 이후 전부 Kiwi로 진행 |
| 3 Dense Embedding | BGE-M3(실무) / e5-instruct(정확도상한) | bge-m3 R@10=0.840(591.7ms) vs e5 R@10=0.867(3710.0ms, 6.3배 느림) |
| 4 Fusion | Normalized Weighted Fusion | R@10=0.903 R@20=0.940 MRR=0.713 NDCG@10=0.735 |
| 5 Reranker | No-Reranker(CPU 배포) | Hit@1=0.633 MRR=0.712(42.7ms) vs bge_reranker Hit@1=0.667 MRR=0.773(11,053.8ms, 258배 느림) |
| 8 Entity Extraction | Rule only | company_EM=1.0 metric_F1=0.971 period_F1=1.0(12μs) — hcx 계열 압승, trade-off 없음 |
| 9 Router | ~~hcx_structured_router~~ → **CascadingRouter**(v2, §5-A) | 원래 n=30: hcx 0.800/4.5s vs semantic 0.600/38.7ms. n=54~55 재측정(2026-08-18) 후 뒤집힘, routes.py 경계 재정리까지 반영한 최종: semantic 0.836/40ms, hcx 0.685/2.26s, **CascadingRouter 0.889/1.01s 채택** — `results/router_v2/summary.md` |
| 10 Agent HCX 모델 | **HCX-007** | tool_acc=0.966 arg_acc=0.980 task_success=0.793(13.9s) — 정확도·지연·비용 전부 우위 |
| 11 E2E RAG | 시나리오 분리 | hybrid_reranker R@5=0.820(5.4s, 정확도 최우선) vs full_agentic R@5=0.622(15.7s, task_success=0.759, 실시간성+도구조합) |
| 12 Answer HCX 모델 | **HCX-005**(Agent와 다른 모델) | pass_rate 0.750 vs HCX-007 0.690 vs DASH-002 0.321 |
| 14 Final E2E(TEST SET n=10, 유일 사용) | efficiency(reranker off) | task_success=0.700 pass_rate=1.000(23.1s) — n=10이라 통계적으로 약함 |

**최종 확정 baseline**: Section-aware+Parent-Child chunking + Kiwi BM25 +
BGE-M3 dense + Normalized Weighted Fusion + No-Reranker + Rule-only Entity
+ **CascadingRouter**(§5-A, semantic margin 게이팅 + HCX-005 escalation,
`router/hcx_router.py`) + **Agent=HCX-007** + **Answer=HCX-005**. `.env`의
`HCX_MODEL=HCX-007`(agent 기본값). answer 전용 모델 분리(2026-08-18)와
CascadingRouter 조립(2026-08-25, `router.hcx_router.build_cascading_router`)
둘 다 완료 — §12 참고. **주의**: Fusion 은 위 표에 "Normalized Weighted
Fusion"으로 적혀있지만 실제 `retrieval/fusion.py`엔 그 함수가 없고 RRF만
있다(2026-08-25, Kim 브랜치 감사로 발견, §10 참고) — 문서와 코드가 다시
어긋난 사례이니 다음에 손볼 것.

---

## 9. 실패했던 접근 — 다시 하면 안 되는 것

1. **HCX Agent/Router system prompt 를 300자 넘게 쓰지 말 것** — tool-calling
   결정적 실패(3회 독립 재현).
2. **HCX tool-calling 요청에 `tools`+`maxTokens` 동시 사용 금지.**
3. **Qwen3-Embedding-0.6B / Qwen3-Reranker-0.6B 를 이 환경에서 재시도하지
   말 것** — 4차례 이상 시도 후 전부 실패 확정(환경 이슈로 판단).
4. **전체 코퍼스로 CPU Dense 임베딩 비교 실험을 시도하지 말 것** — 모델당
   최소 70시간+. GPU 서버 활용(§7) 또는 축소 corpus로 진행.
5. **Retrieval 인덱스에 parent chunk 를 포함시키지 말 것** — `filter_leaf_
   chunks()` 필수(안 그러면 임베딩 30분+ 로 폭주).
6. **표 파싱에서 TR 개수 상한 없이 처리하지 말 것.**
7. **NDCG 계산 시 report-level gold 를 chunk 단위로 중복 카운트하지 말 것**
   (`ndcg_at_k` 이미 report-level dedup, 회귀 테스트로 고정됨).
8. **HuggingFace 모델 다운로드 시 xet 가속 다운로더를 기본값으로 두지
   말 것** — `HF_HUB_DISABLE_XET=1` 권장.
9. **malformed XML 대응을 증상별 캡(TR 500행 등)으로만 때우고 넘어가지
   말 것** — 근본 원인(bare `&`/`<`)을 찾으면 훨씬 광범위한 문제(문서 전체
   유실)였음이 §10에서 드러남. 이번처럼 "이상한 결과가 나오면 반드시 원문
   대조부터" 하는 습관이 중요.
10. **bare `<` 만 고치고 속성값 안 여분 따옴표(malformation C)는 "위험해서"
    미루지 말 것 — 둘은 서로를 가리는 관계다.** 2026-08-25 Kim 브랜치 병합
    실측: bare `<` 가 만드는 유령 element 가 스택을 깊게 쌓아둔 덕에, 속성값
    파싱 실패가 유발하는 연쇄 tag mismatch 가 DOCUMENT 까지 pop 되지 못하고
    멈춰 있던 문서가 실존한다. 그 상태에서 `<` 만 고치면 완충재가 사라져
    루트가 조기 종료되고 이후 내용이 통째로 폐기된다(Kim 실측: 현대차
    813천자→422천자, KB금융 1,634천자→814천자 — **B만 고치면 오히려 본문이
    줄어든다**). "위험해서 부분적으로만 고친다"는 판단 자체가 함정이었다 —
    두 malformation 을 **함께** 고쳐야 순수 이득이 된다(bytes 레벨에서 실제
    파싱 실패가 난 지점만 최소 개입으로 수리하면 안전하게 가능함이
    `xml_sanitizer.py` 로 증명됨). §12 TODO #3(3번째 malformation 패턴)도
    이번에 완료 처리.

---

## 10. 발견된 문제 (버그 픽스 이력)

### 2026-08-25 발견, 미수정 — `Normalized Weighted Fusion` 문서-코드 불일치

§8 표/§8 "최종 확정 baseline"이 Fusion 채택 결과로 "Normalized Weighted
Fusion"을 적어놨지만, 팀원(Kim)의 독립 감사에서 **`retrieval/fusion.py`에
그 함수가 아예 없다**는 게 드러났다 — 실제로 `hybrid_retriever.py`가 쓰는
건 `reciprocal_rank_fusion()`(RRF)뿐이다(직접 `grep -rl
normalized_weighted_fusion .` → 0건으로 확인). CascadingRouter/answer 모델
분리와 같은 계열의 "ablation으로 이겼는데 production에 안 배선됨" 패턴이
Fusion에서도 벌어지고 있었던 것 — 이번이 세 번째 사례. RRF는 점수를
버리고 순위만 쓰기 때문에, BM25가 확신하는 1등이 Dense의 애매한 후보에
밀리는 문제가 있을 수 있다(Kim 지적). 아직 안 고침 — §12 후보 참고.

### 최근 발견/수정 (회사 일반화 검증 트랙, 2026-08-15~16)

- **[수정됨, 검증 완료]** malformed XML(escape 안 된 `&`, `<`)이 lxml
  recover 모드를 오동작시켜 문서 대부분이 TABLE 안에 파묻혀 유실되는 버그.
  400개 샘플 중 116건(29%) 영향 확인(현대차/현대모비스/POSCO홀딩스/
  KB금융/신한지주/하나금융지주/메리츠금융지주 등). `dart_xml_parser.py`에
  `_escape_bare_special_chars()` 추가해 파싱 전 사전 치환 — 재스캔 결과
  116건→0건(같은 진단 기준). KB금융/현대자동차 실제 질의로 end-to-end
  재검증 완료(§5 참고, `matrix.csv`에 [재검증] PASS 행 추가). 회귀 테스트
  4건 `tests/test_parsers.py`에 추가.
- **[2026-08-25 Kim 브랜치 병합으로 수정 완료]** 3번째 malformation 패턴(속성값 안
  이스케이프 안 된 따옴표, 예: `ENG="" KB Insurance Co., Ltd ""`) —
  전체 코퍼스 재스캔(2,732건)으로 정밀 정량화됐던 시점엔 627건(23.0%)
  영향, 전부 periodic, 금융지주·대기업 계열의 특수관계자/종속기업
  현황 표(영문 회사명을 ENG 속성에 넣는 표)에 집중. 속성값 경계를
  정규식으로 안전하게 판별하기 어려워 미뤄왔던 것을, 팀원 Kim 이 별도
  작업본에서 bytes 레벨 상태기계 파서(`xml_sanitizer.py`, "닫는 따옴표
  직후 잡문자가 오면 다음 속성명=/> 앞의 진짜 닫는 따옴표를 찾아 재작성"
  방식)로 안전하게 해결 — 상세는 아래 "Kim 브랜치 병합" 절 참고.
- **[수정됨]** TOC(목차) 표가 청크로 인덱싱돼 BM25 허위매칭 유발 —
  `chunkers.py`의 `_is_toc_table()`로 필터링.
- **[수정됨]** 카운팅 질문("몇 건이야?")이 calculation route로 오분류돼
  `calculate_cagr`을 무의미한 인자로 반복 호출 — system prompt 문구 추가 +
  동일 tool 중복호출 방지 dedup guard(`agent_loop.py`).
- **[수정됨]** Validator 오탐 2건 — (a) `get_correction_history`만 호출된
  경우 citation 오탐, (b) "(약 ...)" 괄호 안 숫자 재표기 오탐(`validator.py`).
- **[미수정]** 다중행 표 요약 시 답변 모델이 숫자를 잘못 읽음(알테오젠
  80억원→800억원 10배 오답, validator가 정확히 잡아냄 — 오탐 아님).
- **[미수정]** "정정" 단어에 과민반응해 `get_correction_history` 오선택
  (이벤트성 계약해지 질문에서).
- **[미수정]** 비교 질문에서 `search_disclosures` 대신 `get_latest_report`
  오선택.
- **[미수정]** 업종별 회계 용어 차이 미적응(금융지주 "매출액"↔"영업수익").
- **[미수정]** 복합추론_Open(다년도/장문 비교) retrieval breadth 부족.
- **[관찰됨, 버그 아님]** 답변 모델이 evidence 숫자로 스스로 계산한 파생값
  (뺄셈 등)이 validator에 ungrounded로 잡히는 경우 있음 — 검산 결과 계산은
  정확했던 사례 확인(알테오젠 10배 오류와는 다른 성격, 구분해서 볼 것).
- **[기존부터 있던 flaky 테스트, 파싱버그와 무관 확인됨]**
  `tests/test_dense_retriever.py::test_bge_m3_dense_retriever_finds_
  relevant_chunk` — top-5에 R&D 관련 청크는 들어오지만 assertion이 요구하는
  정확한 문자열 "연구개발"이 없어 실패. `git stash`로 파싱 수정을 뺀
  베이스라인에서도 동일하게 실패함을 확인(2026-08-16) — 회귀 아님, 원래도
  brittle 했던 테스트. 고치려면 assertion을 "R&D"도 인정하도록 완화.

### 표 semantic block chunking 회귀 수정 (2026-08-24, 무인 야간 작업)

- **[수정됨, 회귀테스트 14건 추가]** 표 청킹이 "1. 매출액"류 상위항목+하위행
  묶음(semantic block)을 무시하고 `max_rows_per_chunk`(20)/`max_tokens_
  per_chunk`(1000) 같은 순수 행count/토큰 기준으로만 잘라, 의미적으로 붙어
  있어야 할 항목이 서로 다른 chunk 로 갈라지는 버그. **실제 재현**:
  SK하이닉스 사업보고서(`periodic_20260317000635`, 지역별 매출/영업이익
  표, "192,972,588" 검색)에서 "1. 매출액"의 계(192,972,588백만원) 바로
  다음에 오는 "2. 영업이익"의 계(47,206,319백만원, "2025년 영업이익은
  얼마야?" 질문의 정답)가 옛 알고리즘(46행짜리 표를 20행 단위로 자름)
  에서는 서로 다른 chunk 로 분리됐다 — 단일 chunk 검색으로는 정답을 찾을
  수 없었다(100문항 배치 §5-0/§10 "2025년 재무지표 검색누락" 11건 중
  SK하이닉스 영업이익 사례가 여기 해당, 다른 회사 사례는 별개 원인일 수
  있어 전부 이걸로 설명되진 않음 — 후속 확인 필요).
  - **Detector**: `table_parser.detect_semantic_blocks()` — 번호/계층
    표기("1." "1)" "(1)" "가." "(가)" "I." 유니코드 로마숫자 "Ⅰ." 포함) +
    셀 들여쓰기(`TableCell.indent`, `dart_xml_parser._cell_indent()`가
    strip 전 원본에서 계산) + rowspan 확장 시 동일 origin_id 반복
    (`TableCell.origin_id`) 3가지 deterministic 신호를 조합해 semantic
    block 경계를 찾는다. 신호가 전혀 없는 평평한 표는 기존처럼 행 단위
    그대로 유지(정규식 하나로 다 해결하려 하지 않음).
  - **Packer**: `chunk_schema.render_table_node_fragments()` — 우선순위
    1순위 semantic block 보존 > 2순위 max_tokens 예산 > 3순위(구조 없는
    표만) max_rows fallback. 하나의 block 이 그 자체로 예산을 넘으면만
    내부적으로 추가 분할하고, 분할된 모든 조각에 title_hint/unit_hint/
    header/`"[block라벨 i/n]"` 을 반복 삽입("계"만 남은 마지막 조각도
    어느 항목 합계인지 독립적으로 식별 가능). 기존 `render_table_node()`
    는 텍스트만 필요한 호출부(`chunking_variants.py`) 하위호환용 wrapper
    로 유지.
  - **Metadata**: `ChunkSchema`/`PackedUnit`에 `table_id`/`semantic_groups`/
    `metric_hints`/`table_chunk_index`/`table_chunk_count`/`prev_table_
    chunk_id`/`next_table_chunk_id` 추가(전부 기본값 있어 하위호환 유지,
    기존 필드는 이름/위치 그대로 보존).
  - **Sibling expansion**: `tools.make_search_disclosures_tool`에
    `expand_table_siblings=True`(기본), `max_table_sibling_expansion=1`
    옵션 추가 — 검색된 chunk 가 table_id 를 가지면 같은 표의 다른 chunk
    (query 와 metric_hint 가 겹치는 것 우선, 그 다음 표 안 거리순)를
    evidence 후보에 추가한다. BM25/Dense/Fusion 스코어링 로직 자체는
    건드리지 않음(§12 TODO 에 score boost 후보로 기록) — sibling 은
    score=None 으로 추가됨.
  - 수정 파일: `common/doc_tree.py`, `parsing/table_parser.py`,
    `parsing/dart_xml_parser.py`, `chunking/chunk_schema.py`,
    `chunking/packer.py`, `chunking/chunkers.py`, `agent/tools.py`.
  - 회귀 테스트: `tests/test_table_semantic_chunking.py`(신규 14건 —
    detector 단위테스트, packer 우선순위, oversized block 분할, 실제
    XML 4개(`exchange/SK하이닉스/{20240424800596,20240726800615,
    20241220800005,20260225801974}`) + SK하이닉스 사업보고서 regression,
    sibling expansion on/off) + `tests/test_chunkers.py`에 KeyValueNode
    긴 value characterization 1건 추가. 기존 97건 전부 그대로 PASS(회귀
    없음), 전체 112건 PASS.
  - **실제 XML 조사로 밝혀진 사실**: 사용자가 예상한 "17×4 TableNode 3개 +
    9×3 KeyValueNode 1개" 구조는 원본 HTML 의 raw expand_grid 크기
    기준으로는 맞았지만(실측: 17×4 3건, 9×3 1건), colspan 확장 셀이
    RLE 로 축약되면서 4번째 열이 3번째 열의 중복이라 전부 3열 이하로
    줄어들어 **4개 파일 전부 KeyValueNode 로 분류됨(TableNode 0개)** —
    기존 `classify_grid()`의 의도된 동작이고 버그 아님.

### Kim 브랜치(pipeline-kim) 감사 결과 병합 (2026-08-25)

팀원 "Kim"이 우리 리포를 복사해서 독립적으로 파싱/표 정합성 버그를 감사·
수정한 작업본(`~/Downloads/pipeline-kim`, 읽기 전용 참고)의 검증된 수정
5가지를 새 base 로 삼고, 그 위에 2026-08-24 밤에 구현한 semantic block
table chunking(바로 위 절)을 다시 얹어서 병합했다. Kim 쪽은 절대 수정하지
않고 전부 우리 리포에만 반영. 상세 diff 분석은 `docs/kim_merge_analysis.md`.

- **xml_sanitizer.py(신규)**: bare `&`(무죄, 구조 안 깨짐)/bare `<`(구조
  붕괴 원인)/속성값 안 여분 따옴표(문서 절단 원인, §9 교훈 10 참고) 3종을
  bytes 레벨 상태기계로 정밀 수리. `dart_xml_parser.py`가 기존
  `_escape_bare_special_chars`(bare &/< 만) 대신 이걸 쓰도록 교체하고,
  `_parse_with_sanitizer()`로 "정리가 오히려 손해면 원본 파싱으로 폴백"
  안전망도 추가. 우리 코퍼스로 실측(전/후 비교, `git stash` 로 대조):
  현대자동차(`periodic_20260318001394`) BODY 직속 SECTION-1 7→14개 회복,
  본문 총량은 6개사(현대자동차/메리츠금융지주/삼성SDI/LG에너지솔루션/
  삼성전자/한미반도체) 전부 증가(+17~102%, 예: 현대차 381,379→770,557자,
  LG에너지솔루션 357,262→464,464자) — 줄어든 문서는 하나도 없음(Kim 의
  핵심 주장과 일치). 4개사는 SECTION-1 개수 자체는 우리 코퍼스의 최신
  버전 필터링에서 이미 안 깨져 있었지만(Kim 이 테스트한 특정 필링과 우리
  "latest" 필링이 달라 정확히 같은 문서는 아님), `[TABLE_PARSER] TR 수
  비정상` 캡 경고가 수정 후 전부 사라져 표 내부 절단은 동일하게 존재했고
  고쳐졌음을 확인.
- **table_parser.expand_grid()**: RLE 축약 저장 대신 정규 그리드 그대로
  저장, span 복제 칸은 `TableCell.dup_left`/`dup_up` 플래그로 표시해
  렌더링에서만 빈칸 처리(열 수 유지 + 텍스트 중복 방지 동시 해결).
  Kim 의 `_GridCell.origin_id` 래퍼는 우리가 이미 갖고 있던
  `TableCell.origin_id`(어젯밤 semantic block 검출용으로 추가)와 개념이
  동일해 래퍼를 없애고 `TableCell` 필드로 통합.
- **classify_grid() 1열 행 처리**: `else: continue`(버림) → `TextNode(
  from_table_row=True)`(보존). `_scan_hints()`로 unit_hint/period_hint 를
  실제로 채움(호출부 `dart_xml_parser.py`가 파라미터에 값을 넘긴 적이
  없어 전 코퍼스 0%였던 죽은 코드였음).
- **field_codes 재설계**: `dict[셀텍스트→코드]`(텍스트 충돌 시 덮어써서
  소실 + unit_value 미보존) → `list[FieldRef(code, unit, unit_value, text,
  row, col, key)]`. repo 전체에서 field_codes 참조처가 chunking/*.py +
  테스트뿐임을 grep 으로 확인 후 진행(다른 계층 영향 없음).
- **packer.split_long_text()**: 문단>줄>문장>어절>문자 순 재귀 분할로
  "버퍼가 비어있으면 오버사이즈 노드가 그대로 통과되는" 가드 버그 수정.
  **병합 중 추가로 발견한 버그**: 표가 여러 chunk 로 쪼개지는 경로는
  sibling expansion 순서를 지키려고 `add()`(split_long_text 적용 경로)를
  거치지 않고 독립 unit 으로 바로 나가는데, `render_table_node_fragments`
  의 block 내부 분할은 "행 1개 자체가 이미 max_tokens 를 넘는" 극단적
  케이스(표 셀 안의 긴 각주)를 더 쪼개지 않는다 — **바로 §12 "재임베딩
  중 발견한 극단적으로 큰 표 chunk"(153,345자 outlier) TODO의 근본
  원인**. `packer.pack_nodes()`의 표 분기에서 각 fragment 를
  `split_long_text()` 에 한 번 더 통과시키는 안전망을 추가해 해결 —
  Kim 의 `test_properties.py::test_leaf_chunk_size_is_actually_bounded`
  가 이 수정 전엔 실패(상한 2200자 초과 leaf 22개, 최대 36,047자)했다가
  수정 후 통과함을 확인.
- **render_table_node_fragments (semantic-block-first) 재작성**: Kim 의
  렌더링 디테일(dup_left/dup_up skip 하는 `_cell_text`, 여러 헤더 행을
  열별로 합치는 `_header_labels`, `style="kv"/"grid"`, preamble 에
  title_hint+unit_hint+period_hint 전부 포함)을 기반으로 삼되, body row
  순회는 Kim 의 순수 row/token count 방식이 아니라 우리
  `detect_semantic_blocks()` + block 단위 패킹을 그대로 유지 — SK하이닉스
  사업보고서 재현 테스트(`test_periodic_sk_hynix_repro_operating_profit_
  same_chunk`) 그대로 통과 확인. `table_style` 기본값은 `"grid"`로 보수적
  유지(Kim 실험은 kv 가 hit@5 우세라고 보고했지만, 채택은 전체 재청킹/
  재임베딩을 요구하는 별도 실험 결정이라 이번 병합 범위 밖으로 판단).
- **테스트**: 기존 112건 + Kim `test_xml_sanitizer.py`(16건) +
  `test_properties.py`(8건, 분포/비율 수준 계약 검증 — 개별 케이스 아니라
  "청크 상한이 실제 상한인가/원문 커버리지/표 열 정렬/1열 행 보존/
  AUNITVALUE 보존/parent-child 무결성") 이식 = 총 144건(`not slow` 기준
  136 PASS + slow 8건은 HCX API 필요라 기존과 동일하게 deselect).
  `field_codes` 관련 테스트는 FieldRef 리스트 기준으로 갱신(의도된 변경).
- **수정 파일**: `parsing/xml_sanitizer.py`(신규, Kim 원본 그대로),
  `parsing/dart_xml_parser.py`, `parsing/table_parser.py`,
  `common/doc_tree.py`, `chunking/chunk_schema.py`, `chunking/packer.py`,
  `chunking/chunkers.py`(ChunkConfig dataclass 도입, `pipeline.py`는 3-
  positional-arg 호출이라 하위호환 유지), `tests/test_parsers.py`,
  `tests/test_chunkers.py`, `tests/test_xml_sanitizer.py`(신규),
  `tests/test_properties.py`(신규). `agent/tools.py`는 **변경 없음** —
  이미 우리 sibling expansion 로직이 Kim 에 없는 것까지 갖고 있었음(Kim
  쪽엔 대신 미배선 dead code인 `TOP_K_BY_ROUTE`가 있었으나 파싱/표 정합성
  범위 밖이라 이식 안 함).
- **다음 단계(실행 안 함, §12 참고)**: 이번 병합으로 chunking 로직이
  바뀌었으므로 전체 코퍼스 재청킹(`chunks_v2/`) + 재임베딩
  (`gpu_embeddings_v2/`)이 필요하다 — 단, 이 세션 도중 이미 돌고 있던
  이전 버전(어젯밤 semantic block 커밋 b112925 기준) 재임베딩 프로세스
  (`embed_full_corpus_mps.py`)가 **세션 중간에 이유 불명으로 종료됨을
  발견**(더 이상 `ps aux`에 없고 `gpu_embeddings_v2/` 산출물도 디스크에
  없음 — 이 세션에서 그 프로세스를 죽이거나 건드린 적 없음, 첫 확인 시점
  에는 PID 5320 으로 정상 실행 중이었음). 사용자가 재확인 필요.

### 100문항 일반화 배치 트랙 (2026-08-19, §5-0 참고)

- **[수정됨, 회귀테스트 추가]** Validator 오탐 — evidence 원문의 "(2023.12)"
  같은 "YYYY.MM" 날짜 표기가 `_extract_numbers()`의 정규식에 의해 "2023.12"
  한 토큰으로 통째로 추출되는 바람에, 답변이 같은 연도를 점 없이 따로 쓰면
  ("2023년 3월 12일") "근거 없는 숫자"로 오탐(correction_analysis 10건 +
  single_lookup 2건, 총 12건). `get_correction_history` 실측 데이터로 재현
  확인 후 `validator.py`의 `_extract_numbers()`에 소수점 하위토큰 등록 로직
  추가로 수정. `tests/test_agent.py::test_validator_does_not_flag_year_
  that_is_literal_substring_of_dated_evidence` 추가.
- **[신규 발견, 미수정, 최우선 후속조사]** "OO의 2025년 매출액/영업이익/
  부채비율"류 질문 11건이 "확인할 수 없습니다"라고 답했는데, 원문 대조 결과
  전부 FY2025 사업보고서가 코퍼스에 이미 존재하고 원하는 숫자도 원문에 명확히
  있었다(기아/현대자동차/SK하이닉스/셀트리온/삼성SDI/현대건설 등). grounded/
  citation 자동 지표로는 전혀 안 걸린다(숫자 없는 "정보없음" 답변이라 검산할
  게 없고 "근거:" 문구는 있어 citation도 통과). `search_disclosures`의
  period 필터 완화 재시도로는 설명 안 되는 retrieval relevance 문제로
  추정되나 근본 원인 미확정 — HCX 재호출 없이는 tool-call 트레이스 재현이
  안 돼 이번 세션에서 못 끝냄. 상세: `results/generalization_check/
  100q_batch/summary.md` §4-D.
- **[완료 2026-08-29]** ~~`_verify_derived_number()`의 O(n²) 안전장치
  (`_MAX_VERIFY_NUMBERS=200`)가 evidence 숫자가 많은(재무제표 등) 청크에서
  검산 자체를 건너뛰어 정확한 계산도 ungrounded로 남을 수 있음~~(알테오젠
  당기/전기 영업이익 뺄셈 사례로 확인). O(n log n)으로 재작성해서 해소.
  §5-I 참고.
- **[재확인, 새 회사에서 재현]** 다중행 표 요약 시 답변 모델이 숫자를
  잘못 읽는 10배 단위 오류가 알테오젠 외 **레인보우로보틱스에서도 재현**
  (투자금액 28,178백만원을 281,784백만원으로 10배 부풀림, 정정본도 동일
  오류) — validator가 정확히 잡아냄(오탐 아님). 회사를 넓혀도 사라지지
  않는 근본적 취약점임을 재확인.
- **[재확인]** `has_citation` 판정의 `"근거" in answer` OR 폴백이 매우
  느슨해서, evidence가 하나라도 있으면 답변에 "근거"라는 글자만 있어도
  report_id 실제 일치 여부와 무관하게 citation=True로 통과시킨다 — citation
  =True 92.6%는 "인용 형식을 갖췄는가"에 가깝고 "인용이 실제로 정확한가"의
  보증은 약함. citation=False 7건은 전부 "근거:" 문구 자체가 아예 빠진
  경우였음(형식 누락, 오탐 아님).
- **[진단 정정]** API 에러 5건 중 4건은 ConnectionError/ReadTimeout(네트워크
  계열)이지만 크래프톤 1건은 `HCXError 400 Bad Request`로 성격이 다름 —
  "전부 커넥션에러/타임아웃"이라던 이전 진단은 부정확했음.

### 이전 발견/수정 (Phase 개발~Stage 1~14)
- HCX-007 `thinking`/`maxCompletionTokens` 파라미터 자동분기 (`hcx_client.py`)
- CrossEncoderReranker `max_length` 미지정 시 outlier chunk에서 처리시간 폭증
- `LIBRARY` wrapper skip으로 holding SECTION 유실, SECTION 밖 loose content
  유실 → synthetic section 보존
- colspan 확장 시 텍스트 3배 반복(RLE dedup 누락)
- 표 분할이 행 수만 기준이라 컬럼 많은 표에서 chunk 폭주
- TR 12,184개 중 11,786개가 표 하나에 몰린 malformed XML 오동작(§9 참고,
  이번에 근본원인 규명됨) → TR 500행 cap 으로 방어(유지)
- Retrieval 인덱스에 parent chunk 혼입 → 임베딩 30분+ 폭주
- `search_disclosures` period 필터 완화 재시도 로직
- `report_name_contains` 구분자("ㆍ") 불일치 매칭 실패

---

## 11. 남은 TODO

**Stage 1~14 전부 완료.** 남은 건 §12의 "다음에 바로 해야 할 작업" 참고.

---

## 12. 다음에 바로 해야 할 작업

**파싱 버그 재검증 체크리스트(1~7)는 전부 완료됨** (§5 참고). 다음은
사용자가 명시적으로 요청할 경우의 후보 목록.

### [완료 2026-08-27] ~~Kim 브랜치 병합 후 전체 코퍼스 재청킹/재임베딩~~
Kim이 전체 70개사(626,497 leaf chunk)를 재청킹+재임베딩 완료해서 전달
(`임베딩결과_v2_20260827.zip`), 우리 쪽에서 100문항 배치로 전체 규모
재검증까지 완료(§5-E). **다음 최우선은 재청킹/재임베딩이 아니라, §5-E에서
새로 드러난 문제들이다** (아래).

### [최우선, 실행 필요] §5-E 전체 규모 재검증에서 발견된 문제 5건 (2026-08-27)
우선순위 순:
1. **[완료 2026-08-29]** ~~CascadingRouter의 HCX escalation에서 간헐적
   400 "Unsupported function" 에러~~ — 원인 조사 결과 **새 버그가 아니라
   기존에 이미 `hcx_client.py` docstring에 문서화돼 있던 RPM(분당
   요청수) rate-limit 현상**이 처음 실제 영향을 준 것으로 확인됨. v1은
   라우팅에 HCX를 아예 안 썼는데(SemanticRouterAdapter 단독),
   CascadingRouter 배선(§12, 2026-08-25) 이후 100문항 중 **60건**에서
   `route_score=None`(=HCX escalation 경유, `results.json` 직접 집계로
   확인)이 추가로 발생 — 이 추가 호출량이 누적 요청 빈도를 밀어올렸다.
   트레이스백이 매번 `agent_loop.py`의 `router.route()` 호출 지점이고
   재시도로 종종 성공하는 걸로 보아 어제 추가한 tool description(route별
   구별 기준)은 원인이 아님을 재확인. **수정**: `hcx_client.py`에
   `min_interval_sec`(기본 1.0초) 기반 pacing 추가 — 모듈 레벨 타이머를
   써서 이 프로세스 안의 *모든* `HCXClient` 인스턴스(agent_client/
   answer_client처럼 모델이 달라도)가 하나의 최소 호출 간격을 공유한다
   (RPM은 계정/API 키 단위지 인스턴스 단위가 아니므로). 테스트 3건 추가
   (`tests/test_hcx_client.py`, pacing 강제/인스턴스간 공유/0으로 비활성화
   확인). **정확한 계정 RPM을 몰라서 1초는 보수적 추정치** — 다시 400이
   재발하면 값을 올리거나, 반대로 배치가 너무 느려지면 실측하며 낮출 것.
2. **[완료 2026-08-29]** ~~연결/별도 재무제표 혼동~~(SK하이닉스 사례로
   발견) — `ANSWER_SYSTEM_PROMPT`에 원칙 8번 추가(§5-H 참고). validator
   레벨 검증(grounded=True로 통과하던 것 포함)까지는 손 안 댐 — 프롬프트
   원칙만으로 해소되는지는 실사용 재관찰 필요.
3. **10배 단위환산 자기모순 재발**(아모레퍼시픽, 알테오젠/레인보우로보틱스
   와 동일 계열) — 회사를 바꿔가며 계속 나타나는 구조적 문제. 답변모델이
   같은 문장 안에서 정답과 10배 축소값을 동시 제시하는 패턴 자체를
   조사할 가치 있음(어느 단계에서 자릿수가 밀리는지).
4. **[완료 2026-08-29]** ~~report_id 인용 형식 손상~~(한미반도체) —
   `has_citation`의 `"근거" in answer` 느슨한 폴백 제거 + report_id 실제
   일치 요구로 승격, "인용 손상" vs "인용 누락" 경고 구분 추가(§5-H 참고).
5. **잔여 검색 실패 2~3건**(셀트리온/현대건설 등, 원문에 답이 명시적으로
   있는데도 "확인 불가") — 11건 중 9건은 개선됐지만 완전 해결 아님, 근본
   원인 추가 조사 필요.
6. **[완료 2026-08-29]** ~~`_verify_derived_number()`가 음수(손실) evidence
   부호를 처리 못 해 정확한 산술도 ungrounded로 남는 문제~~(알테오젠) —
   O(n²) cap도 함께 O(n log n)으로 재작성. §5-I 참고.
7. **[신규, 2026-08-29]** `HCXClient.chat()`의 6회 재시도(최대 96초
   backoff)로도 못 뚫는 400("Unsupported function") 사례 실측(§5-F-1,
   margin_threshold 재조정 중 33건 HCX 호출 중 1건). 같은 질의를 몇 분 뒤
   재시도하면 즉시 성공해서 질의 문구 문제는 아니고, 확률적/일시적 API
   현상으로 추정(§12 기존 RPM pacing 관찰과 같은 계열이지만 이번엔 기존
   재시도 상한을 실제로 넘는 사례). 재시도 횟수 상향 또는 최대 backoff
   시간 연장 검토 후보 — 이번 작업 범위에서는 관찰만 하고 코드는 안 고침.

### 후보 (우선순위순 아님, 사용자 요청 시 진행)
- **[완료 2026-08-18] ~~routes.py의 calculation/event_analysis ↔
  single_lookup 경계 재정리~~** — utterance 19개 추가로 semantic 단독
  0.818→0.836, CascadingRouter 0.796→0.889 개선 확인(§5-A).
- **[완료 2026-08-25] ~~`hcx_router.py`의 `classify_route` tool schema에
  route별 짧은 description 추가~~** — 6개 route 전부에 구별 기준 추가
  (예: "calculation: 증가율/CAGR/비율처럼 문서에 없는 값을 연산해야 나옴
  (단순 조회면 single_lookup)"). 실제 HCX API 라이브 호출 4건으로 검증:
  "SK하이닉스가 최근 체결한 계약이 정정된 적 있어?"처럼 "정정"이라는
  단어가 있어도 계약 이벤트 질문이면 `event_analysis`로 정확히 분류됨
  (§10에 기록된 "'정정' 단어 과민반응" 문제가 라우터 레벨에서는 재현
  안 됨 확인). tool schema `description` 필드는 system prompt 300자
  제약(§9)과 별개 필드라 안전하게 늘려도 tool-calling이 안 깨짐을 라이브
  호출로 확인(400 에러 0건, 4/4 정상 분류).
- **[완료 2026-08-25] ~~CascadingRouter/HCXStructuredRouter를 `ask.py`
  진입점에 실제 배선~~** — `router/hcx_router.py`에 `build_cascading_router
  (embed_provider, hcx_client)` 팩토리 함수 추가(단일 조립 진입점, 매번
  스텁 새로 짜는 관행 종료). `results/generalization_check/100q_batch/
  assemble_pipeline.py`가 이제 이걸로 조립(기존엔 SemanticRouterAdapter
  절대 threshold=0.5만 썼음 — 즉 100문항 배치조차 CascadingRouter가
  아니었다). `ask()` 자체는 원래도 `router: Router | None` protocol
  파라미터라 코드 변경 불필요했음 — 문제는 항상 "조립하는 코드가
  없었다"는 것.
- **[완료 2026-08-25, Kim 브랜치 병합]** ~~3번째 malformation 패턴(속성값 안
  따옴표) 수정 검토~~ — `xml_sanitizer.py` 로 해결. §10 "Kim 브랜치 병합"
  절 참고.
- 복합추론_Open retrieval breadth 개선(top_k 확대, 하위키워드 분할검색,
  전용 tool 등)
- Router 파인튜닝 데이터 1,200건까지 확대(현재 149건, `results/router_tuning/`)
  — CLOVA Studio 콘솔에서 라벨 형식(언더스코어 포함 route 이름 허용여부),
  분류 태스크 권장 모델(HCX-DASH-001?) 재확인 먼저 필요.
- **[완료 2026-08-18, 100문항 배치에서 실사용 확인]** ~~`ask.py`에 answer
  전용 모델(HCX-005) 분리 적용~~ — `ask()`가 `answer_client` 파라미터를
  이미 지원하고(agent=HCX-007, answer=HCX-005), 100문항 배치도 이 설정으로
  실행됨(`results/generalization_check/100q_batch/assemble_pipeline.py`
  확인).
- **`fusion.py`에 `normalized_weighted_fusion()` 실제 구현 + 배선** — §10
  "2026-08-25 발견" 참고. §8의 Stage 4 결과(R@10=0.903)를 실제로 재현하는
  함수가 코드에 없다. 구현 후 RRF와 A/B 비교 필요(지금 RRF 성능이 얼마나
  떨어지는지도 아직 실측 안 됨 — 위 표 수치는 원래 다른 함수를 가정하고
  측정된 것일 수 있어 그대로 못 믿는다).
- char_2gram BM25 토크나이저 재검토
- **[완료 2026-08-27]** ~~`query_normalizer.py`에 `[YEAR]` placeholder
  추가~~ (2026-08-25 최초 발견, 2026-08-26 Entity Extraction 확장 §5-C
  작업 중 범위 밖으로 미뤄뒀다가 이번에 처리) — `entity_extractor.py`에
  `period_spans`(company_spans 와 동일 패턴, annual/year_month 매칭만
  대상 — quarter/half/recent_n_year 는 4자리 연도를 안 담아 과적합 위험이
  적고 routes.py 도 "[YEAR] 반기보고서"처럼 리터럴로 두므로 제외) 추가.
  `normalize_query()`를 company_spans+period_spans 를 하나로 합쳐 정렬 후
  한 번에 치환하도록 재작성(따로 두 번 치환하면 두 번째 시점에 좌표가
  어긋남). 같은 연도 재언급은 회사명과 동일하게 같은 `[YEAR_N]` 번호
  재사용. **효과 실측**: "[COMPANY]의 2025년 영업이익은 얼마야?"(수정
  전, 연도 리터럴)와 "[COMPANY]의 [YEAR] 영업이익은 얼마야?"(수정 후)
  각각을 routes.py 학습 문장 "[COMPANY]의 [YEAR] 매출액은 얼마야?"와
  BGE-M3 코사인 유사도 비교 — **0.821 → 0.942로 상승**, 실제로 semantic
  router 매칭이 더 정확해질 근거 확인. 테스트 5건 추가(tests/
  test_entity_extraction.py), 기존 `test_query_normalize_single_company`
  기대값 갱신(의도된 동작 변경). 전체 154 passed.
- **[완료 2026-08-27]** ~~`ANSWER_SYSTEM_PROMPT`에 "이벤트 발생일과 그
  이벤트가 참조하는 원본 계약/공시의 체결일·제출일이 다를 수 있으니
  혼동하지 말라"는 원칙 추가~~ — 원칙 7번으로 추가(`answer_generator.py`).
  Q5(§5-D)로 재검증: 수정 전 "네, 존재합니다"(날짜 혼동으로 확신에 찬
  오답) → 수정 후 "제공된 근거로는 확인할 수 없습니다"(날짜를 지어내거나
  혼동하지 않고 정직하게 모른다고 답함). **완전한 정답("2025년 체결
  계약 중 해지된 건 없음")까지는 아직 아님** — 재확인해보니 Agent가
  `search_disclosures`에 `report_type="periodic"`만 넘겨서 검색했는데,
  계약 체결/해지 이벤트는 실제로 major/exchange 공시에 있다 — report_type
  필터를 event_analysis route에서는 비우거나 major/exchange 우선으로
  가이드하는 게 다음 후보(§9-0 이 항목으로 재기록).
- **[완료 2026-08-27]** ~~`comparison_axis="period"` 신호를
  `_route_hint_message`의 "각 기간을 따로 조회하라" 지시와 연결~~ —
  기존엔 `period_comparison`(당기 대비 전기류 명시적 문구)에만 이 지시가
  붙어서 "2023년 사업보고서와 2025년 사업보고서를 비교"처럼 기간을 그냥
  나열한 질문은 지시를 못 받았다. `comparison_axis == "period"`면 항상
  뜨도록 조건 확장(`agent_loop.py`). Q6로 재검증: 수정 전 검색 1회
  ("핵심 사업", top_k=1) → 수정 후 **2023-12/2025-12 기간을 각각 분리해서
  검색**(top_k=10씩) — 의도한 행동 변화 정확히 확인됨. **단, "핵심 사업"
  이라는 검색어 자체가 여전히 일반적이라 진짜 정답(DX/DS 부문 매출비중
  변화)까지는 못 찾음** — hallucination 없이 "직접 비교할 수 없다"고
  정직하게 한계는 인정함(Harman/Sound United 인수 등 실제 근거는 인용).
  **알려진 부작용**: 이 확장은 §12의 기존 알려진 한계("2026년 1분기"
  처럼 한 기간이 연도+분기 두 조각으로 매칭돼 `comparison_axis`가
  오탐하는 케이스, 아래 항목)에도 지시가 얹히므로, 그런 케이스에서
  불필요하게 "두 기간으로 나눠 검색"을 유도할 수 있다 — 이번 6문항
  재현에서는 무해했음(Q2는 이 수정 이전에도 정확했음).
- `AGENT_SYSTEM_PROMPT`에 "비교·복합 질문은 검색어를 구체적으로 쪼개고
  top_k를 넉넉히(10 이상) 잡으라"는 문구 추가(248자, 300자 제약 안에서
  live HCX 호출로 tool-calling 안 깨짐 확인). Q5/Q6 재검증에서 실제로
  top_k=10 사용 확인됨(수정 전엔 Q6이 top_k=1).
- **[완료 2026-08-29]** ~~`comparison_axis` 계산에서 "period 매칭 2개
  이상 = period 비교"로 보는 휴리스틱이 "2026년 1분기"(한 기간을 연도+
  분기 두 조각으로 표현)와 "2023년과 2025년"(진짜 두 기간 비교)을 구분
  못 하는 문제~~ — `len(period_matches) >= 2`를 `period_spans`(연도를
  담은 매칭만 모아둔 것, `_YEAR_BEARING_TYPES`)의 **서로 다른 연도 dedup
  key 개수 >= 2**로 교체(`entity_extractor.py`). "2026년 1분기"는
  `period_spans`에 "2026" 하나만 들어있어(quarter는 non-year-bearing) 자동
  해소. 회귀 테스트 2건 추가(`tests/test_entity_extraction.py`:
  `test_comparison_axis_none_for_year_plus_quarter_single_period`,
  `test_comparison_axis_period_for_single_company_two_distinct_years`),
  기존 comparison_axis 테스트 전부 무변경 통과. 전체 스위트 166 passed.
- **[부분 완료, 잔여 이슈로 재기록]** 복합문서추론 Open 질문에서 Agent가
  지나치게 일반적인 검색어를 쓰는 문제 — top_k/기간분리는 위에서
  고쳤으나, **검색어 자체를 더 구체적인 하위 키워드로 쪼개는 것**은
  아직 미해결(§5-D Q6, "핵심 사업" -&gt; "사업부문별 매출 비중" 같은
  구체화가 안 됨). §11/기존 "복합추론_Open retrieval breadth 개선"
  후보와 같은 계열 — search_disclosures tool description에 "일반적인
  키워드보다 문서에 실제 나올 법한 구체적 섹션명/문구를 쓰라"는 가이드
  보강이나, 하위쿼리 분할(sub-query decomposition) 도입이 다음 후보.
- Stage 14 test set 표본 확대(n=10 한계 보완)
- TOC 버그가 Stage 1~14 지표에 준 영향 재검증(선택적, 비용 큼)
- `test_dense_retriever.py`의 brittle assertion 완화(§10 참고, 급하지 않음)

### 재임베딩(로컬 MPS) 중 발견 — 극단적으로 큰 표 chunk (2026-08-24 발견, 2026-08-25 Kim 브랜치 병합으로 수정)
- **[2026-08-25 수정됨]** 아래 원인 분석까지는 2026-08-24 그대로이지만,
  Kim 브랜치 병합 중 `packer.pack_nodes()`의 표 분기에 `split_long_text()`
  안전망을 추가해 해결했다(§10 "Kim 브랜치 병합" 절 참고) — "다음에 제대로
  고치려면" 아래 제안과 사실상 동일한 방향(문단/문장/어절 단위 재분할)을
  Kim 의 범용 재귀 분할기로 구현. `test_properties.py::test_leaf_chunk_
  size_is_actually_bounded` 로 상한 준수를 회귀 테스트로 고정(수정 전
  36,047자 outlier 22개 실측 → 수정 후 0개). 원문 발견 경위는 아래 그대로 보존.
- **[2026-08-24 원 발견 내용]** 전체 코퍼스 재임베딩(§7 참고) 첫
  시도에서 MPS `Insufficient Memory (kIOGPUCommandBufferCallbackErrorOut
  OfMemory)` 크래시. 원인: 표 셀 하나에 긴 각주성 텍스트가 통째로 들어간
  chunk 가 소수 존재(최대 153,345자 — 신한지주 `periodic_20260318000826`
  "관계기업투자주식 현황" 각주, POSCO홀딩스/미래에셋증권/NAVER 등에서도
  유사 사례, 텍스트 4,000자 초과 chunk 430,925개 중 7,426개=1.7%).
  **이번 표 semantic block chunking 수정 때문에 생긴 게 아님** — 옛
  `gpu_embeddings/` 샤드(예전 로직)에도 이미 동일 크기의 outlier가 있음을
  확인(60,000건 샘플 중 >10,000자 48건, 최대 30,013자). 원인은 표의 한
  행(row) 안 셀(cell) 자체가 원래 매우 크다는 것 — `render_table_node`
  계열은 옛 로직/새 로직 둘 다 row 단위로만 나눠서 한 행 안의 셀 내용
  자체를 더 쪼갤 수 없다(§Phase6 KeyValueNode 긴 value 케이스와 같은
  성격의 문제, TableNode 판에서도 존재).
  **임시 대응(임베딩 스크립트에만 적용, chunking 로직은 안 건드림)**:
  `scripts/embed_full_corpus_mps.py`가 임베딩 입력만 6,000자로 clip하고
  (저장되는 ChunkSchema.text/raw_text 원본은 그대로), batch_size=8로
  낮추고, 그래도 실패하면 배치를 절반씩 재귀 분할 재시도, 개별 chunk가
  끝까지 실패하면 zero-vector로 대체 후 `failed_chunk_ids.jsonl`에 기록.
  **다음에 제대로 고치려면**: `render_table_node_fragments`가 "행 단위"
  가 아니라 "셀 내용이 비정상적으로 큰 경우 그 셀 자체를 문단 단위로
  재분할"하는 로직을 추가로 넣어야 한다(TableCell.text 안에 이미 "\n\n"
  구분자가 남아있는 경우가 많아 문단 분리가 가능해 보임) — 범위가 커서
  이번엔 손대지 않음.

### 표 semantic block chunking 후속 TODO (2026-08-24)
- **KeyValueNode 긴 value semantic subdivision** — exchange_20241220800005
  의 "2. 주요내용" 처럼 KeyValueNode 하나의 value 문자열 안에 "1. 투자
  목적/2. 투자 금액/3. 투자 기간/4. 투자 방법" 같은 여러 의미 항목이
  통째로 이어붙어 있는 경우가 있음(약 300토큰). 이번 작업은 TableNode
  경로만 손댔고 KeyValueNode 내부는 그대로 — 정보 손실은 없음(characterization
  테스트로 고정, `tests/test_chunkers.py::test_keyvalue_node_long_value_
  characterization`)이지만, 개별 항목 단위 검색(예: "투자 기간만" 질의)이
  필요해지면 이 value 를 detect_semantic_blocks 와 유사한 규칙으로
  쪼개는 작업이 필요.
- **metric_hint 기반 sibling score boost 미구현** — `tools.py`의
  sibling expansion 은 evidence 후보 "추가"만 하고 BM25/Dense/Fusion
  스코어링 로직 자체는 건드리지 않았다(score=None 으로 추가됨, 최종 응답
  순서/가중치에 영향 없음). Fusion 단계에서 "직접 검색된 chunk" > "metric_
  hint 일치 sibling" > "단순 prev/next sibling" 우선순위를 실제 점수에도
  반영하려면 `fusion.py`/`hybrid_retriever.py`를 건드려야 하는데, 이번
  작업 범위(§12 원칙: scoring 로직 불변)에서 의도적으로 제외함.
- **표가 여러 chunk 로 나뉠 때 buffer 병합 케이스의 table_id 병합 단순화**
  — `packer.py`에서 여러 개의 서로 다른 작은 표가 우연히 같은 병합 buffer
  에 섞이면 `table_id`/`semantic_groups`가 "마지막 표 기준으로 덮어쓰기"
  된다(주석에 명시). 실제로는 이 경우 표들이 전부 `table_chunk_count=1`
  (형제 없음)이라 sibling expansion 기능에 영향은 없지만, metadata 정확성
  자체를 완벽히 하려면 PackedUnit 을 표 단위로 분리 추적하도록 확장 가능.

### 100문항 배치에서 새로 나온 후보 (2026-08-19, 우선순위순)
- **[최우선]** "2025년 재무지표 검색누락" 원인 규명 — 11건이 실제로 존재하는
  FY2025 데이터를 "확인할 수 없음"으로 잘못 답함(§10, summary.md §4-D).
  `search_disclosures`의 period 필터 완화 재시도로는 설명 안 되는 retrieval
  relevance 문제로 추정 — 실제 agent tool-call 트레이스(HCX 재호출 필요)로
  재현해서 top_k 확대/쿼리 재작성/전용 필터링 중 어느 게 원인인지 특정 필요.
- **[완료 2026-08-29]** ~~`validator._verify_derived_number()`의 O(n²)
  안전장치(`_MAX_VERIFY_NUMBERS=200`)를 O(n) 알고리즘으로 재작성~~ — §5-I 참고.
- **[완료 2026-08-29]** ~~`has_citation`의 `"근거" in answer` 느슨한 폴백을
  report_id/chunk_id 실제 일치 요구로 강화~~ — §5-H 참고.
- citation=False 7건처럼 답변이 "근거:" 인용 문구 자체를 빠뜨리는 경우를
  줄이기 위해 `ANSWER_SYSTEM_PROMPT`에 문구 보강 검토(재현 빈도 7/95=7.4%).
- API 타임아웃 정책 재검토 — 지금은 실패까지 45~97분 hang(5건 실측). 실사용
  환경이면 60~120초 수준으로 짧게 잡고 재시도/폴백하는 게 맞음.

### 로컬 임시 리소스 (git 미포함)
- `gpu_embeddings/`(2.8GB, 전체 70개사 467,043 chunk) — 로컬 영구 보관,
  삭제하지 말 것. 재사용법은 §7.
- `/tmp/bgem3_chunks_vectors.pkl`(삼성전자 33개 문서), `/tmp/
  extra_companies_vectors.pkl`(삼성SDI/LG엔솔/한미반도체 등), `/tmp/
  expand_sectors_vectors.pkl`(KB금융/알테오젠/HD현대중공업/현대자동차/
  현대건설/SK텔레콤) — 세션 재시작 시 사라짐, 필요시 §7 방법으로
  `gpu_embeddings/`에서 재추출.
- `~/Desktop/embedding/`, `~/Desktop/embedding.tar.gz` — GPU 서버 전달용
  번들, 이제 삭제해도 무방.
- TaskList로 진행상황 재확인 가능(Task #14~25 전부 completed).
