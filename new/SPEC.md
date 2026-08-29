# DART 공시 분석 Agent 시스템 설계 및 구현 요청

## 0. 목표

이미 수집·파싱된 DART 공시 데이터와 별도의 Company Master 데이터를 기반으로, 사용자의 자연어 질문을 분석하여 적절한 기업·산업·공시 범위와 분석 Task를 결정하고, 필요한 공시 근거를 검색하여 답변하는 **Agentic Financial Disclosure QA 시스템**을 처음부터 설계하고 구현하라.

중요한 점은 다음과 같다.

* DART API 호출이나 OpenDartReader를 이용한 공시 수집은 필요 없다.
* 공시 데이터는 이미 로컬에 존재한다고 가정한다.
* 핵심은 **질문을 바로 Vector Search에 넣지 않고 먼저 구조화한 뒤 검색하는 것**이다.
* 전체 구조는 FinAgentBench의 `Document → Passage Retrieval` 개념과 FinSAgent의 `Corpus-aware Retrieval Planning / Query Decomposition` 아이디어를 참고한다.
* 다만 기존 논문 구조를 그대로 복제하지 말고 DART 공시 데이터에 맞게 단순하고 재현성 있게 설계한다.
* Multi-Agent를 불필요하게 많이 만들지 않는다.
* 가능하면 LLM 판단과 deterministic logic을 분리한다.
* 최종 시스템은 평가와 ablation test가 가능해야 한다.

---

# 1. 전체 Architecture

전체 파이프라인을 다음 구조로 구현한다.

```text
User Query
    │
    ▼
Query Understanding
    │
    ├─ 회사/산업/peer 정보
    ├─ 기간
    ├─ metric/topic
    └─ 사용자 의도
    │
    ▼
1. Entity / Universe Resolver
    │
    │  Company Master 활용
    │
    ├─ explicit company
    ├─ sector
    ├─ industry
    └─ peer group
    │
    ▼
2. Task Router
    │
    ├─ single_lookup
    ├─ comparison
    ├─ calculation
    ├─ correction
    ├─ ownership
    └─ event
    │
    ▼
3. Evidence Router
    │
    ├─ report_type
    ├─ section
    ├─ subsection
    ├─ content_type
    └─ evidence_type
    │
    ▼
Metadata Filter
    │
    ▼
Optional Corpus-aware Query Expansion
    │
    ▼
BM25 + Dense Retrieval
    │
    ▼
Fusion
    │
    ▼
Reranker
    │
    ▼
Evidence Normalization
    │
    ▼
Route-specific Workflow
    │
    ├─ comparison
    ├─ calculation
    ├─ correction diff
    └─ etc.
    │
    ▼
Final Answer LLM
    │
    ▼
Evidence-grounded Answer
```

---

# 2. 가장 중요한 설계 원칙

다음 원칙을 반드시 지켜라.

## 원칙 1. Retrieval 전에 질문을 구조화한다.

나쁜 구조:

```text
질문
→ embedding
→ 전체 vector DB 검색
→ LLM
```

좋은 구조:

```text
질문
→ entity/period/task/evidence scope 분석
→ metadata filtering
→ 좁혀진 corpus에서 retrieval
→ reranking
→ answer
```

---

## 원칙 2. "무엇을 해야 하는가"와 "어디서 찾아야 하는가"를 분리한다.

두 종류 Router를 별도로 둔다.

### Task Router

질문이 어떤 작업인지를 결정한다.

```text
single_lookup
comparison
calculation
correction
ownership
event
```

### Evidence Router

공시의 어떤 영역에서 근거를 찾아야 하는지 결정한다.

예:

```text
사업의 내용
재무에 관한 사항
이사의 경영진단 및 분석의견
주주에 관한 사항
임원 및 직원
위험 관련 section
주요사항보고서
```

즉:

```text
Task Router = WHAT

Evidence Router = WHERE
```

---

# 3. Company Master

다음 형태의 CSV가 존재한다.

