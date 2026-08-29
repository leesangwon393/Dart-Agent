# COMPARISON.md — new/ (Phase 1) vs 기존 시스템(src/disclosure_rag)

## 범위 한계 (반드시 먼저 읽을 것)

이 비교는 **SPEC.md §36 Phase 1 (Company Master / Entity Resolver / Task
Router / Evidence Router) 에만 국한**된다. SPEC.md §11~§34가 요구하는
Metadata Filter, BM25/Dense Retrieval, Fusion, Reranker, Route-specific
Workflow, Answer LLM, Evaluation/Ablation(§32~34) 은 `new/`에 **하나도
구현되지 않았다.** 따라서 이 문서를 "새 시스템이 기존 시스템보다 전체적으로
낫다/못하다"는 결론으로 읽으면 안 된다 — 이번 비교는 **질문 이해·라우팅
계층(Query Understanding → Entity Resolution → Task Routing → Evidence
Routing)** 하나에 대한 비교다. 기존 시스템은 이 계층 뒤에 실제 BM25+Dense
retrieval, HCX Agent tool-calling loop, 검산 로직, 100문항 배치 검증까지
전부 갖춘 **end-to-end 운영 시스템**이고, `new/`는 그 앞단 하나만 만든
**프로토타입**이다.

---

## 1. 개념적 차이

| 축 | 기존 시스템 (`entity_extractor.py` + `hcx_router.py`/`routes.py`) | `new/` Phase 1 (4개 컴포넌트) |
|---|---|---|
| 회사명 매칭 | alias map(corp_name+listed_name) + longest-match 겹침 제거, NFC 정규화 | 동일 패턴 재구현(corp_name+listed_name+corp_eng_name+stock_code) + 수동 alias(`삼전`,`하이닉스`,`현차`) |
| 기간 추출 | 정규식 기반, quarter/half/year_month/annual/recent_n_year 5종 + period_type 우선순위 + [YEAR] placeholder | 정규식 기반이지만 연도(4자리)만 처리 — quarter/half/recent_n_year 미구현 |
| Sector/Industry 활용 | **없음.** entity_extractor에 sector/industry 필드 자체가 없다 | **핵심 신규 기능.** Company Master의 sector를 "·" 분할해 질문 텍스트와 substring 매칭 → entity_scope="sector"로 전환 |
| "주요 OO기업 N곳" | **없음.** 회사명이 명시되지 않으면 companies=[] 로 끝(§37 Case 5/6에서 실측 확인, 아래 §3) | **핵심 신규 기능.** sector filter → market_cap 내림차순 → top N deterministic 규칙(§5) 그대로 구현, peer_selection/requested_top_n 필드로 출력에 노출 |
| Task Router | 6-way 강제 분류 + "unclear" escape hatch, semantic_router(BGE-M3 임베딩) margin 게이팅 + HCX escalation(CascadingRouter) | 우선순위 규칙 체인(correction→calculation→ownership→event→comparison→single_lookup) 순수 키워드/엔티티 신호 기반. 신호가 전혀 없을 때만 `RouteClassifier` Protocol(LLM 확장점, 미구현)로 위임 |
| Route 이름 | `single_lookup / correction_analysis / multi_compare / calculation / ownership_analysis / event_analysis` | `single_lookup / comparison / calculation / correction / ownership / event` (SPEC.md §6 표기 그대로) — **이름만 다르고 의미는 대응**(multi_compare≈comparison, *_analysis 접미사만 차이) |
| Evidence Router (WHERE) | **명시적으로 없음.** `event_terms.txt`/`ownership_terms.txt` substring 매칭으로 "이 질문이 event/ownership 신호를 담고 있다"는 것만 Agent에게 힌트로 전달 — "어느 report_type/section을 봐야 하는지"를 구조화된 스키마로 미리 계획하지 않는다. 실제 section 선택은 HCX Agent가 tool-calling 루프 안에서 그때그때 결정 | **핵심 신규 기능.** route별 report_type/section_candidates/content_type/evidence_type을 §8 스키마로 사전에 명시적으로 계획. query_concepts 미니 동의어 사전(HBM→고대역폭메모리 등, §16 축소판)도 포함 |
| LLM 사용 위치 | Task Router 자체가 이미 LLM(HCX)/임베딩 기반(CascadingRouter) — deterministic은 엔티티 추출뿐 | Phase 1 전체가 100% deterministic. LLM은 `RouteClassifier`/`ConceptExpander` Protocol로 확장점만 남김(§29 "우선 deterministic, 필요시 LLM") |
| 검증 이력 | 100문항 배치 실측, Stage 8/9 ablation, 실제 버그 3건 수정 이력(§9-0, [YEAR] placeholder 버그, comparison_axis 오탐 등) | 이번에 작성한 21개 unit test(§37 8개 + 자체 13개)만 통과. 실전 다양성 미검증 |

