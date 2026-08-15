# Stage 12 — Answer HCX Model: Failure Analysis

## 핵심 관찰
Tool-calling(agent loop)은 질의당 **HCX-007 로 딱 1번만** 실행해 만든 동일
Evidence Pack 을 3개 모델에 그대로 재사용했다(그래야 "어느 근거를 받았는가"가
아니라 "그 근거로 얼마나 잘 답하는가"만 비교된다). 결과는 Stage 10(Agent 역할)
과 **다른 승자**가 나왔다 — HCX-005 가 answer generation 역할에서는
overall_pass_rate=0.750 로 1위, HCX-007(0.690)과 HCX-DASH-002(0.321)를
앞선다. **Agent 역할=HCX-007, Answer 역할=HCX-005** 로 역할별 다른 모델을
쓰는 것이 실험적으로 뒷받침된다.

| Model | Faithfulness | Citation Acc | Overall Pass | Latency | Answer Len |
|---|---|---|---|---|---|
| HCX-DASH-002 | 0.893 | 0.393 | 0.321 | 4.2s | 370자 |
| **HCX-005** | 0.821 | **0.893** | **0.750** | 6.9s | 467자 |
| HCX-007 | 0.759 | 0.897 | 0.690 | 6.2s | 504자 |

(n=28~29/30: 각 모델 1~2건 client exception(429/timeout) — 특정 질의 문제가
아니라 일시적 API 부하로 판단. `numerical_accuracy`는 evidence pack 캐시에
calculation tool 결과가 포함된 질의가 2건뿐이라(n_numerical_checks=2)
표본이 너무 작아 세 모델 모두 1.0 이라는 수치를 신뢰하기 어렵다 — 참고용.)

## 프로덕션 버그 발견 (수정 완료)
Stage 12 실행 중 HCX-007 답변 생성이 거의 전부 400("Invalid parameter:
maxTokens")으로 실패하는 것을 발견했다. 직접 API 로 재현한 결과 **HCX-007
은 파라미터 이름 자체가 다르다**("maxTokens" 대신 "maxCompletionTokens"를
써야 함, thinking 모드와는 무관) — `hcx_client.py`에 모델명 기반 자동 분기
(`self._max_tokens_param`)를 추가해 해결. Stage 10 에서는 agent loop 가
tool-calling 모드라 애초에 `max_tokens`를 안 보내서(§hcx_client.py 로직상
tools 있으면 max_tokens 무시) 이 문제가 드러나지 않았다 — Stage 12 에서
비로소 발견된, 순수 answer-generation 경로 전용 버그였다.

## HCX-DASH-002 실패 유형 (19/28)
- **citation 누락이 거의 전부**(citation_accuracy=0.393): 내용 자체는
  대체로 근거에 grounded 되어 있는데(faithfulness=0.893, 사실상 가장
  높음), `ANSWER_SYSTEM_PROMPT`가 명시한 "마지막 줄에 근거: report_id
  (chunk_id) 나열" 지시를 자주 빼먹는다. **환각이 아니라 형식 지시
  불이행**이 주 실패 원인 — 경량 모델이 형식 요구사항을 덜 안정적으로
  따른다는 Stage 10 의 관찰(system prompt 지시 무시 경향)과 일관된 패턴.

## HCX-007 실패 유형 (9/29)
- **"ungrounded 숫자" 판정 다수가 실제로는 evidence 숫자로부터 올바르게
  파생 계산한 값**(id=21,22): 예) evidence 의 영업이익/매출액 원시 숫자로
  "42.75%" 를 직접 계산해 답변에 포함 — 계산 자체는 정확하고 근거 숫자에서
  추적 가능하지만, `validate_answer()`의 문자열 완전일치 검사는 이를
  "지어낸 숫자"로 오탐한다. **이는 모델의 실제 결함이라기보다 Stage 12
  검증 지표의 엄격함 한계**로 해석해야 한다 — 다만 시스템 프롬프트가
  "계산은 calculate_* tool 을 쓰라"가 아니라 "근거에 있는 내용만 쓰라"는
  answer 단계 규칙이므로, 원칙적으로는 tool 계산값을 그대로 인용해야
  하는데 그러지 않았다는 점에서 지시 불이행이기도 하다(경계 사례).
- **날짜/기간 포맷 불일치로 인한 오탐**(id=13, '2026'): evidence 에
  "20260318" 형태로 있는데 답변은 "2026년 3월"로 표현 — Stage 8 의
  period 포맷 불일치와 같은 계열의 검증 지표 한계.
- **진짜 근거 부족으로 인한 grounded=False**(id=11): evidence 에 없는
  세부 수치를 답변에 포함한 것으로 보이는 케이스도 일부 존재 — 전부가
  지표 한계는 아님.

## HCX-005 실패 유형 (7/28, 가장 적음)
- correction_evidence_complete_rate 는 세 모델 다 0.6 으로 동일(5건 중
  3건만 원본+정정본 모두 인용) — 이는 **Evidence Pack 자체의 한계**(Stage
  11 의 agent tool-calling 단계 문제)이지 answer 모델 차이가 아니다. 세
  모델이 똑같은 값을 보인다는 사실 자체가 이 결론을 뒷받침한다.
- 나머지 실패는 citation 누락 1~2건, grounding 실패 소수 — DASH-002 만큼
  뚜렷한 단일 실패 패턴은 없고 산발적.

## 교차 모델 공통 관찰
- **correction_evidence_complete_rate=0.6 (3모델 동일)**: Answer 모델과
  무관하게 Evidence Pack 안에 원본/정정본이 둘 다 없으면 어떤 답변
  모델도 완전한 비교를 할 수 없다 — Stage 11 failure_analysis 에서 지적한
  "정정 이력만 조회하고 본문 미확인" 패턴(agent 단계 문제)이 answer 단계
  결과에 그대로 전파됨을 재확인.
- **numerical_accuracy 표본 부족(n=2)**: 이번 validation 30개 중 실제로
  agent 가 calculation tool 을 호출한 질의가 매우 적어(calculation route
  5건 중 일부만 tool 호출 성공, Stage 10 failure_analysis 참고) 검증
  표본이 부족하다 — Stage 14(test set) 또는 향후 재평가 시 calculation
  질의 비중을 늘린 gold set 보강이 필요.

## 결론 / 권고
baseline(answer 역할) = **HCX-005**. overall_pass_rate 격차(0.750 vs 0.690
vs 0.321)가 "미미한 수준"이 아니라 실질적이므로 정확도 우선으로 채택한다.
지연은 HCX-005(6.9s)가 HCX-007(6.2s)보다 약간 느리지만 그 차이는 작다.
**결과적으로 파이프라인은 역할별로 다른 모델을 쓴다**: Agent(tool-calling)
= HCX-007(Stage 10 결론), Answer(최종 답변 생성) = HCX-005(이번 결론).
이는 운영 복잡도(모델 2종 관리)를 약간 늘리지만, 각 역할에 실제로 맞는
모델을 실험적 근거로 고른 것이므로 임의 선택보다 낫다고 판단한다.
`.env`는 agent 기본값(HCX-007)을 유지하고, `answer_generator.py` 호출부에
answer 전용 모델을 별도로 지정하는 것을 다음 구현 과제로 남긴다(현재는
`ask.py`가 agent client 를 answer 생성에도 그대로 재사용하고 있어 실제
적용을 위해서는 별도 client 인스턴스 분리가 필요 — 이번 Stage 는 비교
실험까지만 수행, 프로덕션 반영은 별도 작업).
