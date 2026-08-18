# Router v2 — Cascading Router + Escape Hatch (2026-08-18)

## 계기 (사용자 피드백)
Stage 9 는 `hcx_structured_router` 를 최종 baseline 으로 채택했다(accuracy
0.800 vs semantic_router 0.600). 사용자가 이 구조를 직접 뜯어보고 지적한
문제: **HCX 라우터가 6개 route 중 반드시 하나를 강제로 고르도록 세팅돼
있어서, 애매한 질문에도 무조건 틀린 route 가 배정된다.** 사용자가 제안한
구조: semantic_router 를 먼저 시도하고(빠름), score 가 threshold 를 못
넘으면 HCX 로 escalate 하고(느리지만 정확), HCX 도 애매하면 route=None 으로
Agent 가 직접 판단하게 하자는 cascading 구조.

## 확인된 사실 (코드 감사)

1. **`RouteResult.route: str | None`** 설계 자체는 처음부터 "None=fallback"
   을 지원하도록 돼 있었다(`semantic_router_wrapper.py` 주석). 문제는 실제
   HCX 라우터 구현이 `tool_choice` 로 `classify_route` 를 강제 호출하고,
   enum 이 6개 route 뿐이라 **구조적으로 None 을 반환할 수 없었다** —
   Stage 9 결과의 `fallback_rate=0.0` 이 그 증거.
2. **`router/eval_dataset.py` 의 `AMBIGUOUS_SET`(§47, 정답이 여러 개인
   질문 4건)은 애초에 이 문제를 테스트하려고 만든 데이터셋이었는데,
   `evaluate_router()` 가 이를 전혀 참조하지 않는 죽은 코드였다** — 즉
   "라우터가 애매한 질문을 잘 처리하는가"는 Stage 9 에서 한 번도 실제로
   측정되지 않았다.
3. **Stage 9 는 `n=30` 으로 표기됐지만 `EVAL_SET` 실제 크기는 55개다.**
   n=30 이 어떤 방법으로 뽑힌 서브셋인지 기록이 남아있지 않다(git 히스토리가
   Phase 1~19 를 통째로 스쿼시한 단일 커밋이라 재구성 불가). 이번에 전체
   55개로 다시 돌려보니 **두 라우터의 accuracy 가 old 대비 반대 방향으로
   크게 달라졌다** — 아래 표.
4. **`semantic_router` 의 절대 유사도 점수는 정답/오답 구분력이 거의 없다.**
   EVAL_SET 55건 기준 top-1 score: 정답 median=0.781, 오답 median=0.804
   (오답 쪽이 오히려 더 높음). `DEFAULT_THRESHOLD=0.5` 에서는 55/55 가
   그대로 통과되고 있었다(threshold 가 사실상 아무 것도 안 걸렀다). 반면
   **top1-top2 margin** 은 뚜렷한 구분력이 있었다: margin>=0.05 부분집합
   accuracy=1.000(23/55), margin<0.05 부분집합만 진짜 헷갈리는 질문들이었다.

## 수정 (`src/disclosure_rag/router/hcx_router.py`, 신규)
- **`HCXStructuredRouter`**: 기존과 동일한 강제 tool-calling 구조를 쓰되,
  `route` enum 에 `"unclear"` 를 추가해서 "여러 유형에 걸치거나 애매함"을
  정직하게 답할 수 있게 했다 → `RouteResult(route=None)`.
- **`CascadingRouter`**: semantic_router 를 절대 threshold 대신 top1-top2
  **margin** 으로 게이팅(기본 0.05)해서 먼저 시도, margin 이 좁으면 HCX 로
  escalate.
- `router/eval.py` 에 `evaluate_router_ambiguous()` 추가해 `AMBIGUOUS_SET`
  을 실제로 평가할 수 있게 함(fallback 도 "적절한 처리"로 인정).
- 회귀 테스트 12건 추가(`tests/test_router.py`) — 전부 stub 기반, API/모델
  불필요.

## 실측 결과 (EVAL_SET 55건 — 1건은 API 일시오류로 제외, n=54)

| Router | Accuracy | Mean Latency | 비고 |
|---|---|---|---|
| semantic_router (top1, threshold=0.5) | **0.818** | 40ms | Stage 9는 이 방식을 0.600으로 보고했었음(다른 서브셋) |
| hcx_structured_router(+unclear) | 0.685 | 2.26s | Stage 9는 0.800으로 보고했었음(다른 서브셋) |
| **CascadingRouter**(margin>=0.05) | 0.796 | 1.34s | fast-path 24/55(44%), escalate 31/55(56%) |

