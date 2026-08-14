"""Fusion (§53): BM25 와 Dense 의 raw score scale 이 다르므로 단순 합산을 쓰지 않고
RRF(Reciprocal Rank Fusion) 를 baseline 으로 쓴다. 교체 가능하도록 인터페이스를
함수 하나로 분리해둔다."""

from __future__ import annotations

from disclosure_rag.chunking.chunk_schema import ChunkSchema

RankedList = list[tuple[ChunkSchema, float]]


def reciprocal_rank_fusion(ranked_lists: list[RankedList], *, k: int = 60, top_k: int | None = None) -> RankedList:
    """각 ranked_list 는 이미 관련도 내림차순으로 정렬돼 있다고 가정한다.
    score(d) = sum_{list 마다 d 가 등장하면} 1 / (k + rank(d))  (rank 는 1-base)."""
    scores: dict[str, float] = {}
    chunk_lookup: dict[str, ChunkSchema] = {}

    for ranked in ranked_lists:
        for rank, (chunk, _orig_score) in enumerate(ranked, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
            chunk_lookup.setdefault(chunk.chunk_id, chunk)

    fused = sorted(
        ((chunk_lookup[cid], s) for cid, s in scores.items()),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return fused[:top_k] if top_k is not None else fused