---

## 2. §37 8개 테스트 케이스 — `new/` 통과 결과

모두 PASS (21/21 unit test 통과, `pytest new/` 기준).

| # | 질문 | route | entity_scope | companies | 기타 확인 필드 | 결과 |
|---|---|---|---|---|---|---|
| 1 | 삼성전자 2024년 영업이익은? | single_lookup | explicit_companies | 삼성전자 | periods=[2024], evidence_types에 quantitative, section에 "재무에 관한 사항" | PASS |
| 2 | 삼성전자와 SK하이닉스의 2024년 HBM 투자 전략을 비교해줘 | comparison | explicit_companies | 삼성전자, SK하이닉스 | sector=반도체·전자부품(공통 sector 주석), section_candidates=[사업의 내용,재무에 관한 사항,이사의 경영진단 및 분석의견] | PASS |
| 3 | 삼성전자 2023년 대비 2024년 영업이익 증가율은? | calculation | explicit_companies | 삼성전자 | periods=[2023,2024], operation=growth_rate, metric=영업이익 | PASS |
| 4 | 삼성전자 사업보고서에서 정정된 내용 알려줘 | correction | explicit_companies | 삼성전자 | requires_historical_versions=True | PASS |
| 5 | 반도체 기업들의 최근 설비투자 전략을 비교해줘 | comparison | **sector** | 삼성전자,SK하이닉스,삼성전기,한미반도체,LG이노텍(5개 전체) | sector=반도체·전자부품 | PASS |
| 6 | 주요 방산기업 3곳의 수주 전략을 비교해줘 | comparison | **sector** | 한화에어로스페이스,현대로템,LIG디펜스앤에어로스페이스(top3) | peer_selection=market_cap_top_n, requested_top_n=3 | PASS |
| 7 | 삼성전자 최대주주 관련 내용 알려줘 | ownership | explicit_companies | 삼성전자 | section_candidates에 "주주에 관한 사항"·"최대주주 등의 주식소유현황" | PASS |
| 8 | 현대차 최근 유상증자 공시가 있어? | event | explicit_companies | 현대자동차 | event_type=유상증자 | PASS |

FAIL 없음. (구현 과정 노트: entity_resolver.py의 heuristic topic 추출기는
정규식 기반이라 완벽하지 않다 — 예: Case 3의 topic이 `"대비  영업이익
증가율"`처럼 어색하게 나온다. §37이 topic 필드를 어느 케이스에서도
명시적으로 요구하지 않아 테스트 실패로 이어지진 않았지만, 정직하게 밝혀둔다.)

---

## 3. §37 8개 테스트 케이스 — 기존 시스템 실측 결과

`new/scripts/compare_with_legacy.py` 로 실제 실행했다(추측이 아니라 실측
— `src/disclosure_rag/entity/entity_extractor.py` + `query_normalizer.py`
+ `SemanticRouterAdapter(BgeM3EmbeddingProvider, threshold=0.3)`,
`agent_loop.py`가 쓰는 것과 동일한 파이프라인: `extract → normalize_query →
router.route`. BGE-M3는 로컬 캐시로 로드해 네트워크/API 호출 없음).

| # | 질문 | entity_extractor companies | period | comparison_axis | **route (semantic router)** | score |
|---|---|---|---|---|---|---|
| 1 | 삼성전자 2024년 영업이익은? | 삼성전자 | ['2024년'] | None | **calculation** (오분류, 정답은 single_lookup) | 0.869 |
| 2 | 삼성전자와 SK하이닉스의 2024년 HBM... 비교 | 삼성전자, SK하이닉스 | ['2024년'] | company | **multi_compare** (comparison과 의미 대응, 정답) | 0.748 |
| 3 | 삼성전자 2023년 대비 2024년 영업이익 증가율은? | 삼성전자 | ['2023년','2024년'] | period | **calculation** (정답) | 0.854 |
| 4 | 삼성전자 사업보고서에서 정정된 내용 알려줘 | 삼성전자 | [] | None | **correction_analysis** (correction과 대응, 정답) | 0.851 |
| 5 | 반도체 기업들의 최근 설비투자 전략을 비교해줘 | **[]** (회사 추출 실패) | [] | None | **event_analysis** (오분류, 정답은 comparison) | 0.642 |
| 6 | 주요 방산기업 3곳의 수주 전략을 비교해줘 | **[]** (회사 추출 실패) | [] | None | **multi_compare** (route 명칭은 맞으나 companies=[] 라 실제 3개사를 전혀 못 찾음) | 0.587 |
| 7 | 삼성전자 최대주주 관련 내용 알려줘 | 삼성전자 | [] | None | **ownership_analysis** (ownership과 대응, 정답) | 0.758 |
| 8 | 현대차 최근 유상증자 공시가 있어? | 현대자동차 | [] | None | **event_analysis** (event와 대응, 정답) | 0.851 |

