"""Query Normalize (§36): Semantic Router 가 회사명/연도 자체에 과도하게
좌우되지 않도록 회사명을 [COMPANY]/[COMPANY_1]/[COMPANY_2], 연도를
[YEAR]/[YEAR_1]/[YEAR_2] placeholder 로 치환한다. 값을 삭제하지 않고, 개수와
질의 구조는 그대로 보존한다.

2026-08-27: 연도 치환은 처음부터 routes.py 의 utterance("[COMPANY]의 [YEAR]
매출액은...")가 기대하던 동작이었지만, 실제로는 회사명만 치환하고 연도는 한
번도 치환한 적이 없었다(2026-08-25 발견, PROJECT_STATE §12) — router 가
"[YEAR]"라는 토큰을 학습만 하고 실제 추론에서는 절대 못 보는 train/inference
불일치였다. company_spans/period_spans 를 하나의 정렬된 리스트로 합쳐 한
번에 치환해야 오프셋이 안 어긋난다(회사명 치환 후 연도 치환을 순차로 하면
이미 바뀐 문자열 위에서 좌표가 틀어짐)."""

from __future__ import annotations

from disclosure_rag.common.unicode_utils import normalize_nfc
from disclosure_rag.entity.entity_extractor import ExtractedEntities


def _index_map(keys_in_order: list[str]) -> dict[str, int]:
    order: list[str] = []
    for key in keys_in_order:
        if key not in order:
            order.append(key)
    return {key: i + 1 for i, key in enumerate(order)}


def normalize_query(entities: ExtractedEntities) -> str:
    # span 은 normalize_nfc(raw_query) 기준으로 계산됐으므로, 동일하게 정규화된
    # 문자열 위에서만 슬라이싱해야 offset 이 어긋나지 않는다.
    text = normalize_nfc(entities.raw_query)

    # (start, end, kind, key) — kind 로 placeholder 종류를, key 로 같은 값
    # 재언급 시 같은 번호를 재사용할지를 판단한다. start 기준으로 정렬해서
    # 한 번의 좌→우 스캔으로 치환한다(company/period 를 따로 두 번 치환하면
    # 두 번째 치환 시점엔 문자열이 이미 바뀌어 있어 좌표가 어긋난다).
    items: list[tuple[int, int, str, str]] = (
        [(s, e, "company", corp) for s, e, corp in entities.company_spans]
        + [(s, e, "period", year) for s, e, year in entities.period_spans]
    )
    items.sort(key=lambda it: it[0])

    # 방어적 겹침 제거: company/period span 이 서로 겹칠 일은 실무상 없지만
    # (회사명엔 4자리 연도가 안 나옴), 겹치면 먼저 나온(더 앞에서 시작한)
    # span 을 우선하고 뒤엣것은 버린다.
    filtered: list[tuple[int, int, str, str]] = []
    cursor_end = -1
    for item in items:
        if item[0] >= cursor_end:
            filtered.append(item)
            cursor_end = item[1]

    if not filtered:
        return text

    company_index = _index_map([k for _s, _e, kind, k in filtered if kind == "company"])
    period_index = _index_map([k for _s, _e, kind, k in filtered if kind == "period"])
    company_multi = len(company_index) > 1
    period_multi = len(period_index) > 1

    pieces = []
    cursor = 0
    for start, end, kind, key in filtered:
        pieces.append(text[cursor:start])
        if kind == "company":
            placeholder = f"[COMPANY_{company_index[key]}]" if company_multi else "[COMPANY]"
        else:
            placeholder = f"[YEAR_{period_index[key]}]" if period_multi else "[YEAR]"
        pieces.append(placeholder)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)