```text
corp_code
stock_code
corp_name
listed_name
corp_eng_name
market
industry
sector_no
sector
listing_date
fiscal_month
market_cap
n_periodic
n_major
n_exchange
n_holding
note
```

예:

```text
00126380,005930,삼성전자,삼성전자,SAMSUNG ELECTRONICS...,KOSPI,IT,1,반도체·전자부품,...
00164779,000660,SK하이닉스,SK하이닉스,SK hynix Inc.,KOSPI,IT,1,반도체·전자부품,...
```

Company Master는 단순 참고용이 아니라 시스템의 핵심 Entity Registry로 사용한다.

---

# 4. Entity / Universe Resolver

사용자 질문에서 다음을 결정한다.

```json
{
  "entity_scope": "explicit_companies | sector | industry | market | peer_group",
  "companies": [],
  "sector": null,
  "sector_no": null,
  "industry": null,
  "periods": [],
  "topic": null,
  "metric": null
}
```

---

## 4-1. 회사명 Resolution

다음 컬럼을 사용한다.

```text
corp_name
listed_name
stock_code
corp_eng_name
```

예:

```text
"삼성전자"
"삼전"
"005930"
"SAMSUNG ELECTRONICS"
```

등을 동일 회사로 mapping할 수 있어야 한다.

Alias dictionary도 별도로 관리한다.

예:

```python
ALIASES = {
    "삼전": "삼성전자",
    "하이닉스": "SK하이닉스",
    "현차": "현대자동차"
}
```

LLM이 회사명을 임의 추론하는 것보다 deterministic lookup을 우선한다.

---

# 5. Sector / Industry 활용

Company Master의:

```text
industry
sector
sector_no
```

를 적극 활용한다.

예:

사용자:

```text
반도체 기업들 비교해줘
```

이면:

```python
company_master[
    company_master["sector"] == "반도체·전자부품"
]
```

또는:

```python
sector_no == 1
```

로 universe를 생성한다.

---

## "주요 기업" 해석

사용자가:

```text
주요 방산기업 3곳 비교해줘
```

라고 하면 LLM이 자의적으로 기업을 고르지 않는다.

다음 deterministic rule을 사용한다.

```text
sector filter
→ market_cap descending
→ top N
```

즉:

```text
"주요" = 해당 sector 내 market_cap 상위 기업
```

로 정의한다.

---

# 6. Task Router

Task Router는 다음 6개 클래스로 제한한다.

```python
class Route(str, Enum):
    SINGLE_LOOKUP = "single_lookup"
    COMPARISON = "comparison"
    CALCULATION = "calculation"
    CORRECTION = "correction"
    OWNERSHIP = "ownership"
    EVENT = "event"
```

각 정의는 다음과 같다.

---

## single_lookup

하나의 기업 또는 하나의 주제를 조회하거나 설명하는 질문.

예:

```text
삼성전자 2024년 영업이익은?
삼성전자가 HBM을 어떤 사업으로 설명하고 있어?
현대차의 주요 위험요인은?
```

---

## comparison

기업 간 또는 기간 간 비교.

예:

```text
삼성전자와 SK하이닉스의 HBM 전략 비교
삼성전자 2023년과 2024년 반도체 사업 비교
반도체 기업들의 설비투자 전략 비교
```

---

## calculation

검색된 숫자를 이용해 명시적인 계산이 필요한 질문.

예:

```text
2023년 대비 2024년 영업이익 증가율
두 기업의 영업이익률 차이
매출 CAGR
```

계산은 LLM에게 시키지 않는다.

Python calculator를 사용한다.

---

## correction

정정공시의 원본/수정본 비교.

예:

```text
이 사업보고서에서 무엇이 정정됐어?
최초 공시와 최종 공시의 매출 차이
정정 전후 최대주주 정보 비교
```

---

## ownership

지분·최대주주·주식소유 관련 질의.

예:

```text
삼성전자 최대주주는?
국민연금 지분 관련 공시는?
최대주주 변동이 있었어?
```

---

