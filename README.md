# 금융공시 Agentic RAG 시스템

70개사 DART 공시 코퍼스(`corpus/`) 기반 Agentic RAG. Entity Extraction → Router →
HCX Agent(Tool Calling) → Evidence Pack → HCX Answer → Validation 까지 **Phase 1~19
전체 구현 완료**, 실제 HyperCLOVA X API(.env)로 end-to-end 검증됨 (아래 "실측 검증
사례" 참고).

## 셋업

```bash
uv venv --python 3.11 .venv
uv pip install -p .venv/bin/python -e .
```

`.env` 에 HCX API 키 필요 (Agent/Answer 생성에 실제로 사용됨):

```
HCX_API_KEY=...
HCX_MODEL=...   # 예: HCX-005
```

## 실행

```bash
# Corpus 검증 (Unicode/Path resolve 성공률 리포트)
.venv/bin/python -m disclosure_rag.common.corpus_validator corpus

# 전체 코퍼스 파싱+청킹 (4,204건, ~9분)
.venv/bin/python -m disclosure_rag.pipeline corpus

# 테스트 (BGE-M3 필요한 느린 테스트 제외)
.venv/bin/python -m pytest tests/ -m "not slow"

# 전체 테스트 (BGE-M3/Kiwi 모델 로딩 포함, CPU 환경에서 수 분 소요)
.venv/bin/python -m pytest tests/
```

## 모듈 구조

```
src/disclosure_rag/
  common/
    unicode_utils.py       # NFC 정규화 + 세그먼트 단위 path resolver (raw/ 폴더명 NFD 대응)
    manifest_loader.py      # manifest.jsonl / universe.csv 로더
    corpus_validator.py     # startup validation report
    doc_tree.py              # Parser 공통 중간 표현 (SectionNode/TableNode/KeyValueNode/TextNode)
  parsing/
    dart_xml_parser.py      # periodic/major/holding 공용 (동일 DART DOCUMENT/SECTION-N 스키마)
    exchange_parser.py      # exchange 전용 (.xml 확장자지만 실제는 위장 HTML)
    table_parser.py          # rowspan/colspan grid 확장 + KeyValue vs Table 분류
    document_detector.py    # manifest.doc_group 기반 라우팅, 첨부파일(감사보고서 등) 처리
  chunking/
    chunk_schema.py          # 공통 Chunk Schema (pydantic) + 검색용 text 렌더링
    packer.py                 # Section/Paragraph 경계 우선 token-budget 패킹
    chunkers.py                # periodic/holding=Parent-Child, major/exchange=flat whole-doc 우선
  correction/
    correction_extractor.py  # "정정대상 공시서류의 최초제출일" 정규식 추출
    correction_graph_builder.py  # correction_group_id/order/is_latest 그래프 (transitive chain resolution)
    overrides.py               # 원문 오타 등 deterministic 하게 안 풀리는 known edge case
  retrieval/
    tokenizers.py             # whitespace/Kiwi/char-ngram tokenizer 추상화
    bm25_retriever.py         # BM25S + metadata filter (coarse-to-fine, overfetch+postfilter)
    embeddings.py              # EmbeddingProvider 추상화 (BGE-M3 baseline, E5/HCX 비교 슬롯)
    qdrant_store.py            # Qdrant vector store (payload = 전체 metadata)
    dense_retriever.py         # embedding + qdrant 묶은 검색 인터페이스
    fusion.py                   # RRF
    reranker.py                 # bge-reranker-v2-m3 cross-encoder (optional)
    hybrid_retriever.py        # Metadata Filter → BM25+Dense → RRF → (선택)Reranker
    metadata_filter.py         # RetrievalFilter (company/period/report_type/correction 등)
  entity/
    entity_extractor.py       # 회사(universe.csv alias)/기간/지표/공시명/정정여부 추출
    query_normalizer.py        # [COMPANY]/[COMPANY_1]/[COMPANY_2] placeholder 치환
  router/
    routes.py                   # 6개 baseline route + 대표 utterance
    encoder_adapter.py          # EmbeddingProvider -> semantic-router DenseEncoder 어댑터
    semantic_router_wrapper.py # Router 인터페이스 (교체 가능: Semantic/HCX/None)
    eval_dataset.py             # 평가셋 (학습 utterance와 분리)
    eval.py                      # Accuracy/Macro-F1/Confusion Matrix/Fallback rate, threshold sweep
  agent/
    hcx_client.py               # HCX Chat Completions 저수준 클라이언트 (tool-calling 포함)
    tools.py                     # search_disclosures/get_correction_history/get_latest_report/calculate_*
    calculation.py                # deterministic 계산 (증가율/비율/CAGR) — LLM 암산 금지
    agent_loop.py                  # Tool-calling 루프 (max_iterations, Entity/Router 힌트 주입)
    evidence.py                     # Evidence Pack Builder (citation 보존)
    answer_generator.py            # Evidence-only 최종 답변 생성
    validator.py                    # 숫자 grounding / citation / 정정 근거 완비 여부 검증
    ask.py                           # 전체 진입점: 질문 -> ... -> 답변+검증
  pipeline.py                    # manifest -> parse -> correction graph -> chunk 오케스트레이터

config/
  financial_terms.txt   # Kiwi 사용자 사전 (BM25 tokenizer 용)
  metric_terms.txt        # Entity Extraction 지표 키워드
```

## 알아둘 것 (구현 중 발견한 실측 사실)

- `corpus/raw/` 하위 법인 폴더명은 **NFD**, manifest/universe 는 **NFC**. 파일명은 숫자
  (접수번호) 기반이라 이슈 없음 — 세그먼트 단위 리졸버 하나로 100% 해결.
- exchange 는 `.xml` 확장자지만 원문이 **HTML**. 확장자로 파서를 고르면 안 됨.
- periodic/major/holding 은 동일한 DART `DOCUMENT/SECTION-N` XML 스키마 공유 — Parser
  1개로 처리(§22 원칙: "클래스 4개를 반드시 만들 필요는 없다").
- DART XML 의 `TE[ACODE]`/`TU[AUNIT]` 셀은 이미 구조화된 key-value 필드 — 버리지 않고
  chunk 의 `field_codes` 로 보존.
- 정정공시 원본 매칭: periodic 은 `(corp_name, doc_subtype, base_year, base_month)` 키만
  으로 collision 없이 100% 해결. major/exchange/holding 은 본문의 "정정대상 공시서류의
  최초제출일" 텍스트(99.9% 추출 성공)로 원본을 역탐색하되, **직전 정정본을 가리키는
  다단 체인**이 존재해 root 까지 chasing 해야 함 (periodic 과 다른 점).
- "unresolved" 정정의 대다수(exchange 95.7%, major 77.4%, holding 100%)는 원본이 코퍼스
  수집기간(2023-01-01~) 이전이라 애초에 코퍼스에 없는, 정상적인 케이스.
- **검색 인덱스에는 leaf chunk 만 넣어야 한다** — Parent(섹션 전체를 이어붙인, 길이
  제한 없는 context 확장용 chunk)를 그대로 임베딩하면 비정상적으로 느려짐(실측: CPU
  에서 30분+). `chunk_schema.filter_leaf_chunks()` / `pipeline.build_retrieval_chunks()`
  를 반드시 거칠 것.
- **HCX API: system prompt 를 길게 쓰지 말 것.** 실측으로 결정적으로 재현됨 — system
  prompt 가 길면(~400자, 6줄 bullet) tool-calling 2번째 turn부터 API 가 매번
  400("Unsupported function")을 반환했다. 같은 내용을 3줄로 줄이니 문제없이 동작함.
  원인 불명(추정: system+tools+누적 tool 결과 총합 길이 제한). 새 지침 추가 시
  반드시 실제 다중 턴(2턴 이상) tool-calling 까지 테스트해서 확인할 것
  (`tests/test_agent.py::test_agent_correction_analysis_uses_both_versions_and_two_plus_turns`
  가 이 회귀를 잡는다).
- HCX tool-calling 요청에 `tools`+`maxTokens` 를 동시에 주면 400 이 남 — tool 호출
  시에는 `maxTokens` 를 생략해야 한다.

## 실측 검증 사례 (실제 HCX API + 실제 코퍼스로 end-to-end 확인)

정정공시 분석 질문("삼성전자 단일판매공급계약체결 공시 정정 전후로 뭐가 바뀌었어?")에
Agent 가 `get_correction_history` → 원본/정정본 각각 `search_disclosures(report_id=...)`
로 3턴에 걸쳐 근거를 모은 뒤, "계약상대가 '글로벌 대형기업' → '테슬라(Tesla, Inc.)'로
정정됨" 이라고 정확히 답변 — corpus 원문 XML 의 정정사항 표와 대조해 **100% 일치** 확인.
단순 조회 질문("삼성전자 반도체 위탁생산 계약금액 얼마야?")도 22,764,764,160,000원으로
정확히 답변, Validator 통과(숫자 grounding + citation 확인).

## 미해결/추후 작업

- Phase 15~19 (HCX Agent, Tool Calling, Evidence Pack, Answer Generation, Validation)
  구현 완료 — 단, Entity Extraction/Router 결과는 현재 Agent 에게 "힌트"로만 제공되고
  최종 tool 호출 판단은 HCX 가 자유롭게 함(§37 원칙과 일치). Retrieval Plan(§50) 처럼
  route→검색전략을 더 엄격하게 강제하는 레이어는 아직 없음 — 필요하면 추가 검토.
- search_disclosures 의 recall 은 BM25(lexical)만으로는 완벽하지 않음(실측: 특정 계산형
  질문에서 관련 청크가 top-5 밖으로 밀림) — Dense/Hybrid+Reranker 를 실제 Agent 파이프라인에
  연결(현재는 Phase 8~11 컴포넌트가 준비돼 있지만 agent/tools.py 는 BM25Retriever 예시로만
  테스트됨, HybridRetriever 로 교체 가능)하면 개선 여지 있음.
- Router 평가셋이 초기 baseline 규모(§48 목표는 route당 ~50개, 현재는 route당 10~15개).
- pdf+html 대체수집 3건(KB금융/한화오션/한화에어로스페이스)은 별도 처리 필요.
- Synthetic Data Generation 은 대회 규정(외부 LLM에 코퍼스 전송 허용 여부) 확인 전까지 미실행.
- 표(TableNode) 분할은 행 수+token 예산 둘 다 고려하지만, **열이 지나치게 많은 header
  행 자체**(예: 삼성전자 특수관계자 주석처럼 자회사 수십 개가 각각 컬럼인 표)는 분할
  로직이 없어 6000+ 토큰짜리 chunk 가 소수(표본 807개 중 13개, 1.6%) 발생함 — BGE-M3
  한도(8192) 안에는 들지만 인코딩이 느려짐. 컬럼 그룹 단위 분할은 추후 개선 과제.
- 전체 코퍼스(454,425 chunks) 규모의 BGE-M3 임베딩은 이 개발 환경(CPU) 기준 비현실적으로
  느림(4개 문서/807 chunk 만으로도 CPU 20분+) — GPU 또는 배치 작업으로 별도 실행 필요.
