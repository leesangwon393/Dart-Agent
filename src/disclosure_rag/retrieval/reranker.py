"""Reranker (§54): Hybrid Retrieval Top-N 후보를 Query+Candidate 쌍으로 정밀
재평가한다. optional 로 만들어 "Hybrid only" vs "Hybrid + Reranker" 비교가
가능하게 한다 (§74)."""

from __future__ import annotations

from typing import Protocol

from disclosure_rag.chunking.chunk_schema import ChunkSchema

Candidates = list[tuple[ChunkSchema, float]]


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, candidates: Candidates, *, top_k: int = 5) -> Candidates: ...


class CrossEncoderReranker:
    name = "bge-reranker-v2-m3"

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", device: str | None = None):
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name, device=device)

    def rerank(self, query: str, candidates: Candidates, *, top_k: int = 5) -> Candidates:
        if not candidates:
            return []
        pairs = [(query, c.text) for c, _ in candidates]
        scores = self._model.predict(pairs)
        reranked = sorted(
            zip((c for c, _ in candidates), scores), key=lambda pair: pair[1], reverse=True,
        )
        return [(c, float(s)) for c, s in reranked[:top_k]]


class NoOpReranker:
    """Reranker 를 끈 baseline (Hybrid only) 비교용."""

    name = "none"

    def rerank(self, query: str, candidates: Candidates, *, top_k: int = 5) -> Candidates:
        return candidates[:top_k]