## event

특정 사건/공시 발생 여부.

예:

```text
최근 유상증자 공시 있어?
M&A 관련 공시 찾아줘
전환사채 발행 공시 있었어?
```

---

# 7. Router 출력 Schema

Task Router는 route 이름만 반환하지 않는다.

다음 구조화 JSON을 반환하도록 한다.

```json
{
  "route": "comparison",

  "entity_scope": "explicit_companies",

  "companies": [
    {
      "corp_code": "00126380",
      "stock_code": "005930",
      "corp_name": "삼성전자"
    },
    {
      "corp_code": "00164779",
      "stock_code": "000660",
      "corp_name": "SK하이닉스"
    }
  ],

  "periods": [2024],

  "metric": null,

  "topic": "HBM 투자 전략",

  "requires_calculation": false,

  "requires_historical_versions": false
}
```

가능하면 Pydantic schema를 사용한다.

---

# 8. Evidence Router

Evidence Router의 목적은 질문에 답하기 위해 **어떤 공시 문서와 section을 우선 검색해야 하는지 결정하는 것**이다.

출력 예:

```json
{
  "report_types": [
    "사업보고서"
  ],

  "section_candidates": [
    "사업의 내용",
    "재무에 관한 사항",
    "이사의 경영진단 및 분석의견"
  ],

  "content_types": [
    "text",
    "table"
  ],

  "evidence_types": [
    "business",
    "quantitative"
  ],

  "query_concepts": [
    "HBM",
    "고대역폭메모리",
    "설비투자",
    "CAPEX",
    "증설",
    "생산능력"
  ]
}
```

---

# 9. 공시 DB Metadata

공시 chunk에는 최소 다음 metadata가 있다고 가정한다.

```json
{
  "chunk_id": "...",

  "corp_code": "...",
  "stock_code": "...",
  "corp_name": "...",

  "rcept_no": "...",

  "report_type": "...",

  "fiscal_year": 2024,

  "report_date": "...",

  "section": "...",
  "subsection": "...",

  "content_type": "text | table",

  "text": "...",

  "correction_group": "...",

  "version": 1,

  "is_latest": true
}
```

현재 실제 DB schema가 이와 다르다면 adapter layer를 만들어 대응할 수 있도록 구현한다.

---

# 10. Metadata Filtering

Retrieval 전에 반드시 metadata filter를 적용한다.

예:

질문:

```text
삼성전자 2024년 반도체 사업 위험요인 알려줘
```

먼저:

```text
corp_code = 00126380

fiscal_year = 2024

report_type = 사업보고서

is_latest = true

section IN relevant_sections
```

로 corpus를 축소한다.

그 다음에 BM25 / Dense Retrieval을 수행한다.

---

# 11. Retrieval Pipeline

다음 구조를 사용한다.

```text
Metadata Filter
        │
        ▼
┌───────────────┐
│     BM25      │
└───────┬───────┘
        │
        ├─────────┐
        │         │
        ▼         ▼
      Sparse    Dense
        │         │
        └────┬────┘
             ▼
           Fusion
             │
             ▼
          Reranker
             │
             ▼
          Top Evidence
```

Hybrid Retrieval을 기본으로 설계한다.

---

# 12. BM25

한국어 공시 문서를 고려한다.

가능하면 다음 실험이 가능하게 modular하게 구현한다.

```text
Whitespace tokenizer
Mecab/형태소 tokenizer
Nori-like tokenizer
character n-gram
```

BM25 결과는:

```python
[
    {
        "chunk_id": "...",
        "score": 11.3
    }
]
```

형태로 표준화한다.

---

# 13. Dense Retrieval

Embedding Model을 교체 가능하도록 abstraction을 만든다.

예:

```python
class EmbeddingProvider(Protocol):
    def embed_query(self, text: str) -> list[float]:
        ...
```

HCX embedding 또는 다른 공개 embedding 모델을 나중에 쉽게 비교할 수 있게 한다.

---

# 14. Fusion