**AMBIGUOUS_SET(§47, 4건, appropriate_rate = 정답목록에 있거나 fallback)**:
semantic 4/4, HCX(+unclear) 4/4, CascadingRouter 4/4 — 셋 다 이 4건에서는
문제없었다. 다만 HCX 는 4건 전부에서 **한 번도 "unclear" 를 선택하지
않았다** — 우연히 그럴듯한 route 를 골라서 acceptable 목록에 들어갔을
뿐, escape hatch 메커니즘 자체가 실전에서 발동된 적은 없다(4건은 너무
작은 표본이라 이걸로 "HCX가 unclear를 안 쓴다"고 일반화하긴 이르다).

## 솔직한 결론 — 예상과 다르게 나온 부분

**"HCX로 escalate하면 hard 케이스에서 더 정확해질 것"이라는 가설은 이번
측정에서 검증되지 않았다.** margin<0.05 로 escalate 된 31건만 따로 보면:

| | margin<0.05 (31건) 정확도 |
|---|---|
| semantic top1 (참고용, escalate 안 했다면) | 0.677 |
| HCX (+unclear) | 0.645 |

즉 이 "hard" 구간에서 HCX 가 semantic 보다 **오히려 약간 더 틀린다.**
HCX 오류 17건 중 12건(71%)이 **"calculation"/"event_analysis" 를
"single_lookup" 으로 오분류**하는 단일 패턴에 집중돼 있었다(예: "매출
증가율 몇 %야?" → single_lookup, 정답은 calculation). 이는 라우팅
메커니즘(semantic vs HCX vs cascading)의 문제가 아니라 **route 정의 자체가
겹친다는 taxonomy 문제**로 보인다 — "몇 %야?" 류의 질문은 실제로 문서에
비율이 그대로 적혀있으면 single_lookup 이 맞고, 계산이 필요하면
calculation 이 맞아서, 질문 문구만으로는 사람이 봐도 구분이 어렵다.
Stage 9 의 기존 failure_analysis.md 도 "calculation vs single_lookup
혼동... 어휘 중첩이 크다"고 이미 지적했었다 — 이번 재측정은 그 문제가
Stage 9 가 보고했던 것보다 훨씬 크다는 걸 보여준다(당시 표본에는 이
패턴의 질문이 적게 뽑혔던 것으로 추정).

## 그래서 무엇을 채택하나

1. **`CascadingRouter` 를 채택한다** — pure HCX 대비 정확도(+0.111)와
   지연(1.34s vs 2.26s, 41% 감소) 모두 우위이고, 무엇보다 **구조적으로
   진짜 fallback(route=None)이 가능**해졌다(사용자가 지적한 원래 문제의
   근본 해결). pure semantic 단독보다 이번 표본에서 근소하게 낮지만
   (0.796 vs 0.818), semantic 단독은 "unclear"를 낼 수 없는 것도 여전히
   맞고, out-of-distribution 질문에 대한 안전판으로 HCX escalation 경로를
   유지하는 게 가치 있다고 판단.
2. **다음 우선순위는 라우팅 메커니즘이 아니라 route 정의 정리다** — 특히
   calculation ↔ single_lookup, event_analysis ↔ single_lookup 사이 경계를
   `routes.py` utterance 세트에서 다시 그어야 한다(예: calculation 은
   "계산해줘/증가율이 몇 %인지 계산" 처럼 명시적 연산 요청으로 좁히고,
   "얼마야?/몇 %야?" 류의 단순 조회 어투는 single_lookup 쪽에 확실히
   배치). 이건 라우터 알고리즘이 아니라 **데이터(taxonomy) 문제**라
   CascadingRouter 를 아무리 잘 만들어도 못 고친다.
3. n=54~55 는 여전히 통계적으로 약한 표본이다(Stage 14 의 n=10 경고와
   같은 종류의 한계). 위 결론(특히 calculation/event_analysis 혼동
   패턴)은 방향성은 신뢰할 만하지만 정확한 수치(0.685, 0.818 등)에
   과도한 의미를 부여하지 말 것.

## 재현 방법
```python
from disclosure_rag.router.hcx_router import HCXStructuredRouter, CascadingRouter
from disclosure_rag.router.semantic_router_wrapper import build_semantic_router
from disclosure_rag.retrieval.embeddings import build_embedding_provider
from disclosure_rag.agent.hcx_client import HCXClient

provider = build_embedding_provider("bge-m3", device="cpu")
semantic = build_semantic_router(provider, threshold=0.0)  # margin 게이팅은 CascadingRouter가 직접 하므로 0으로 열어둠
hcx = HCXStructuredRouter(HCXClient(api_key=..., model="HCX-005"))
router = CascadingRouter(semantic, hcx, margin_threshold=0.05)
```
원본 raw 데이터: `semantic_margin_analysis.json`(EVAL_SET+AMBIGUOUS_SET
top1/top2/margin), `hcx_unclear_results.json`(HCX 예측+지연시간).
