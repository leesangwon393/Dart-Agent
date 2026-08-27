"""Kim이 전달한 전체 코퍼스 재청킹+재임베딩 산출물(pipeline_kim_v2/artifacts_v2/)을
우리 ChunkSchema/BM25Retriever/DenseRetriever 스택으로 읽어들이는 호환 로더.

**Kim 리포(/Users/isang-won/Downloads/pipeline-kim/)를 import 하지 않는다** — 여기
구현은 그 리포의 `l1.py::load_chunks()` / `retrieval/index_bundle.py::_load_dense()`가
쓰는 JSON/npz 스키마를 참고해서 새로 작성한 것이다(읽기 참고만, 코드 복붙 아님).

확인된 스키마(실제 레코드 3건 샘플로 검증, 2026-08-27):
- chunks.jsonl.gz 레코드 키: chunk_id, report_id, parent_chunk_id(optional),
  raw_text, company, corp_code, report_type, report_name, period, filing_date,
  section_path, content_type, source_path, is_correction, table_ids(Kim 전용,
  우리 스키마엔 없음 — pydantic extra="ignore" 기본값이라 조용히 버려짐),
  correction_group_id, correction_order, is_latest, field_codes, is_leaf.
  `text`(검색용 렌더텍스트)는 저장되지 않는다 — render_search_text()로 재현한다.
- dense_*.npz: z["chunk_ids"](str array), z["vectors"](float16, shape (N,1024)).

우리 ChunkSchema에만 있는 신규 필드(table_id/semantic_groups/metric_hints/
table_chunk_index/table_chunk_count/prev_table_chunk_id/next_table_chunk_id)는
Kim 레코드에 아예 없다 — pydantic 기본값(None/[])으로 채워지는 것을 실제 로드
테스트로 확인했다(아래 self-test 참고).
"""
from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from typing import Iterator

from disclosure_rag.chunking.chunk_schema import ChunkSchema, render_search_text
from disclosure_rag.retrieval.qdrant_store import QdrantVectorStore


def _open(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def iter_chunk_records(l1_dir: Path) -> Iterator[dict]:
    path = l1_dir / "chunks.jsonl.gz"
    if not path.is_file():
        path = l1_dir / "chunks.jsonl"
    with _open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_leaf_chunks(l1_dir: Path, *, log=print) -> list[ChunkSchema]:
    """leaf chunk만 ChunkSchema로 변환한다. field_codes는 기본으로 버린다
    (Kim의 load_chunks(include_field_codes=False)와 동일 — 81M field_refs를
    전부 pydantic 객체로 만들면 메모리가 감당 안 됨)."""
    t0 = time.time()
    out: list[ChunkSchema] = []
    n_seen = 0
    for rec in iter_chunk_records(l1_dir):
        n_seen += 1
        is_leaf = rec.pop("is_leaf", True)
        if not is_leaf:
            continue
        rec.pop("field_codes", None)
        rec.pop("table_ids", None)  # Kim 전용 필드, 우리 스키마엔 table_id(단수)만 있음
        rec["text"] = render_search_text(
            company=rec.get("company"), report_name=rec.get("report_name"),
            period=rec.get("period"), section_path=rec.get("section_path") or [],
            body_text=rec.get("raw_text", ""),
        )
        out.append(ChunkSchema(**rec))
        if n_seen % 100_000 == 0:
            log(f"  ...{n_seen}건 스캔, leaf {len(out)}건 누적 ({time.time()-t0:.0f}s)")
    log(f"leaf chunk 로드 완료: 전체 {n_seen}건 중 leaf {len(out)}건 ({time.time()-t0:.0f}s)")
    return out


def load_dense_into_store(
    emb_dir: Path, chunks: list[ChunkSchema], store: QdrantVectorStore,
    *, skip_shards: set[str] | None = None, log=print,
) -> int:
    """dense_*.npz 샤드를 순회하며 QdrantVectorStore에 직접 upsert한다.
    Kim의 index_bundle._load_dense()와 동일한 전략(샤드 단위로 처리, 전체를
    한 번에 메모리에 올리지 않음) — 626,497건 x 1024차원을 감당하려면 필수.
    """
    import numpy as np

    skip_shards = skip_shards or set()
    by_id = {c.chunk_id: c for c in chunks}
    shards = sorted(emb_dir.glob("dense_*.npz"))
    t0 = time.time()
    total = 0
    STEP = 2000
    for sh in shards:
        if sh.name in skip_shards:
            log(f"  스킵(손상 확인됨): {sh.name}")
            continue
        z = np.load(sh, allow_pickle=True)
        ids, vecs = z["chunk_ids"], z["vectors"]
        for b in range(0, len(ids), STEP):
            cs, vs = [], []
            for cid, v in zip(ids[b:b + STEP], vecs[b:b + STEP]):
                c = by_id.get(str(cid))
                if c is not None:
                    cs.append(c)
                    vs.append(v.astype("float32").tolist())
            if cs:
                store.upsert_chunks(cs, vs)
                total += len(cs)
            del cs, vs
        del z, ids, vecs
        log(f"  {sh.name} 적재 완료 (누적 {total}건, {time.time()-t0:.0f}s)")
    log(f"dense 적재 완료: {total}건 ({time.time()-t0:.0f}s)")
    return total


if __name__ == "__main__":
    # self-test: 실제 artifacts_v2에서 몇 건만 로드해 스키마 호환 확인
    import sys

    REPO_ROOT = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(REPO_ROOT / "src"))
    ARTIFACTS = REPO_ROOT / "pipeline_kim_v2" / "artifacts_v2"

    n = 0
    for rec in iter_chunk_records(ARTIFACTS / "l1"):
        rec.pop("field_codes", None)
        rec.pop("table_ids", None)
        rec["text"] = render_search_text(
            company=rec.get("company"), report_name=rec.get("report_name"),
            period=rec.get("period"), section_path=rec.get("section_path") or [],
            body_text=rec.get("raw_text", ""),
        )
        c = ChunkSchema(**rec)
        assert c.table_id is None
        assert c.semantic_groups == []
        assert c.metric_hints == []
        assert c.table_chunk_index is None
        n += 1
        if n >= 20:
            break
    print(f"self-test OK: {n}건 ChunkSchema 변환 성공, 신규 필드 기본값 확인됨")
