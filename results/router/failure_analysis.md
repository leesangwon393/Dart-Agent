# Stage 9 — Router: Failure Analysis

## 핵심 관찰
`hcx_structured_router`(accuracy=0.800, macro_f1=0.813)가 `semantic_router`
(accuracy=0.600, macro_f1=0.495)를 정확도에서 크게 앞선다. 다만 지연은
정반대로, semantic_router 는 평균 38.7ms(로컬 BGE-M3 인코딩만 필요)인 반면
hcx_structured_router 는 평균 4.5초(API 호출 + 페이싱 sleep(2) 포함)로
약 116배 느리다. `agent_only_no_router`(NoRouter)는 route 자체를 절대
반환하지 않도록 설계되어 있어 accuracy=0.0/fallback_rate=1.0 이 나오는 것이
정상 동작이다 — "라우터가 없으면 모든 질의가 fallback(=agent 자유 판단)으로
간다"는 걸 보여주는 대조군으로서 의미가 있다.

## Semantic Router 실패 유형 (12/30 실패)
- **calculation vs single_lookup 혼동**: "매출액 알려줘", "연구개발비 얼마야?"
  같은 단순 조회 질의를 "얼마" 라는 어휘 때문에 calculation 으로 오분류(3건).
  utterance 예시 세트에 두 route 간 어휘 중첩이 크다는 신호.
- **event_analysis 과다예측**: "계약금액", "자기주식 취득예정금액", "상장
  결정" 등 단순 조회성 질의를 이벤트로 잘못 분류(3건) — "결정","계약" 같은
  단어에 임베딩이 지나치게 민감.
- **multi_compare → ownership_analysis/event_analysis/single_lookup 오분류**
  (4건): "비교", "추이" 같은 명시적 비교 신호어보다 "지분", "보유주식" 같은
  도메인 키워드 임베딩이 더 강하게 작용해 route 선택을 왜곡.
- 전반적으로 semantic_router 는 표면적 어휘 유사도에 의존하므로, 질문
  구조(비교/계산 요구)보다 도메인 명사(지분/계약/자기주식)에 이끌리는
  경향이 뚜렷하다.

## HCX Structured Router 실패 유형 (6/30 실패)
- **correction_analysis → single_lookup**(1건): "정정공시 내용 알려줘"를
  "정정" 신호보다 "내용 알려줘"라는 조회형 어미에 이끌려 single_lookup으로
  분류.
- **multi_compare → ownership_analysis**(2건): "지분 변동 추이", "보유주식
  변동 추이" 처럼 비교 대상이 암묵적(시계열 비교)인 질의에서 "추이"라는
  명시적 비교 신호를 놓치고 지분 관련 도메인으로만 분류 — semantic_router
  에서도 동일 질의가 동일하게 틀려, **두 방식 모두 시계열형 암묵적 비교
  질의에 공통으로 취약**하다는 corpus-무관 패턴으로 보인다.
- **ownership_analysis → single_lookup**(2건): "대량보유상황보고서 내용",
  "지분보고 내용" 처럼 보고서명이 명시된 조회 질의를 지분 분석이 아닌
  단순 조회로 분류 — report_name 존재가 route 판단에 강하게 작용하는 것으로
  추정.
- **event_analysis → ownership_analysis**(1건): "자기주식 처분 결정" 질의를
  이벤트가 아닌 지분(자기주식) 도메인으로 분류.
- 6건 모두 다른 route로 오분류될 뿐, 응답 자체가 아예 실패(HCX 400 등)한
  경우는 없었다(재시도 1회로 전부 복구, `fallback_rate=0.0`).

## Latency/Resource Trade-off
| Router | Accuracy | Macro F1 | Fallback | Mean Latency |
|---|---|---|---|---|
| semantic_router | 0.600 | 0.495 | 0.000 | 38.7ms |
| hcx_structured_router | 0.800 | 0.813 | 0.000 | 4.502s |
| agent_only_no_router | 0.000 | 0.000 | 1.000 | ~0ms(no-op) |

hcx_structured_router 는 정확도가 약 20%p(accuracy), 0.32(macro F1) 더
높지만 지연이 116배 더 길고 외부 API 비용이 매 질의마다 발생한다.
semantic_router 는 완전히 로컬(BGE-M3 인코더만 필요)이라 비용이 없다.

## 결론 / 권고
baseline = **hcx_structured_router**. 이번 실험 조건에서 정확도 격차가
"미미한 수준"이 아니라 매우 크므로(accuracy +0.2, macro F1 +0.32), 사용자
지침("성능 차이가 미미한 경우에만 latency로 결정")에 따라 정확도 우위인
HCX 라우터를 채택한다. 다만 4.5초/질의는 실서비스 체감 지연에 영향을 줄
수 있으므로, 캐싱(동일 질의 반복 시)이나 route 분류 전용 경량 fine-tuned
분류기로의 추후 대체를 검토 과제로 남긴다. semantic_router 는 hcx 라우터
장애 시 폴백 경로로 유지할 가치가 있다(정확도는 낮지만 라우터 자체
장애로 인한 fallback_rate=1.0 상황보다는 낫다).
