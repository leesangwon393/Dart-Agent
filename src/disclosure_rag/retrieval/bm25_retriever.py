"""BM25S baseline retriever (§8, §32, §51~52).

Metadata Filtering 은 §51 대로 "Coarse-to-Fine" 로 구현한다: 정확한 field-filter
가 가능한 inverted index 를 새로 만드는 대신(baseline 이므로), BM25S 전체
인덱스에서 넉넉히 overfetch 한 뒤 metadata 로 post-filter 하고 top-k 를 자른다.
후보가 부족하면 overfetch 배수를 늘려 재시도한다.
"""

from __future__ import annotations

import logging

import bm25s

from disclosure_rag.chunking.chunk_schema import ChunkSchema
from disclosure_rag.retrieval.metadata_filter import RetrievalFilter
from disclosure_rag.retrieval.tokenizers import Tokenizer

logger = logging.getLogger(__name__)


class BM25Retriever:
    def __init__(self, chunks: list[ChunkSchema], tokenizer: Tokenizer):
        self.tokenizer = tokenizer
        self.chunks_by_id = {c.chunk_id: c for c in chunks}
        self._ids = [c.chunk_id for c in chunks]

        corpus_tokens = [tokenizer.tokenize(c.text) for c in chunks]
        self._bm25 = bm25s.BM25(corpus=self._ids)
        self._bm25.index(corpus_tokens, show_progress=False)

    def search(
        self,
        query: str,
        *,
        k: int = 10,
        flt: RetrievalFilter | None = None,
        overfetch_multiplier: int = 20,
        max_overfetch: int = 2000,
    ) -> list[tuple[ChunkSchema, float]]:
        query_tokens = self.tokenizer.tokenize(query)
        if not query_tokens:
            logger.warning("[BM25] query 토큰화 결과가 비어있음: %r", query)
            return []

        fetch_k = min(max(k * overfetch_multiplier, k), max_overfetch, len(self._ids))
        while True:
            ids, scores = self._bm25.retrieve([query_tokens], k=fetch_k, show_progress=False)
            candidates = list(zip(ids[0].tolist(), scores[0].tolist()))

            results: list[tuple[ChunkSchema, float]] = []
            for chunk_id, score in candidates:
                chunk = self.chunks_by_id.get(chunk_id)
                if chunk is None or score <= 0:
                    continue
                if flt is not None and not flt.matches(chunk):
                    continue
                results.append((chunk, score))
                if len(results) >= k:
                    return results

            if fetch_k >= min(max_overfetch, len(self._ids)):
                return results
            fetch_k = min(fetch_k * 4, max_overfetch, len(self._ids))
