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

---

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
`HCX_MODEL=HCX-007`(agent 기본값) — answer 전용 모델 분리와 CascadingRouter
둘 다 아직 `ask.py` 호출부에 실제로 배선되지 않음(둘 다 구현+테스트는
완료, production 진입점에서 조립하는 코드만 없음 — §12 후보).

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

---

## 10. 발견된 문제 (버그 픽스 이력)

### 최근 발견/수정 (회사 일반화 검증 트랙, 2026-08-15~16)

- **[수정됨, 검증 완료]** malformed XML(escape 안 된 `&`, `<`)이 lxml
  recover 모드를 오동작시켜 문서 대부분이 TABLE 안에 파묻혀 유실되는 버그.
  400개 샘플 중 116건(29%) 영향 확인(현대차/현대모비스/POSCO홀딩스/
  KB금융/신한지주/하나금융지주/메리츠금융지주 등). `dart_xml_parser.py`에
  `_escape_bare_special_chars()` 추가해 파싱 전 사전 치환 — 재스캔 결과
  116건→0건(같은 진단 기준). KB금융/현대자동차 실제 질의로 end-to-end
  재검증 완료(§5 참고, `matrix.csv`에 [재검증] PASS 행 추가). 회귀 테스트
  4건 `tests/test_parsers.py`에 추가.
- **[별개 이슈로 재확인, 미수정]** 3번째 malformation 패턴(속성값 안
  이스케이프 안 된 따옴표, 예: `ENG="" KB Insurance Co., Ltd ""`) —
  전체 코퍼스 재스캔(2,732건)으로 정밀 정량화: **여전히 627건(23.0%)
  영향**, 전부 periodic, 금융지주·대기업 계열의 특수관계자/종속기업
  현황 표(영문 회사명을 ENG 속성에 넣는 표)에 집중. 속성값 경계를
  정규식으로 안전하게 판별하기 어려워 여전히 미수정(잘못 고치면 정상
  데이터를 깨뜨릴 위험) — 다음에 손댈 후보면 §12 참고. 상세: `results/
  generalization_check/summary.md`.
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
- **[미수정, 확인만]** `_verify_derived_number()`의 O(n²) 안전장치
  (`_MAX_VERIFY_NUMBERS=200`)가 evidence 숫자가 많은(재무제표 등) 청크에서
  검산 자체를 건너뛰어 정확한 계산도 ungrounded로 남을 수 있음(알테오젠
  당기/전기 영업이익 뺄셈 사례로 확인 — 원문 대조 결과 세 숫자와 계산 모두
  정확했는데도 ungrounded 유지). O(n) 알고리즘으로 재작성하면 해소 가능하나
  이번 세션에서는 미수정.
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

### 후보 (우선순위순 아님, 사용자 요청 시 진행)
- **[완료 2026-08-18] ~~routes.py의 calculation/event_analysis ↔
  single_lookup 경계 재정리~~** — utterance 19개 추가로 semantic 단독
  0.818→0.836, CascadingRouter 0.796→0.889 개선 확인(§5-A). **남은 부분**:
  이 수정은 semantic_router 쪽만 개선했고 HCX 자체(escalate된 hard
  케이스에서 여전히 오답 다수 발생)는 못 고쳤다 — HCX는 routes.py를
  안 보므로, `hcx_router.py`의 `classify_route` tool schema에 route별
  짧은 description(예: "calculation: 증가율/CAGR/비율처럼 두 수치를
  비교·연산해야 나오는 값. single_lookup: 문서에 그대로 적힌 단일
  수치/사실")을 추가해서 HCX 쪽 오분류를 직접 줄이는 게 다음 후보.
- **CascadingRouter/HCXStructuredRouter를 `ask.py` 진입점에 실제 배선**
  — 구현+테스트는 완료(§5-A)됐지만 아직 어떤 production 스크립트도
  이걸로 router를 조립하지 않음(지금까지 이 프로젝트의 모든 스크립트가
  매번 새로 HCXRouter 스텁을 즉석에서 짜왔음 — 이제 그럴 필요 없음).
- **3번째 malformation 패턴(속성값 안 따옴표) 수정 검토** — 이제 실제
  실패 시그니처를 확보함(`ENG="" 회사명 ""` 형태, 종속기업/특수관계자
  표에 집중, 전체 periodic의 23%). "속성값 경계 판별이 위험하다"는 기존
  우려가 여전히 유효한지, 이 특정 패턴(빈 따옴표+공백으로 시작·끝나는
  ENG 속성값)만 좁게 타겟팅하면 안전하게 고칠 수 있는지 재검토 가치 있음.
- 복합추론_Open retrieval breadth 개선(top_k 확대, 하위키워드 분할검색,
  전용 tool 등)
- Router 파인튜닝 데이터 1,200건까지 확대(현재 149건, `results/router_tuning/`)
  — CLOVA Studio 콘솔에서 라벨 형식(언더스코어 포함 route 이름 허용여부),
  분류 태스크 권장 모델(HCX-DASH-001?) 재확인 먼저 필요.
- **[완료 2026-08-18, 100문항 배치에서 실사용 확인]** ~~`ask.py`에 answer
  전용 모델(HCX-005) 분리 적용~~ — `ask()`가 `answer_client` 파라미터를
  이미 지원하고(agent=HCX-007, answer=HCX-005), 100문항 배치도 이 설정으로
  실행됨(`results/generalization_check/100q_batch/assemble_pipeline.py`
  확인). CascadingRouter는 아직 미배선 상태 유지(위 항목 참고).
- char_2gram BM25 토크나이저 재검토
- Stage 14 test set 표본 확대(n=10 한계 보완)
- TOC 버그가 Stage 1~14 지표에 준 영향 재검증(선택적, 비용 큼)
- `test_dense_retriever.py`의 brittle assertion 완화(§10 참고, 급하지 않음)

### 100문항 배치에서 새로 나온 후보 (2026-08-19, 우선순위순)
- **[최우선]** "2025년 재무지표 검색누락" 원인 규명 — 11건이 실제로 존재하는
  FY2025 데이터를 "확인할 수 없음"으로 잘못 답함(§10, summary.md §4-D).
  `search_disclosures`의 period 필터 완화 재시도로는 설명 안 되는 retrieval
  relevance 문제로 추정 — 실제 agent tool-call 트레이스(HCX 재호출 필요)로
  재현해서 top_k 확대/쿼리 재작성/전용 필터링 중 어느 게 원인인지 특정 필요.
- `validator._verify_derived_number()`의 O(n²) 안전장치(`_MAX_VERIFY_
  NUMBERS=200`)를 O(n) 알고리즘으로 재작성 — 숫자 밀집 evidence(재무제표)에서
  정확한 계산도 검산을 건너뛰어 ungrounded로 남는 문제(알테오젠 사례) 해소.
- `has_citation`의 `"근거" in answer` 느슨한 폴백을 report_id/chunk_id 실제
  일치 요구로 강화 — 지금은 인용 형식만 있으면 내용 일치와 무관하게 통과.
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
