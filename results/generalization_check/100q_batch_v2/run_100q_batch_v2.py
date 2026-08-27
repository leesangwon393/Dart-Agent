"""100문항 배치 v2 실행기 — 전체 코퍼스(70개사, 626,497 leaf chunk) 재검증.

v1(results/generalization_check/100q_batch/run_100q_batch.py)과 로직은 동일하다
(같은 100문항, 10건마다 체크포인트, 재실행 안전). questions.json은 v1 것을 그대로
재사용한다(비교 공정성) — 이 디렉터리에 새로 만들지 않는다.

사용법:
  nohup .venv/bin/python results/generalization_check/100q_batch_v2/run_100q_batch_v2.py \
      > results/generalization_check/100q_batch_v2/run.log 2>&1 &
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

BATCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BATCH_DIR.parents[2]
sys.path.insert(0, str(BATCH_DIR))
sys.path.insert(0, str(REPO_ROOT / "src"))

QUESTIONS_PATH = BATCH_DIR.parent / "100q_batch" / "questions.json"  # v1 것 그대로 재사용
RESULTS_PATH = BATCH_DIR / "results.json"
SLEEP_BETWEEN_QUESTIONS = 3.0  # 질문 사이 pacing (429 완화)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _key(q: dict) -> str:
    return f"{q['label']}::{q['category']}::{q['query']}"


def load_existing_results() -> list[dict]:
    if RESULTS_PATH.is_file():
        try:
            return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            log(f"기존 {RESULTS_PATH} 파싱 실패 — 빈 리스트로 새로 시작")
    return []


def save_results(results: list[dict]) -> None:
    tmp = RESULTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(RESULTS_PATH)


def run_one(ctx: dict, q: dict) -> dict:
    from disclosure_rag.agent.ask import ask

    entry = {"label": q["label"], "category": q["category"], "query": q["query"], "companies": q["companies"]}
    t0 = time.time()
    try:
        result = ask(
            ctx["agent_client"], ctx["tools"], q["query"],
            entity_extractor=ctx["extractor"], router=ctx["router"],
            max_iterations=6, max_answer_retries=1,
            answer_client=ctx["answer_client"],
        )
        elapsed = time.time() - t0
        n_citations = len(result.evidence_pack.citations)
        entry.update({
            "route": result.trace.route,
            "route_score": result.trace.route_score,
            "iterations": result.trace.iterations,
            "n_tool_calls": len(result.trace.tool_calls),
            "n_citations": n_citations,
            "answer": result.answer,
            "grounded": bool(result.validation.numbers_grounded),
            "numbers_grounded": bool(result.validation.numbers_grounded),
            "ungrounded_numbers": sorted(result.validation.ungrounded_numbers),
            "verified_derived_numbers": result.validation.verified_derived_numbers,
            "citation": bool(result.validation.has_citation),
            "correction_evidence_complete": result.validation.correction_evidence_complete,
            "warnings": result.validation.warnings,
            "elapsed": elapsed,
        })
    except Exception as e:  # noqa: BLE001
        elapsed = time.time() - t0
        entry.update({"error": f"{type(e).__name__}: {e}", "elapsed": elapsed})
        log(f"  !! 오류: {entry['error']}")
        log(traceback.format_exc(limit=3))
    return entry


def main():
    from assemble_pipeline_v2 import assemble

    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    log(f"질문 {len(questions)}건 로드 (v1과 동일 파일: {QUESTIONS_PATH})")

    results = load_existing_results()
    done_keys = {_key(r) for r in results}
    log(f"기존 완료 {len(done_keys)}건 발견 — 나머지 이어서 실행")

    ctx = assemble()

    # 조립 메타(leaf chunk 수, dense 벡터 수, BM25 소요시간)를 별도 파일에 기록
    meta_path = BATCH_DIR / "assemble_meta.json"
    meta_path.write_text(json.dumps({
        "n_leaf_chunks": ctx["n_leaf_chunks"],
        "n_dense_vectors": ctx["n_dense_vectors"],
        "bm25_build_seconds": ctx["bm25_elapsed"],
        "assembled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    remaining = [q for q in questions if _key(q) not in done_keys]
    log(f"실행할 질문 {len(remaining)}건 (전체 {len(questions)}건 중)")

    since_last_save = 0
    for i, q in enumerate(remaining, start=1):
        log(f"[{i}/{len(remaining)}] {q['label']} / {q['category']}: {q['query']}")
        entry = run_one(ctx, q)
        results.append(entry)
        since_last_save += 1
        status = "OK" if "error" not in entry else "ERROR"
        log(f"  -> {status} elapsed={entry.get('elapsed', 0):.1f}s route={entry.get('route')} "
            f"grounded={entry.get('grounded')} citation={entry.get('citation')}")

        if since_last_save >= 10:
            save_results(results)
            log(f"체크포인트 저장: {len(results)}/{len(questions)}건")
            since_last_save = 0

        time.sleep(SLEEP_BETWEEN_QUESTIONS)

    save_results(results)
    log(f"전체 완료. 최종 저장: {len(results)}/{len(questions)}건 -> {RESULTS_PATH}")


if __name__ == "__main__":
    main()