원본 JSON: `new/scripts/compare_output.json`.

---

## 4. 두 결과 직접 비교

- **Case 1**: `new`는 single_lookup(정답), 기존 시스템은 semantic router가
  calculation으로 오분류했다(score 0.869로 오히려 confident). "영업이익은?"
  이라는 단순 조회 문장이 calculation 학습 utterance(routes.py의
  "[COMPANY] 영업이익이 전년 대비 얼마나 늘었어?" 류)와 임베딩 공간에서
  더 가깝게 나온 것으로 보인다 — deterministic 키워드 규칙(`new`)이 이런
  임베딩 노이즈에 영향받지 않는다는 것을 보여주는 실측 사례.
- **Case 2, 4, 7, 8**: 두 시스템 모두 사실상 동일한 route에 도달했다(이름만
  다름: multi_compare≈comparison, correction_analysis≈correction,
  ownership_analysis≈ownership, event_analysis≈event). 여기서는 차이가
  없다 — 기존 시스템의 기본 라우팅 능력이 이미 이 4개 케이스에서는 충분히
  검증돼 있다는 뜻이기도 하다.
- **Case 3**: 둘 다 정답(calculation). 다만 `new`는 operation=growth_rate,
  metric=영업이익까지 구조화해서 반환하는 반면, 기존 시스템은 route
  이름만 반환하고 "무엇을 계산해야 하는지"는 이 단계에서 구조화하지
  않는다(Calculation Planner는 §21 workflow 단계 — 기존 시스템에서도
  구현돼 있을 가능성이 높지만 entity_extractor/router 레이어 산출물에는
  없다).
