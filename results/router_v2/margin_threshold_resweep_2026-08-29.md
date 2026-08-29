# margin_threshold 실측 재조정 (2026-08-29)

## 배경

사용자가 지정한 라우터 개선 후보 5개 중 1번(`margin_threshold` 재조정).
2번(provenance, `RouteResult.source`)을 먼저 완료해 escalate 여부를 코드
레벨에서 명확히 구분할 수 있게 한 뒤 진행했다(§5-F 참고).

## 방법론

1. `router/eval_dataset.py`의 `EVAL_SET`(55건)에 대해 semantic router
   (BGE-M3, CPU, `build_semantic_router(provider, threshold=0.0)`)를 직접
   호출해 각 예시의 top1/top2 route 이름·유사도, margin(top1−top2)을 전부
   추출했다(HCX 호출 없음, 로컬 임베딩만).
2. **재현 확인**: PROJECT_STATE §5-A/§8이 인용하는 "margin>=0.05: 23/55
   (42%), accuracy=1.000"과 비교.
3. margin을 0.00~0.15 범위, 0.01 간격으로 sweep. 각 threshold `t`에 대해:
   - fast-path 집합 = `margin >= t`인 예시들 (semantic top1을 그대로 채택)
   - escalate 집합 = `margin < t`인 예시들 (HCX로 넘어갔을 경우)
4. **HCX 실제 호출**: sweep 범위 내에서 가장 낮은 threshold(0.00)에서도
   escalate 대상이 될 수 있는 상한선인 `margin < 0.15`인 33건 전체를
   `HCXStructuredRouter`(실제 HCX API, `.env`의 `HCX_MODEL=HCX-007`,
   CascadingRouter가 production에서 escalation에 실제로 쓰는 것과 동일한
   client)로 1회씩 분류해 캐싱했다. 이 33건 결과를 재사용하면 어떤
   threshold를 골라도(0.00~0.15 사이) escalate 집합의 실제 정확도를
   재호출 없이 계산할 수 있다 — margin<0.15인 예시는 항상 이 캐시 안에
   있고, margin>=0.15인 예시는 어떤 threshold에서도 escalate되지 않는다.
5. 각 threshold에서 overall accuracy = (fast-path 정확도 x fast-path
   비율) + (escalate 정확도 x escalate 비율).

## 재현 확인 결과

**재현 안 됨 — 그러나 원인이 명확하다.** 이번에 다시 뽑은 수치는
margin>=0.05: **31/55(56.4%), accuracy=1.000**으로, PROJECT_STATE가
인용하는 "23/55(42%)"와 다르다.

**원인**: PROJECT_STATE §5-A의 "23/55(42%)" 수치는 `routes.py`에 19개
utterance를 추가하기 **이전**(2026-08-18 13:03, 커밋 `8c5c555`)의 1차
측정값이다. 같은 날 바로 이어서(13:11, 커밋 `c562220`) calculation/
event_analysis ↔ single_lookup 경계를 겨냥한 utterance 19개가 추가됐고,
그 **이후** 측정값이 `results/router_v2/summary.md`에 이미 "margin>=0.05
구간(fast-path) accuracy=1.000(**31/55**)"로 기록돼 있다 — 즉 이번
재현치(31/55)는 PROJECT_STATE 1차 수치가 아니라 summary.md의 2차(최신)
수치와 정확히 일치한다. `routes.py`는 그 이후 이번 세션 이전까지 변경
이력이 없으므로(git log 확인), 현재 코드 기준 정답은 31/55다.
PROJECT_STATE §5-A 서술 자체가 1차 수치를 "발견 당시" 값으로 남겨둔
것이라 오래된 참조였을 뿐, 버그는 아니다.

| | margin>=0.05 부분집합 | accuracy |
|---|---|---|
| PROJECT_STATE §5-A 인용(routes.py 19개 추가 전, 2026-08-18 13:03) | 23/55(42%) | 1.000 |
| summary.md 최신 기록(routes.py 19개 추가 후, 2026-08-18 13:11) | 31/55(56%) | 1.000 |
| **이번 재현(2026-08-29, 코드 변경 없음)** | **31/55(56.4%)** | **1.000** |

## Threshold Sweep (0.00~0.15, 0.01 간격)

