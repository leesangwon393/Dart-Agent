"""v1(subset 코퍼스 배치) vs v2(전체 코퍼스 배치) 집계 비교표를 만든다.

읽기 전용 — 두 results.json을 그대로 집계만 한다(HCX 재호출 없음).
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

BATCH_DIR = Path(__file__).resolve().parent
V1_PATH = BATCH_DIR.parent / "100q_batch" / "results.json"
V2_PATH = BATCH_DIR / "results.json"


def summarize(results: list[dict], label: str) -> dict:
    total = len(results)
    errors = [r for r in results if "error" in r]
    ok = [r for r in results if "error" not in r]
    grounded = [r for r in ok if r.get("grounded")]
    citation = [r for r in ok if r.get("citation")]
    elapsed = [r.get("elapsed", 0) for r in ok]
    route_dist = Counter(r.get("route") for r in ok)
    return {
        "label": label,
        "total": total,
        "api_errors": len(errors),
        "api_error_pct": round(100 * len(errors) / total, 1) if total else None,
        "ok": len(ok),
        "grounded_true": len(grounded),
        "grounded_pct_of_ok": round(100 * len(grounded) / len(ok), 1) if ok else None,
        "citation_true": len(citation),
        "citation_pct_of_ok": round(100 * len(citation) / len(ok), 1) if ok else None,
        "mean_elapsed": round(statistics.mean(elapsed), 1) if elapsed else None,
        "median_elapsed": round(statistics.median(elapsed), 1) if elapsed else None,
        "route_dist": dict(route_dist),
        "error_labels": [r["label"] for r in errors],
    }


def main():
    v1 = json.loads(V1_PATH.read_text(encoding="utf-8"))
    if not V2_PATH.is_file():
        print(f"{V2_PATH} 아직 없음 — v2 배치가 끝나지 않았거나 경로가 다름")
        return
    v2 = json.loads(V2_PATH.read_text(encoding="utf-8"))

    s1 = summarize(v1, "v1 (subset 코퍼스, 2026-08-18)")
    s2 = summarize(v2, "v2 (전체 코퍼스, 626,497 leaf chunk)")

    print(json.dumps({"v1": s1, "v2": s2}, ensure_ascii=False, indent=2))

    # 11건 "2025년 재무지표 확인불가" 추적
    tracked_2025 = [
        ("삼성SDI(2차전지)", "삼성SDI의 2025년 영업이익은 얼마야?"),
        ("현대건설(건설)", "현대건설의 2025년 부채비율은 얼마야?"),
        ("기아(자동차)", "기아의 2025년 매출액은 얼마인가?"),
        ("SK하이닉스(반도체)", "SK하이닉스의 2025년 영업이익은 얼마야?"),
        ("셀트리온(바이오)", "셀트리온의 2025년 영업이익은 얼마야?"),
        ("기아+현대자동차(자동차+자동차)", "기아와 현대자동차의 2025년 매출액을 비교해줘"),
        ("신한지주+KB금융(금융+금융)", "신한지주와 KB금융의 2025년 영업수익을 비교해줘"),
        ("하이브+와이지엔터테인먼트(엔터+엔터)", "하이브와 와이지엔터테인먼트의 2025년 영업이익을 비교해줘"),
        ("LG생활건강+아모레퍼시픽(화장품+화장품)", "LG생활건강와 아모레퍼시픽의 2025년 영업이익을 비교해줘"),
        ("현대건설+대우건설(건설+건설)", "현대건설와 대우건설의 2025년 매출액을 비교해줘"),
        ("현대모비스+삼성전기(자동차부품+전자부품)", "현대모비스와 삼성전기의 2025년 매출액을 비교해줘"),
    ]
    v2_by_key = {(r["label"], r["query"]): r for r in v2}
    print("\n=== v1 '확인불가' 11건 -> v2 재추적 ===")
    fixed = 0
    for label, query in tracked_2025:
        r2 = v2_by_key.get((label, query))
        if r2 is None:
            print(f"- {label} | {query} -> v2에 없음(매칭 실패, 라벨/쿼리 확인 필요)")
            continue
        ans = r2.get("answer", r2.get("error", ""))
        no_data = ("확인할 수 없" in ans) or ("포함되어 있지 않" in ans) or ("정보가 없" in ans) or ("확인되지 않" in ans)
        status = "여전히 확인불가" if no_data else "수치 답변함(원문대조 필요)"
        if not no_data:
            fixed += 1
        print(f"- {label} | {query} -> {status} | ans[:80]={ans[:80]!r}")
    print(f"\n11건 중 '확인불가'를 벗어나 수치를 답한 건수: {fixed}/11 (원문대조로 정확성 별도 확인 필요)")


if __name__ == "__main__":
    main()
