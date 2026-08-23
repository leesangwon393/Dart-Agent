"""Phase 13: 전체 코퍼스를 새 표 semantic block chunking 로직으로 재청킹한다.

기존 leaf chunk 수(약 467,043개, gpu_embeddings/ 이전 결과 기준)와 비교해서
터무니없이 다르면(예: 10배 차이) 버그이므로 원인을 조사해야 한다 — 이번
변경은 표 있는 chunk 의 "개수"를 약간 바꿀 수 있지만 전체 corpus 구조를
바꾸는 게 아니다.

결과는 chunks_v2/all_chunks.pkl (parent+leaf 전체) 에 저장해 재임베딩
스크립트가 재사용할 수 있게 한다.
"""

from __future__ import annotations

import logging
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from disclosure_rag.chunking.chunk_schema import filter_leaf_chunks
from disclosure_rag.pipeline import build_all_chunks

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

OUT_DIR = Path(__file__).resolve().parents[1] / "chunks_v2"
OUT_DIR.mkdir(exist_ok=True)

PREV_LEAF_COUNT = 467_043  # gpu_embeddings/ 이전 결과 기준 (PROJECT_STATE.md §7)


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] 전체 코퍼스 파싱+청킹 시작...", flush=True)
    chunks = build_all_chunks("corpus", validate=True)
    leaf = filter_leaf_chunks(chunks)
    dt = time.time() - t0

    print(f"[{time.strftime('%H:%M:%S')}] 완료 ({dt:.1f}초)", flush=True)
    print(f"총 chunk 수: {len(chunks)} (leaf/검색대상: {len(leaf)}, parent/context용: {len(chunks) - len(leaf)})")

    ratio = len(leaf) / PREV_LEAF_COUNT if PREV_LEAF_COUNT else float("nan")
    print(f"이전 leaf chunk 수({PREV_LEAF_COUNT})와 비율: {ratio:.3f}x")
    if not (0.5 <= ratio <= 2.0):
        print("경고: leaf chunk 수가 이전 대비 2배 이상 차이남 — 버그 의심, 원인 조사 필요.", flush=True)

    with open(OUT_DIR / "all_chunks.pkl", "wb") as f:
        pickle.dump(chunks, f, protocol=pickle.HIGHEST_PROTOCOL)
    with open(OUT_DIR / "leaf_chunks.pkl", "wb") as f:
        pickle.dump(leaf, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"저장 완료: {OUT_DIR}/all_chunks.pkl, {OUT_DIR}/leaf_chunks.pkl")

    # semantic block chunking 이 실제로 몇 개 chunk에 영향을 줬는지 요약
    with_table_id = [c for c in leaf if c.table_id]
    multi_fragment = [c for c in with_table_id if (c.table_chunk_count or 1) > 1]
    print(f"table_id 있는 leaf chunk: {len(with_table_id)}, 그 중 표가 여러 조각으로 나뉜 chunk: {len(multi_fragment)}")


if __name__ == "__main__":
    main()
