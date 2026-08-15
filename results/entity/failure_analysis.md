# Stage 8 — Entity Extraction: Failure Analysis

## 핵심 관찰
`rule_only` 가 5개 지표 전부에서 압도적 1위(거의 만점: company_EM=1.0,
correction_EM=1.0, report_name=1.0, metric_F1=0.971, period_F1=1.0)이고,
지연은 사실상 0(12μs). `rule_hcx_fallback`/`hcx_only` 는 정확도가 뚜렷이
낮고(metric_F1 ~0.40, period_F1 ~0.556) 지연도 7초+ 로 압도적으로 느리다.

**중요 caveat**: 이 gold query 40개는 필자가 `entity_extractor.py`(rule 기반)
를 만들 때(Phase 12) 염두에 둔 것과 유사한 어투로 작성했다 — "삼성전자
2025년 사업보고서" 처럼 rule 의 정규식이 정확히 겨냥한 패턴과 잘 맞는다.
따라서 이 결과가 "rule 이 항상 이긴다"는 일반 결론은 아니고, **이번 corpus/
질의 분포 조건에서는** rule 이 압도적으로 유리하다는 것으로 해석해야 한다.

## HCX 기반 방식의 실패 유형
- **Period false positive**: HCX 는 period precision=0.385, recall=1.0 —
  gold 에 기간이 없는 질의(예: "삼성전자 최대주주가 누구야?")에도 뭔가
  기간을 채워 넣는 경향(예: 질의 시점 기준 "최근"을 임의 연도로 해석)이
  있어 오탐이 많다. Rule 은 명시적 연도/분기 패턴이 없으면 절대 채우지
  않아 이 문제가 없다.
- **Metric 추출 불일치**: HCX 가 "영업이익률"을 "영업이익"으로만 뽑거나,
  반대로 rule 사전에 없는 표현(동의어)을 잡아내는 등 표현이 정확히 일치하지
  않는 경우가 많아 F1 이 낮게 나옴. 다만 sanity 하게 보면 의미상으로는
  크게 틀리지 않은 경우도 섞여 있어(엄격한 문자열 매칭의 한계), 실제
  서비스 영향은 지표가 보여주는 것보다 작을 수 있다.
- **report_name 오검출**: HCX 가 명시되지 않은 report_name 을 추측해서
  채우거나 반대로 놓치는 경우가 있었음(precision/recall 균형이 rule 대비
  나쁨).
- **HCX API 호출 시 간헐적 400 재시도**: `rule_hcx_fallback`, `hcx_only`
  둘 다 실행 중 "Unsupported function" 400 이 몇 차례 발생했으나 재시도로
  전부 복구됨(hcx_client.py 의 백오프 로직이 정상 작동).

## 결론 / 권고
baseline = **Rule only**. 정확도, 지연 둘 다 압도적 우위라 trade-off
자체가 존재하지 않는다. 단, rule 사전에 없는 새로운 표현/약어가 등장하는
질의(이번 eval set 에는 부족)에서는 rule 이 취약할 수 있으므로, 향후 더
다양한 질의 유형(구어체, 약어, 오타 등)을 gold set 에 추가해 재검증 필요
— 이번 결과를 "HCX 기반 추출은 전혀 필요 없다"는 결론으로 과도하게
일반화하지 않는다.