최소 Reciprocal Rank Fusion을 지원한다.

```text
RRF
```

예:

```python
score = 1 / (k + rank)
```

BM25와 Dense의 결과를 하나로 합친다.

추후 weighted fusion도 테스트 가능하도록 한다.

---

# 15. Reranker

Top 20~50 candidates를 가져온 뒤 reranker를 적용한다.

출력은 Top 5~10 evidence.

Reranker model은 교체 가능하도록 interface를 만든다.

---

# 16. FinSAgent식 Corpus-aware Query Expansion

이 기능은 optional하게 구현한다.

목적:

사용자 질문의 표현과 실제 공시 문서에서 사용하는 표현이 다를 수 있기 때문에, 검색 전에 관련 section의 대표 문장/요약을 먼저 확인하여 공시 내부 terminology를 이용해 query를 확장한다.

예:

질문:

```text
삼성전자의 HBM 경쟁력은?
```

초기 query:

```text
삼성전자 HBM 경쟁력
```

관련 section preview에서:

```text
DS부문
고대역폭메모리
AI 서버
고부가가치 메모리
HBM3E
생산능력
```

등을 발견했다면 최종 query:

```text
HBM 고대역폭메모리 HBM3E AI 서버 고부가가치 메모리 생산능력
```

처럼 확장한다.

---

# 17. Query Decomposition

복잡한 질문은 하나의 검색 query로 처리하지 않는다.

예:

```text
삼성전자의 2023년 대비 2024년 반도체 사업 수익성이 왜 개선됐어?
```

다음 subquery로 분해할 수 있다.

```text
1. 2023 DS부문 매출/영업이익
2. 2024 DS부문 매출/영업이익
3. 2023 메모리 시장 상황
4. 2024 HBM/서버 수요 변화
5. 경영진이 설명한 수익성 변화 원인
```

각 subquery는 다른 evidence scope를 가질 수 있다.

---

# 18. Evidence Type

Evidence를 다음 범주로 구분할 수 있게 한다.

```text
quantitative
business
market
risk
ownership
event
management_commentary
```

질문 하나가 여러 Evidence Type을 요구할 수 있다.

예:

```text
수익성이 왜 좋아졌어?
```

이면:

```text
quantitative
+
business
+
management_commentary
```

가 필요하다.

---

# 19. single_lookup Workflow

예:

```text
삼성전자가 AI 관련 사업을 어떻게 설명하고 있어?
```

Pipeline:

```text
Entity Resolver
→ 삼성전자

Task Router
→ single_lookup

Evidence Router
→ 사업의 내용 / 경영진단

Metadata Filter
→ 삼성전자 최신 사업보고서

Query Expansion
→ AI / 생성형 AI / 데이터센터 / 클라우드 등

Hybrid Retrieval

Reranker

Evidence

Answer LLM
```

---

# 20. comparison Workflow

예:

```text
삼성전자와 SK하이닉스의 HBM 투자 전략을 비교해줘
```

다음과 같이 task를 분해한다.

```text
Task A:
삼성전자 / HBM 투자 전략

Task B:
SK하이닉스 / HBM 투자 전략
```

각각 동일 retrieval pipeline을 실행한다.

결과:

```python
{
  "Samsung": [...evidence...],
  "SKHynix": [...evidence...]
}
```

마지막에 비교한다.

비교 LLM이 새로운 사실을 추가하지 못하도록 evidence 안에서만 비교하게 한다.

---

# 21. calculation Workflow

예:

```text
삼성전자 2023년 대비 2024년 영업이익 증가율 알려줘
```

먼저 숫자 Evidence를 검색한다.

그 다음 Calculation Planner가:

```json
{
  "operation": "growth_rate",

  "inputs": [
    {
      "company": "삼성전자",
      "year": 2023,
      "metric": "영업이익"
    },
    {
      "company": "삼성전자",
      "year": 2024,
      "metric": "영업이익"
    }
  ]
}
```

를 만든다.

계산:

```python
growth_rate = (
    value_2024 - value_2023
) / abs(value_2023) * 100
```

LLM에게 arithmetic을 맡기지 않는다.

---

# 22. correction Workflow

공시에는:

```text
correction_group
version
is_latest
```

metadata가 존재한다고 가정한다.

일반 질의에서는:

```text
is_latest = true
```

만 검색한다.

정정 질의에서는:

```text
correction_group
```

전체 version을 가져온다.

예:

```text
V1 original
V2 correction
V3 correction/latest
```

전체 문서를 무식하게 diff하지 않는다.

가능하면:

```text
section matching
→ subsection matching
→ chunk/table diff
```

순서로 비교한다.

변경 유형:

```text
text_change
numeric_change
row_added
row_deleted
table_change
```

를 구조화한다.

---

# 23. ownership Workflow

질문:

```text
삼성전자 최대주주 관련 내용 알려줘
```

Evidence Router가:

```text
주주에 관한 사항
최대주주
대량보유
주식소유
```

관련 section을 우선 지정한다.

Company Master의:

```text
n_holding
```

값도 coverage validation에 활용한다.

---

# 24. event Workflow

예:

```text
최근 유상증자 관련 공시가 있었어?
```

먼저 metadata 기반으로:

```text
company
report_type
report_name
date
event keywords
```

를 검색한다.

해당 공시가 발견되면 그 문서 내부에서 필요한 section만 retrieval한다.

---

# 25. Company Master Coverage 활용

다음 컬럼:

```text
n_periodic
n_major
n_exchange
n_holding
```

을 데이터 존재 여부 확인에 활용한다.

예:

ownership 질문인데:

```text
n_holding == 0
```

이면 불필요한 retrieval을 줄일 수 있다.

---

# 26. note 컬럼

현재 note에는:

```text
원본 XML 미제공
PDF/HTML 대체 수집
사명 변경
파일명 예외
```

등이 들어갈 수 있다.

초기 구현에서는 문자열 그대로 보존한다.

추후:

```json
{
  "source_quality": "xml | html | pdf_fallback",
  "has_collection_issue": true
}
```

같은 구조화된 quality metadata로 확장 가능하게 한다.

---

# 27. Evidence Schema

모든 workflow가 최종적으로 동일한 Evidence schema를 반환하게 한다.

```python
class Evidence(BaseModel):
    evidence_id: str

    corp_code: str
    corp_name: str

    rcept_no: str | None

    report_type: str | None
    fiscal_year: int | None

    section: str | None
    subsection: str | None

    content_type: str | None

    text: str | None

    value: float | str | None

    retrieval_score: float | None

    source_quality: str | None

    is_latest: bool | None
```

---

# 28. Answer LLM

최종 LLM은 새로운 검색이나 계산을 하지 않는다.

입력은:

```text
Question
+
Structured Query
+
Evidence
+
Calculation Results
```

만 받는다.

규칙:

1. Evidence에 없는 사실을 만들지 않는다.
2. 숫자는 제공된 evidence/calculation result를 그대로 사용한다.
3. 근거가 부족하면 부족하다고 답한다.
4. 비교 질문에서는 비교 기준을 명시한다.
5. 정정 질문에서는 old/new를 구분한다.
6. 가능하면 사용한 회사/기간/공시/section을 표시한다.

---

# 29. LLM 사용 위치

LLM은 최소한으로 사용한다.

추천:

```text
Query Understanding      → LLM
Task Router              → LLM 또는 classifier
Evidence Router          → LLM
Query Decomposition      → LLM
Query Expansion          → optional LLM

Entity lookup            → deterministic
peer selection           → deterministic
metadata filtering       → deterministic
retrieval                → BM25/Dense
fusion                   → deterministic
calculation              → Python
correction diff          → Python
final answer synthesis   → LLM
```

---

# 30. 폴더 구조

다음과 유사한 구조로 설계한다.

