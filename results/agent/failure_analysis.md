# Stage 10 — Agent HCX Model: Failure Analysis

## 핵심 관찰
HCX-007(reasoning 모델, `thinking={"effort":"none"}`으로 tool-calling 활성화)이
정확도 3개 지표(tool_accuracy=0.966, argument_accuracy=0.980,
task_success_rate=0.793) 전부에서 압도적 1위이면서, 지연도 HCX-005보다
오히려 짧다(13.9s vs 20.3s mean latency) — reasoning 모델이라 느릴 것이라는
사전 예상과 반대되는 결과다(추정: 더 적은 재시도/재질의로 목표에 도달해
전체 라운드트립 수 자체가 줄었기 때문 — mean_iterations 2.4 vs 2.7,
mean_tool_calls 1.72 vs 2.66). HCX-DASH-002는 가장 빠르지만(9.1s)
task_success_rate=0.233 로 압도적으로 낮아 "빠르지만 쓸모없는" 후보다.

| Model | Tool Acc | Arg Acc | Task Success | Mean Latency | Mean Iter | Mean Tool Calls | Failures(/29or30) |
|---|---|---|---|---|---|---|---|
| HCX-DASH-002 | 0.567 | 1.000 | 0.233 | 9.1s | 1.8 | 0.93 | 23/30 |
| HCX-005 | 0.897 | 0.883 | 0.552 | 20.3s | 2.7 | 2.66 | 20/29 |
| HCX-007 | **0.966** | **0.980** | **0.793** | 13.9s | 2.4 | 1.72 | 7/29 |

(n=29 for HCX-005/007: 각각 client-side 예외 1건 발생 — HCX-005 는 connection
reset, HCX-007 은 40009 "Unsupported function" 400 이 재시도 4회 모두 실패한
1건. 두 경우 다 일시적 네트워크/API 오류로, 특정 질의 자체의 문제는 아니다.)

