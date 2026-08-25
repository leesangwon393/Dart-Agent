"""진단 전용 스크립트 (순수 진단, 코드 수정 없음).

100문항 배치에서 발견된 "확인할 수 없습니다" 실패 케이스 중 4~5건을
assemble_pipeline.assemble()로 조립한 실제 production 파이프라인으로
재실행하고, tool_calls trace + evidence_pack.prompt_text 전체를 파일로
저장한다. ask()/agent_loop.py/tools.py/validator.py 등은 전혀 수정하지
않고 import 해서 그대로 쓴다.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "results" / "generalization_check" / "100q_batch"))

from assemble_pipeline import assemble, log  # noqa: E402
from disclosure_rag.agent.ask import ask  # noqa: E402

OUT_DIR = REPO_ROOT / "results" / "generalization_check" / "100q_batch"

CASES = [
    {"tag": "sk_hynix_opincome", "query": "SK하이닉스의 2025년 영업이익은 얼마야?"},
    {"tag": "samsung_sdi_opincome", "query": "삼성SDI의 2025년 영업이익은 얼마야?"},
    {"tag": "hyundai_daewoo_revenue", "query": "현대건설와 대우건설의 2025년 매출액을 비교해줘"},
    {"tag": "lgh_amore_opincome", "query": "LG생활건강와 아모레퍼시픽의 2025년 영업이익을 비교해줘"},
    {"tag": "hyundai_motor_contract_event", "query": "현대자동차가 최근 체결한 계약 중 해지되거나 정정된 게 있어?"},
]


def tool_call_to_dict(tc) -> dict:
    result = tc.result
    # search_disclosures 결과의 text는 매우 길 수 있으므로 앞부분만 남기되
    # 전체 길이/주요 메타데이터는 보존한다.
    result_summary = {}
    if isinstance(result, dict) and "results" in result and isinstance(result["results"], list):
        result_summary["n_results"] = len(result["results"])
        result_summary["note"] = result.get("note")
        result_summary["items"] = []
        for item in result["results"]:
            item_copy = dict(item)
            text = item_copy.get("text", "") or ""
            item_copy["text_len"] = len(text)
            item_copy["text_preview"] = text[:600]
            item_copy.pop("text", None)
            result_summary["items"].append(item_copy)
    else:
        result_summary = result
    return {"name": tc.name, "arguments": tc.arguments, "result": result_summary}


def main():
    log("파이프라인 조립 시작 (assemble_pipeline.assemble, 수정 없이 그대로 재사용)")
    ctx = assemble()
    log("조립 완료 — 진단 케이스 재실행 시작")

    all_out = []
    for case in CASES:
        tag, query = case["tag"], case["query"]
        log(f"[{tag}] 실행: {query}")
        t0 = time.time()
        result = ask(
            ctx["agent_client"], ctx["tools"], query,
            entity_extractor=ctx["extractor"], router=ctx["router"],
            answer_client=ctx["answer_client"],
        )
        elapsed = time.time() - t0
        log(f"[{tag}] 완료 ({elapsed:.1f}s) route={result.trace.route} n_tool_calls={len(result.trace.tool_calls)}")

        record = {
            "tag": tag,
            "query": query,
            "elapsed": elapsed,
            "entities": json.loads(result.trace.entities.model_dump_json()),
            "normalized_query": result.trace.normalized_query,
            "route": result.trace.route,
            "route_score": result.trace.route_score,
            "iterations": result.trace.iterations,
            "stopped_reason": result.trace.stopped_reason,
            "tool_calls": [tool_call_to_dict(tc) for tc in result.trace.tool_calls],
            "evidence_pack_prompt_text": result.evidence_pack.prompt_text,
            "n_citations": len(result.evidence_pack.citations),
            "answer": result.answer,
            "validation": {
                "numbers_grounded": result.validation.numbers_grounded,
                "ungrounded_numbers": sorted(result.validation.ungrounded_numbers),
                "has_citation": result.validation.has_citation,
                "warnings": result.validation.warnings,
            },
        }
        all_out.append(record)

        # 케이스별로 즉시 저장 (중간에 실패해도 이전 결과 보존)
        out_path = OUT_DIR / f"diagnosis_trace_{tag}.json"
        out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"[{tag}] 저장: {out_path}")

    combined_path = OUT_DIR / "diagnosis_traces_all.json"
    combined_path.write_text(json.dumps(all_out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"전체 저장: {combined_path}")


if __name__ == "__main__":
    main()
