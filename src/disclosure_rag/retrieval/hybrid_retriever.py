"""§52 Hybrid Retrieval 구조: Metadata Filter -> (BM25 + Dense) -> Fusion -> (선택) Reranker.
Dense/Reranker 를 빼면 자동으로 "BM25 only" / "Hybrid only" 로 동작해 §74 평가 매트릭스를
그대로 지원한다."""

from __future__ import annotations

from disclosure_rag.chunking.chunk_schema import ChunkSchema
from disclosure_rag.retrieval.bm25_retriever import BM25Retriever
from disclosure_rag.retrieval.dense_retriever import DenseRetriever
from disclosure_rag.retrieval.fusion import reciprocal_rank_fusion
from disclosure_rag.retrieval.metadata_filter import RetrievalFilter
from disclosure_rag.retrieval.reranker import Reranker


class HybridRetriever:
    def __init__(
        self,
        bm25: BM25Retriever,
        dense: DenseRetriever | None = None,
        reranker: Reranker | None = None,
    ):
        self.bm25 = bm25
        self.dense = dense
        self.reranker = reranker

    def search(
        self,
        query: str,
        *,
        k: int = 10,
        flt: RetrievalFilter | None = None,
        bm25_k: int = 50,
        dense_k: int = 50,
        rrf_k: int = 60,
        rerank_top_n: int = 50,
    ) -> list[tuple[ChunkSchema, float]]:
        ranked_lists = [self.bm25.search(query, k=bm25_k, flt=flt)]
        if self.dense is not None:
            ranked_lists.append(self.dense.search(query, k=dense_k, flt=flt))

        fused = reciprocal_rank_fusion(ranked_lists, k=rrf_k, top_k=max(k, rerank_top_n))

        if self.reranker is not None:
            return self.reranker.rerank(query, fused, top_k=k)
        return fused[:k]