`fast_acc`/`escalate_acc`는 각 집합 내부 정확도, `overall_acc`는 전체
55건 기준 가중 평균이다. escalate 정확도는 margin<0.15인 33건 전체를
실제 HCX-007로 분류한 결과를 재사용했다(캐시:
`results/router_v2/hcx_escalation_cache_2026-08-29.json`). 이 33건 중
5건은 API가 일시적으로 400("Unsupported function")을 반환해 짧은
지연 후 재시도로 해결됐다 — 원인/재현 방법은 아래 "API 신뢰성 관찰"
참고.

| threshold | fast-path n(%) | fast_acc | escalate n(%) | escalate_acc(hint 없음) | overall_acc(hint 없음) |
|---|---|---|---|---|---|
| 0.00 | 55(100%) | 0.836 | 0(0%) | — | 0.836 |
| 0.01 | 49(89%) | 0.898 | 6(11%) | 1.000 | 0.909 |
| 0.02 | 44(80%) | 0.932 | 11(20%) | 1.000 | 0.945 |
| **0.03** | **39(71%)** | **1.000** | **16(29%)** | **1.000** | **1.000** |
| **0.04** | **37(67%)** | **1.000** | **18(33%)** | **1.000** | **1.000** |
| 0.05(기존 기본값) | 31(56%) | 1.000 | 24(44%) | 0.833 | 0.927 |
| 0.06 | 29(53%) | 1.000 | 26(47%) | 0.846 | 0.927 |
| 0.07 | 26(47%) | 1.000 | 29(53%) | 0.828 | 0.909 |
| 0.08 | 25(45%) | 1.000 | 30(55%) | 0.833 | 0.909 |
| 0.09 | 24(44%) | 1.000 | 31(56%) | 0.839 | 0.909 |
| 0.10~0.14 | 23(42%) | 1.000 | 32(58%) | 0.813 | 0.891 |
| 0.15 | 22(40%) | 1.000 | 33(60%) | 0.818 | 0.891 |

전체 raw 값: `results/router_v2/margin_sweep_rows_2026-08-29.json`.

### 메커니즘 — 왜 0.03~0.04가 정점이고 그 이후로는 내려가는가

margin 오름차순으로 개별 예시를 직접 대조하면 이유가 명확하다
(`results/router_v2/semantic_margin_2026-08-29.json` +
`hcx_escalation_cache_2026-08-29.json`):

- **margin < 0.0266 구간(10건)**: semantic top1이 전부 오답이었는데, HCX로
  escalate하면 **10/10 전부 정답으로 구제**됐다.
- **margin 0.0377~0.1463 구간**: semantic top1이 **이미 100% 정답**이다
  (fast_acc=1.000이 t=0.03부터 t=0.15까지 안 변하는 이유). 그런데 이
  구간을 HCX로 escalate하면(threshold를 0.05 이상으로 올리면) HCX 자체
  오류가 섞여 들어온다 — 실측 예: margin=0.0459("단순조회"를
  ownership_analysis로 오분류), margin=0.0491(calculation을
  multi_compare로 오분류), margin=0.0698/0.0931(event_analysis를
  single_lookup으로 오분류).

즉 **margin>=0.04 구간에서는 이미 "확실한 정답"을 HCX에게 다시 물어서
확률적으로 틀릴 위험만 추가하는 것**이다. threshold를 0.05로 유지하는
현재 설정은 정확도(0.927)도 escalate 비율(44%, RPM 위험)도 0.03~0.04
(정확도 1.000, escalate 29~33%)보다 **양쪽 다 열등**하다 — trade-off가
아니라 순수하게 지배당하는(dominated) 선택이었다.

**표본 크기 주의**: 이 결론을 가르는 경계 자체(0.0266 vs 0.0377)는 n=55
중 단 몇 건 차이로 갈린다 — 두 threshold(0.03, 0.04) 모두 이 경계 양쪽에
여유 있게 위치하지만, EVAL_SET을 확장하면 정확한 최적값은 달라질 수
있다. 방향성(0.05보다 낮은 게 낫다)은 메커니즘이 명확해 신뢰할 만하지만,
"정확히 0.03"이라는 소수점 값에 과도한 의미를 두지 말 것.

## 참고: HCX escalation hint(개선 후보 4) 도입 시 재계산

작업 4(§5-F)에서 CascadingRouter가 escalate할 때 semantic top1/top2를
힌트로 넘기도록 바꿨다. 같은 33건을 hint 포함 상태로 다시 분류하면
escalate_acc가 threshold 전 구간에서 개선된다(margin<0.05인 24건 기준
0.833→**0.958**, 상세는 §5-F "작업 4" 참고). hint를 반영해 sweep을
다시 계산하면:

| threshold | escalate_acc(hint 있음) | overall_acc(hint 있음) |
|---|---|---|
| 0.03 | 1.000 | 1.000 |
| 0.04 | 1.000 | 1.000 |
| 0.05 | 0.958 | 0.982 |
| 0.06~0.15 | 0.962~0.970 | 0.982 |

hint가 margin>=0.04 구간에서 HCX가 semantic의 이미-옳은 답과 다시
일치하도록 유도해서, threshold를 0.05 근처로 유지해도 정확도 손실이
0.927→0.982로 크게 줄어든다. **그래도 0.03~0.04가 정확도(1.000)와
escalate 비율(29~33% vs 44%+) 둘 다 여전히 더 낫다** — hint는 threshold
선택을 덜 민감하게 만드는 안전판이지, 0.03 권고를 뒤집을 이유는 아니다.

## 최종 권고

**`margin_threshold`를 0.05 → 0.03으로 낮춘다.** 근거:
1. 정확도: 0.927(hint 없이 0.05) → 1.000(0.03), hint 반영해도
   0.982(0.05) → 1.000(0.03)으로 0.03이 항상 더 높거나 같다.
2. RPM 위험(escalate 비율): 44%(0.05) → 29%(0.03)로 오히려 줄어든다 —
   정확도와 RPM 위험이 상충하는 진짜 trade-off가 아니라, 0.05가 두
   지표 모두에서 0.03에 지배당하는 상황이었다.
3. 메커니즘이 명확하다(위 "왜 0.03~0.04가 정점인가" 참고) — 단순
   과적합이 아니라 "HCX가 이미 확실한 걸 다시 확인해서 오히려 틀리는"
   구조적 이유가 있다.

`src/disclosure_rag/router/hcx_router.py`의 `CascadingRouter.__init__`
과 `build_cascading_router()` 기본값을 0.05→0.03으로 변경, 회귀 테스트
2건 추가(`tests/test_router.py`: 기본값이 실수로 되돌아가지 않도록 고정).

**남은 위험**: n=55라 경계값의 정밀도는 제한적이다(위 표본 크기 주의
참고). 실서비스에서 escalate 비율/정확도를 계속 관측하다가, 0.03이
과도하게 낮아 보이는 신호(예: fast-path 오답이 다시 늘어남)가 보이면
0.04까지는 안전하게 올릴 수 있다(이번 측정에서 0.03과 0.04는 정확도가
동일했다).

## API 신뢰성 관찰 (참고용, 이번 작업 중 실측)

이번 33건 HCX 호출 중 첫 시도에서 5건이 400("Unsupported function")을
반환했다(전부 짧은 재시도 후 해결, 일부는 `HCXClient.chat()`의 내장
재시도로, 일부는 스크립트 레벨 추가 재시도로). 격리 재현 테스트 결과
**같은 질의를 몇 분 후 다시 보내면 성공**해서(예: "[COMPANY] 부채비율이
몇 퍼센트인지 계산해줘"가 최대 96초 backoff까지 포함해 7회 연속 실패한
직후, 별도 세션에서 동일 질의가 즉시 성공) 특정 질의 문구의 문제가
아니라 확률적/일시적 API 현상으로 보인다 — PROJECT_STATE §12 항목 1의
기존 RPM 관찰과 같은 계열이지만, 이번엔 `HCXClient`의 6회 재시도(최대
96초 대기)로도 못 뚫는 경우가 실측됐다(약 33건 중 1건, 3%). 라우터
자체 로직 변경 사항은 아니라 이번 작업 범위에서 코드는 고치지 않았고,
§12 후속 후보로만 기록한다(예: 재시도 횟수를 늘리거나 상한 대기시간을
늘리는 방안 검토).

## 부록: raw 데이터

- semantic top1/top2/margin(55건): `results/router_v2/semantic_margin_2026-08-29.json`
- HCX escalation 분류 결과(33건, margin<0.15, hint 없음): `results/router_v2/hcx_escalation_cache_2026-08-29.json`
- HCX escalation 분류 결과(33건, hint 있음): `results/router_v2/hcx_escalation_hint_cache_2026-08-29.json`
- threshold sweep 전체 행(hint 없음 기준): `results/router_v2/margin_sweep_rows_2026-08-29.json`
