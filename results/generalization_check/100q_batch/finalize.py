"""results.json 완료 후 실행: matrix.csv 에 100행 추가 + summary.md 생성.

verdict 규칙(자동 집계 전용, 사람이 원문대조로 직접 검증한 15~20건은 이 스크립트
실행 후 matrix.csv 를 손으로 다시 수정해 verdict/note 를 갱신한다):
  - error 필드가 있으면 verdict="FAIL(자동, API오류)"
  - grounded and citation 이면 verdict="PASS(자동)"
  - 그 외(citation 없음/grounded 실패)는 verdict="FAIL(자동)"
"자동"이라고 명시하는 이유: validator 통과가 실제 정답을 보장하지 않는다는 게
이 프로젝트에서 이미 확인된 사실(알테오젠 10배 오류 사례) — 사람이 원문대조로
직접 확인하기 전까지는 그렇게 표시해 구분한다.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

BATCH_DIR = Path(__file__).resolve().parent
RESULTS_PATH = BATCH_DIR / "results.json"
MATRIX_PATH = BATCH_DIR.parent / "matrix.csv"
SUMMARY_PATH = BATCH_DIR / "summary.md"


def verdict_and_note(r: dict) -> tuple[str, str]:
    if "error" in r:
        return "FAIL(자동, API오류)", r["error"][:200]
    grounded = r.get("grounded", False)
    citation = r.get("citation", False)
    note_bits = [f"route={r.get('route')}", f"n_citations={r.get('n_citations')}", f"elapsed={r.get('elapsed', 0):.1f}s"]
    if r.get("ungrounded_numbers"):
        note_bits.append(f"ungrounded={r['ungrounded_numbers']}")
    if r.get("verified_derived_numbers"):
        note_bits.append(f"암산검산통과={list(r['verified_derived_numbers'].keys())}")
    if r.get("warnings"):
        note_bits.append(f"warnings={r['warnings']}")
    verdict = "PASS(자동)" if (grounded and citation) else "FAIL(자동)"
    return verdict, "; ".join(note_bits)


def append_matrix(results: list[dict]) -> int:
    rows = []
    for r in results:
        verdict, note = verdict_and_note(r)
        rows.append([r["label"], r["category"], r["query"], verdict, note])
    with open(MATRIX_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    return len(rows)


def build_summary(results: list[dict]) -> str:
    n = len(results)
    n_error = sum(1 for r in results if "error" in r)
    ok = [r for r in results if "error" not in r]
    n_ok = len(ok)
    n_grounded = sum(1 for r in ok if r.get("grounded"))
    n_citation = sum(1 for r in ok if r.get("citation"))
    n_both = sum(1 for r in ok if r.get("grounded") and r.get("citation"))
    n_derived = sum(1 for r in ok if r.get("verified_derived_numbers"))

    route_counts = Counter(r.get("route") for r in ok)
    cat_counts = Counter(r["category"] for r in results)
    cat_pass = Counter(r["category"] for r in ok if r.get("grounded") and r.get("citation"))

    elapsed_vals = [r.get("elapsed", 0) for r in ok]
    avg_elapsed = sum(elapsed_vals) / len(elapsed_vals) if elapsed_vals else 0.0

    lines = [
        "# 100문항 일반화 테스트 — 자동 집계 요약",
        "",
        f"- 총 문항: {n}, 정상 완료: {n_ok}, API 오류로 실패: {n_error}",
        f"- grounded율(numbers_grounded): {n_grounded}/{n_ok} ({n_grounded/n_ok*100:.1f}%)" if n_ok else "- n_ok=0",
        f"- citation율(has_citation): {n_citation}/{n_ok} ({n_citation/n_ok*100:.1f}%)" if n_ok else "",
        f"- grounded AND citation (자동 PASS): {n_both}/{n_ok} ({n_both/n_ok*100:.1f}%)" if n_ok else "",
        f"- 암산이지만 검산 통과(verified_derived_numbers 존재): {n_derived}/{n_ok}",
        f"- 평균 elapsed: {avg_elapsed:.1f}s",
        "",
        "## Route 분포",
        "",
    ]
    for route, cnt in route_counts.most_common():
        lines.append(f"- {route}: {cnt}")

    lines += ["", "## Category별 분포 (자동 PASS 기준)", ""]
    for cat, total in cat_counts.items():
        p = cat_pass.get(cat, 0)
        lines.append(f"- {cat}: {p}/{total} 자동 PASS ({p/total*100:.1f}%)")

    lines += ["", "## API 오류 목록", ""]
    for r in results:
        if "error" in r:
            lines.append(f"- {r['label']} / {r['category']}: {r['query']} -> {r['error'][:200]}")

    lines += [
        "",
        "## 주의",
        "",
        "이 요약은 validator 의 자동 판정(grounded/citation)만 집계한 것이다. "
        "validator 통과가 실제 정답을 보장하지 않는다는 사실이 이 프로젝트에서 "
        "이미 확인되었다(알테오젠 10배 오류 사례 — validator 는 정확히 잡아냈지만, "
        "반대 방향 오류가 자동 검증을 통과할 가능성은 항상 있다). 15~20건은 원문 "
        "대조로 직접 검증한 결과를 별도로 아래에 기록한다(수동으로 채울 것).",
        "",
        "## 수동 원문 대조 검증 (15~20건)",
        "",
        "(이 섹션은 배치 완료 후 사람이/에이전트가 직접 corpus/raw 원문과 대조해 채운다)",
        "",
    ]
    return "\n".join(lines)


def main():
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    print(f"results.json 로드: {len(results)}건")
    n_added = append_matrix(results)
    print(f"matrix.csv 에 {n_added}행 추가 완료 -> {MATRIX_PATH}")
    summary = build_summary(results)
    SUMMARY_PATH.write_text(summary, encoding="utf-8")
    print(f"summary.md 작성 완료 -> {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