```text
app/
│
├── main.py
│
├── core/
│   ├── config.py
│   └── logging.py
│
├── company/
│   ├── repository.py
│   ├── resolver.py
│   ├── aliases.py
│   └── peer_selector.py
│
├── query/
│   ├── parser.py
│   ├── schemas.py
│   └── decomposition.py
│
├── routing/
│   ├── task_router.py
│   ├── evidence_router.py
│   └── schemas.py
│
├── retrieval/
│   ├── metadata_filter.py
│   ├── bm25.py
│   ├── dense.py
│   ├── fusion.py
│   ├── reranker.py
│   └── retriever.py
│
├── workflows/
│   ├── lookup.py
│   ├── comparison.py
│   ├── calculation.py
│   ├── correction.py
│   ├── ownership.py
│   └── event.py
│
├── evidence/
│   ├── schemas.py
│   └── normalizer.py
│
├── answer/
│   └── generator.py
│
└── evaluation/
    ├── router_eval.py
    ├── retrieval_eval.py
    └── e2e_eval.py
```

불필요하게 복잡하면 더 단순화해도 되지만 각 계층의 책임은 분리한다.

---

# 31. Main Orchestrator

최종 orchestration은 복잡한 ReAct loop보다 다음과 같이 deterministic하게 구성한다.

```python
def answer(question: str):

    parsed = query_parser.parse(question)

    entities = entity_resolver.resolve(parsed)

    task = task_router.route(
        question=question,
        parsed=parsed,
        entities=entities
    )

    evidence_plan = evidence_router.route(
        question=question,
        parsed=parsed,
        task=task,
        entities=entities
    )

    if task.route == "single_lookup":
        evidence = lookup_workflow(...)

    elif task.route == "comparison":
        evidence = comparison_workflow(...)

    elif task.route == "calculation":
        evidence = calculation_workflow(...)

    elif task.route == "correction":
        evidence = correction_workflow(...)

    elif task.route == "ownership":
        evidence = ownership_workflow(...)

    elif task.route == "event":
        evidence = event_workflow(...)

    result = answer_generator.generate(
        question=question,
        evidence=evidence
    )

    return result
```

---

# 32. 평가 구조

시스템을 반드시 평가 가능하게 만든다.

## Router 평가

Test set에:

```text
question
gold_route
gold_company
gold_period
gold_sector
gold_sections
```

를 저장한다.

평가:

```text
Route Accuracy
Entity Accuracy
Period Accuracy
Section Recall@K
```

---

# 33. Retrieval 평가

가능하면:

```text
Recall@5
Recall@10
MRR
nDCG@10
```

를 계산한다.

---

# 34. Ablation

최소 다음 3개를 비교할 수 있게 한다.

### Baseline A

```text
Question
→ Dense Retrieval
```

### Baseline B

```text
Question
→ Metadata Router
→ Dense/BM25
```

### Proposed

```text
Question
→ Entity Resolver
→ Task Router
→ Evidence Router
→ Metadata Filter
→ Query Decomposition / Expansion
→ BM25 + Dense
→ Fusion
→ Reranker
```

이 비교로 Router의 효과를 검증할 수 있어야 한다.

---

# 35. 구현 우선순위

한 번에 모든 기능을 만들지 않는다.

## Phase 1

```text
Company Master Loader
Entity Resolver
Task Router
Evidence Router
Pydantic schemas
```

먼저 구현한다.

Mock 공시 데이터로 Router가 정상 동작하는지 확인한다.

---

## Phase 2

```text
Metadata Filter
BM25
Dense Retriever interface
Hybrid Fusion
```

구현.

---

## Phase 3

```text
single_lookup
comparison
```

먼저 end-to-end 완성.

---

## Phase 4

```text
calculation
ownership
event
```

추가.

---

## Phase 5

```text
correction
version diff
```

추가.

---

## Phase 6

```text
Corpus-aware Query Expansion
Query Decomposition
Reranker
```

추가.

---

## Phase 7

Evaluation 및 ablation.

---

# 36. 첫 구현 요청

지금 당장 모든 파일을 무작정 생성하지 말고 먼저 다음을 수행하라.