## HCX-DASH-002 실패 유형 (23/30)
- **"아무 tool 도 안 부르고 종료"가 압도적**(9건): 특히 calculation route
  3건 전부, correction_analysis/ownership_analysis 일부에서 tool 호출 없이
  바로 `no_more_tool_calls` 로 종료 — system prompt 의 지시("계산은
  calculate_* 를 쓰세요")를 따르지 않고 추측성 답변으로 넘어가려는 경향이
  뚜렷함. 경량 모델이 tool-calling 필요성 판단 자체를 못 하는 패턴.
- **엉뚱한 tool 선택**(6건): correction_analysis/multi_compare/ownership_
  analysis/event_analysis 질문에 `get_latest_report`(메타데이터 조회용)만
  불러서 끝냄 — 실제 본문 내용이 필요한데 문서 존재 여부만 확인하고
  멈춤. tool 설명을 읽고 목적을 구분하는 능력이 약함.
- **tool 은 맞게 불렀지만 근거 실패**(8건): search_disclosures 를 정상
  호출했음에도 task_success=False — query 문구/필터가 부정확해 검색
  자체가 빗나감(예: entity 힌트를 그대로 못 살림).

## HCX-005 실패 유형 (20/29)
- **argument_accuracy 저하(0.883, error 9건)**: tool 선택은 대체로
  맞지만(tool_acc=0.897) 핸들러가 예외를 던지는 잘못된 인자(예: 존재하지
  않는 report_type enum 값, 형식이 안 맞는 period)를 종종 생성 — DASH-002
  보다 더 적극적으로 tool 을 쓰는 대신 인자 품질이 떨어지는 trade-off.
- **max_iterations 도달 1건**(id=7, "해외증권시장 상장 결정 내용
  알려줘") — 6회 반복 동안 답을 못 찾고 계속 재검색만 반복, 결국
  task_success=False 로 종료. 비효율적 tool 사용의 극단 사례.
- **calculation route 는 여전히 약함**(id=22,23 tool 0건) — DASH-002 와
  동일한 실패 패턴 공유, 이 route 자체가 경량/중급 모델 공통 약점으로
  보임(HCX-007 은 동일 id 에서 성공).

## HCX-007 실패 유형 (7/29, 가장 적음)
- **tool 은 맞게 불렀는데 근거를 못 찾음**(5/7): id=6,26,27,38 등 —
  search_disclosures 호출 자체는 정상이지만 gold report 를 못 찾음.
  이는 모델의 tool-calling 판단력 문제가 아니라 **retrieval 단(Stage
  1~5 baseline)의 한계**로 보이며, id=26/27(ownership_analysis)은
  DASH-002/HCX-005 에서도 공통으로 실패해(§ 아래 교차분석) 특정 모델
  탓이 아닌 corpus/retrieval 난이도로 해석해야 한다.
- **정정 이력만 조회하고 본문 미확인**(id=13): `get_correction_history`
  만 부르고 실제 본문(search_disclosures)까지 안 감 — DASH-002 에서
  더 흔했던 패턴이 HCX-007 에도 드물게 남아 있음.
- **일시적 400/429 오류**: 6회 관측(재시도로 5회 복구, 1회는 재시도
  소진 후 최종 실패) — HCX-007 이 다른 두 모델보다 API 오류를 더 자주
  겪었는데(로그 관찰), thinking 파라미터를 켜고 쓰는 모델 특성상
  요청당 처리 비용이 더 커 rate-limit 에 더 민감할 가능성.

## 교차 모델 공통 실패 (모델 무관 — retrieval/query 자체의 난이도)
- **id=15**(multi_compare, "비교") — DASH-002/HCX-007 둘 다 tool_ok=False
  (get_latest_report 만 부름). 비교형 질의에서 어떤 tool 조합을 써야
  하는지 모델 공통으로 헷갈리는 패턴.
- **id=26/27**(ownership_analysis) — 3개 모델 전부 task_success=False.
  Stage 1/4 failure_analysis 에서도 지분/보유 관련 질의의 retrieval
  난이도가 다른 route 대비 높다는 관찰과 일치 — Stage 11(E2E)에서
  metadata filter/query rewriting 개선 여지로 재확인 필요.
- **id=22/23**(calculation) — DASH-002/HCX-005 둘 다 tool 0건 호출로
  실패, HCX-007 만 성공. "계산이 필요하면 calculate_* 를 쓰라"는 system
  prompt 지시를 따르는 능력이 모델 성능에 강하게 비례하는 것으로 보임.

## Resource
`mem_rss_delta_mb` 는 모델 간 부호/크기가 들쭉날쭉해(-1671MB ~ +1220MB)
신뢰할 수 있는 신호가 아니다(GC 타이밍에 좌우되는 노이즈로 판단) — HCX
API 호출 자체는 로컬 모델을 로드하지 않으므로 애초에 RSS 로 의미있는
차이가 나기 어렵다. 대신 `mean_tool_calls`(API 호출 횟수 proxy)를 비용
지표로 본다: DASH-002 0.93 < HCX-007 1.72 < HCX-005 2.66. HCX-007 이
HCX-005보다도 호출 수가 적어 API 비용 측면에서도 더 유리하다.

## 결론 / 권고
baseline = **HCX-007**. 정확도(tool/argument/task 3개 지표 전부), 지연,
API 호출 비용(mean_tool_calls) 모두에서 HCX-005 를 앞서므로 trade-off
자체가 거의 없다(유일한 우려는 관찰된 400/429 오류 빈도가 약간 더 높다는
점 — 재시도 로직으로 대부분 흡수되지만 프로덕션에서는 `max_retries`를
기본값보다 살짝 늘리는 것을 고려). 남은 실패의 대다수(5/7)는 모델이 아닌
retrieval 단의 한계이므로, Stage 11(E2E)에서 재확인 대상으로 남긴다.
`HCXClient`에 추가한 `thinking` 파라미터(모델명에 "007" 포함 시
`{"effort":"none"}` 자동 적용)는 정식 프로덕션 코드에 반영되어 있어
`agent_loop.py`/`ask.py` 호출부 수정 없이 HCX-007 을 그대로 쓸 수 있다.