- **Case 5, 6 (가장 중요한 차이)**: 기존 시스템은 **sector/peer 개념이
  전혀 없어서 회사명이 명시되지 않은 두 질문 모두 companies=[] 로 끝난다.**
  Case 5는 route조차 event_analysis로 잘못 튄다. Case 6은 route 이름은
  우연히 맞았지만(multi_compare) 정작 "어느 3개 회사인지"는 전혀 알아내지
  못한다 — HCX Agent가 tool-calling 루프 안에서 이 회사들을 어떻게든
  자체적으로 찾아야 하는데, 그 근거가 없으면 LLM이 임의로 3개 회사를
  "추론"할 위험이 있다(SPEC.md §5가 정확히 경고하는 실패 모드: "LLM이
  자의적으로 기업을 고르지 않는다"). `new`는 sector filter→market_cap
  top N deterministic 규칙으로 정확히 한화에어로스페이스/현대로템/
  LIG디펜스앤에어로스페이스 3곳을 재현 가능하게 산출한다.

**요약**: 8개 중 6개(1,2,3,4,7,8)는 두 시스템의 route 판단이 사실상
동등하거나(2,3,4,7,8) `new`가 근소하게 낫고(1, deterministic이 임베딩
오분류를 피함), 나머지 2개(5,6, sector/peer 케이스)에서는 `new`가
구조적으로 우월하다 — 기존 시스템에 아예 없는 기능이기 때문이다.

---

## 5. 정직한 결론

### `new`가 설계상 나은 점
1. **Sector/Industry 기반 entity_scope과 "주요 기업 N곳" market_cap 규칙**
   — 기존 시스템에 없는 기능이며, §37 Case 5/6 실측에서 기존 시스템의
   실제 공백(companies=[])을 확인했다.
2. **Evidence Router의 명시적 구조화** — "어느 report_type/section을 볼지"를
   질문 이해 단계에서 미리 스키마로 못박는다. 기존 시스템은 이 결정을
   HCX Agent의 tool-calling 판단에 위임한다(§8 목적에 더 부합하지만,
   실제 검색 단계가 없어 이 계획이 실전에서 얼마나 유효한지는 검증 못함).
3. **100% deterministic** — LLM/임베딩 호출 없이 5ms 이내 재현 가능한
   판단. 기존 시스템은 Task Router 자체가 이미 임베딩(BGE-M3) 기반이라
   같은 질문도 threshold/margin 설정에 따라 CascadingRouter가 HCX까지
   escalate할 수 있다(레이턴시/비용 트레이드오프).

### 기존 시스템이 이미 검증한 것, `new`가 아직 못 따라가는 점
1. **실전 100문항 배치 + ablation 이력** — 기존 시스템은 Stage 8/9
   ablation, [YEAR] placeholder 버그, comparison_axis 오탐 같은 실제
   실패를 발견하고 고친 이력이 있다(`PROJECT_STATE.md`). `new`는 21개
   자체 unit test만 통과했을 뿐 이런 규모의 실전 검증이 전혀 없다.
2. **기간 표현의 폭** — 기존 시스템은 분기/반기/최근N년/연월까지
   `period_type` 우선순위 규칙으로 분류한다. `new`는 4자리 연도만
   추출한다(Phase 1 범위상 §37 케이스에 분기/반기 질문이 없어서 굳이
   만들지 않았다 — 일반화 갭으로 남는다).
3. **[YEAR]/[COMPANY] placeholder 정규화, comparison_axis, period_comparison
   ("당기 대비 전기")** — 세 가지 모두 기존 시스템이 실제 버그 수정을
   거쳐 얻은 견고한 기능인데 `new`엔 없다. `new`의 topic 추출기는 훨씬
   단순한 정규식 기반이라 §4 결론에서 밝혔듯 완벽하지 않다.
4. **CascadingRouter(semantic + HCX escalation)와 실제 HCX 연동** — `new`의
   `RouteClassifier`/`ConceptExpander` Protocol은 인터페이스만 존재하고
   실제 LLM 구현이 없다. 기존 시스템은 이미 HCX-007/HCX-005 조합까지
   ablation으로 확정해 운영 중이다.
5. **Route 이름 자체의 학습된 안정성** — 기존 시스템의 6개 route 이름과
   utterance 세트는 대회/실전 질문에서 반복 학습·수정된 결과물이다. `new`의
   6개 route 이름(§37 그대로)은 이번 스펙 문서 기준으로만 정의됐고, 아직
   대규모 실전 질문 분포로 검증되지 않았다.

**결론**: 이번 Phase 1 비교에서 "어느 게 무조건 더 낫다"는 말은 성립하지
않는다. `new`는 스펙이 강조한 신규 아이디어(sector/peer 처리, evidence
routing 명시화)를 깔끔하게 구현했고 §37 8개 케이스에서 기존 시스템의 실제
공백(Case 5/6)을 확인했다. 그러나 기존 시스템은 이 라우팅 계층 뒤에 실제
동작하는 retrieval+answer 파이프라인 전체와 그걸 뒷받침하는 실전 검증
이력을 갖추고 있고, `new`는 아직 그 어느 것도 없다.

---

## 6. §34 Ablation(Baseline A/B/Proposed) — 지금 실행 불가능한 이유와 필요한 것

SPEC §34의 ablation은 Retrieval(BM25/Dense/Fusion/Reranker)과 Workflow가
있어야 "Router가 있고 없고의 차이"를 Recall@K/nDCG 같은 지표로 잴 수 있다.
지금은 질문을 구조화하는 계층만 있고 그 구조화 결과로 실제 무언가를
검색해본 적이 없으므로 세 baseline 모두 실행 불가능하다. 필요한 최소 범위
(실행은 하지 않음, 추정치만):

- **Phase 2**: Metadata Filter + BM25 + Dense Retriever interface + RRF
  Fusion. 여기서 처음으로 "좁혀진 corpus에서 검색"이 가능해진다.
- **Phase 3**: single_lookup/comparison workflow end-to-end(§19, §20) —
  Baseline B(Metadata Router→Dense/BM25)와 Proposed를 비교하려면 최소
  이 두 route는 완성돼야 한다.
- **평가용 gold set**: §32가 요구하는 `question/gold_route/gold_company/
  gold_period/gold_sector/gold_sections`. 지금은 §37 8문항 정도 뿐이라
  Recall@K/nDCG 계산에 필요한 표본 크기(최소 수십~백 단위)에 한참 못
  미친다 — 기존 시스템이 이미 갖춘 100문항 배치를 참고해 유사 규모로
  새로 만들어야 공정한 비교가 된다(기존 100문항을 그대로 재사용하면
  기존 시스템에 유리하게 편향될 수 있다는 점도 유의).

---

## 부록: 재현 방법

```bash
cd "공시 agent"
source .venv/bin/activate

# new/ 자체 테스트 (21개, deterministic, API 불필요, <1초)
cd new && python -m pytest -q

# 기존 시스템과의 실측 비교 (BGE-M3 로컬 로드, API 불필요, 수십 초)
cd .. && python new/scripts/compare_with_legacy.py
```