1. 위 요구사항을 분석한다.
2. 전체 Architecture를 최종적으로 정리한다.
3. 각 component의 책임을 명확히 정의한다.
4. 필요한 Python package와 기술 선택을 제안한다.
5. 실제 프로젝트 폴더 구조를 제시한다.
6. Pydantic schema를 구체적으로 설계한다.
7. `CompanyMasterRepository`
8. `EntityResolver`
9. `TaskRouter`
10. `EvidenceRouter`

이 네 component를 우선 구현한다.

그 다음 아래 테스트 질문을 넣었을 때 어떤 JSON이 나와야 하는지 unit test를 만든다.

---

# 37. Router Test Questions

### Case 1

```text
삼성전자 2024년 영업이익은?
```

기대:

```text
company = 삼성전자
period = 2024
route = single_lookup
evidence = financial
```

---

### Case 2

```text
삼성전자와 SK하이닉스의 2024년 HBM 투자 전략을 비교해줘
```

기대:

```text
companies = 삼성전자, SK하이닉스
period = 2024
route = comparison
sector = 반도체·전자부품

section_candidates =
사업의 내용
재무에 관한 사항
경영진단
```

---

### Case 3

```text
삼성전자 2023년 대비 2024년 영업이익 증가율은?
```

기대:

```text
route = calculation
periods = 2023, 2024
operation = growth_rate
metric = 영업이익
```

---

### Case 4

```text
삼성전자 사업보고서에서 정정된 내용 알려줘
```

기대:

```text
route = correction
requires_historical_versions = true
```

---

### Case 5

```text
반도체 기업들의 최근 설비투자 전략을 비교해줘
```

기대:

```text
entity_scope = sector
sector = 반도체·전자부품
route = comparison
```

회사 universe는 Company Master의 sector filter로 deterministic하게 생성.

---

### Case 6

```text
주요 방산기업 3곳의 수주 전략을 비교해줘
```

기대:

```text
entity_scope = sector
sector = 방산·항공우주
route = comparison
peer_selection = market_cap_top_n
top_n = 3
```

---

### Case 7

```text
삼성전자 최대주주 관련 내용 알려줘
```

기대:

```text
route = ownership
section_candidates = 주주/최대주주 관련 section
```

---

### Case 8

```text
현대차 최근 유상증자 공시가 있어?
```

기대:

```text
company = 현대자동차
route = event
event_type = 유상증자
```

---

# 38. Coding Style

* Python 3.11+ 기준
* Type hint 적극 사용
* Pydantic 사용
* 각 component에 interface 또는 명확한 class boundary를 둔다.
* LLM provider는 abstraction한다.
* HCX를 나중에 쉽게 연결할 수 있도록 한다.
* OpenAI 또는 특정 API에 종속되지 않게 한다.
* retrieval backend 역시 interface화한다.
* 테스트 가능한 함수형 구조를 선호한다.
* 불필요한 LangChain 의존성을 피한다.
* 단순한 기능은 직접 구현한다.
* LLM이 없어도 deterministic component 테스트가 가능해야 한다.

---

# 39. 가장 중요한 목표

이 프로젝트의 핵심은 "복잡한 Multi-Agent"가 아니다.

핵심은:

```text
질문을 이해하고
↓
대상 기업/산업을 확정하고
↓
질문의 Task를 결정하고
↓
어떤 공시 영역을 찾아야 하는지 계획하고
↓
필요한 corpus만 좁힌 후
↓
정확한 evidence를 retrieval한다.
```

는 **Agentic Retrieval Planning**이다.

따라서 시스템 설계 시 Agent 수를 늘리는 것보다:

```text
Entity Resolution
Task Routing
Evidence Routing
Metadata Filtering
Retrieval Quality
Evidence Grounding
```

을 우선적으로 최적화하라.

최종 답변 시스템보다 먼저 **Router와 Retrieval Planning을 제대로 완성하는 것**을 1차 목표로 삼아라.
