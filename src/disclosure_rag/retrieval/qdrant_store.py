"""Dense Vector 저장소 (사용자 결정 #6): Qdrant.

chunk vector 와 metadata payload 를 같이 저장해 company/period/report_type/
correction 상태 등으로 filter 가 가능하게 한다. 서버 없이도 쓸 수 있게 embedded
(local path 또는 in-memory) 모드를 기본으로 지원한다.
"""

from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    Range,
    VectorParams,
)

from disclosure_rag.chunking.chunk_schema import ChunkSchema
from disclosure_rag.retrieval.metadata_filter import RetrievalFilter

_NAMESPACE = uuid.UUID("d15c105e-0000-4000-8000-000000000000")  # "disclosure" 고정 namespace


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


def _payload_of(chunk: ChunkSchema) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "report_id": chunk.report_id,
        "parent_chunk_id": chunk.parent_chunk_id,
        "company": chunk.company,
        "corp_code": chunk.corp_code,
        "report_type": chunk.report_type,
        "report_subtype": chunk.report_subtype,
        "report_name": chunk.report_name,
        "period": chunk.period,
        "filing_date": chunk.filing_date,
        "filing_date_int": int(chunk.filing_date) if chunk.filing_date else None,
        "section_path": chunk.section_path,
        "content_type": chunk.content_type,
        "is_correction": chunk.is_correction,
        "correction_group_id": chunk.correction_group_id,
        "correction_order": chunk.correction_order,
        "is_latest": chunk.is_latest,
    }


def build_qdrant_filter(flt: RetrievalFilter | None) -> Filter | None:
    if flt is None:
        return None
    must: list[FieldCondition] = []
    # 2026-08-30 버그 수정: report_ids 조건이 여기 빠져 있었다 — search_disclosures가
    # 특정 문서를 report_id로 정확히 지정해도(RetrievalFilter(report_ids=[...])),
    # BM25Retriever.search()는 매 후보마다 flt.matches()를 직접 호출해 올바르게
    # 걸러졌지만, Dense 쪽은 이 함수가 만든 Filter로만 Qdrant에 질의하는데 여기에
    # report_ids 매핑이 없어 조건이 통째로 빠졌다(must=[]면 build_qdrant_filter가
    # None을 반환) — 그래서 Dense 검색은 필터 없이 전체 코퍼스를 뒤졌고, RRF로
    # BM25(정상 필터링됨) 결과와 합쳐지면서 다른 문서의 chunk가 섞여 들어왔다.
    if flt.report_ids:
        must.append(FieldCondition(key="report_id", match=MatchAny(any=flt.report_ids)))
    if flt.companies:
        must.append(FieldCondition(key="company", match=MatchAny(any=flt.companies)))
    if flt.doc_groups:
        must.append(FieldCondition(key="report_type", match=MatchAny(any=flt.doc_groups)))
    if flt.doc_subtypes:
        must.append(FieldCondition(key="report_subtype", match=MatchAny(any=flt.doc_subtypes)))
    if flt.periods:
        must.append(FieldCondition(key="period", match=MatchAny(any=flt.periods)))
    if flt.filing_date_from or flt.filing_date_to:
        must.append(
            FieldCondition(
                key="filing_date_int",
                range=Range(
                    gte=int(flt.filing_date_from) if flt.filing_date_from else None,
                    lte=int(flt.filing_date_to) if flt.filing_date_to else None,
                ),
            )
        )
    if flt.latest_only:
        must.append(FieldCondition(key="is_latest", match=MatchValue(value=True)))
    if not flt.include_corrections:
        must.append(FieldCondition(key="is_correction", match=MatchValue(value=False)))
    return Filter(must=must) if must else None


class QdrantVectorStore:
    def __init__(
        self,
        *,
        collection_name: str = "disclosure_chunks",
        dim: int = 1024,
        path: str | None = None,
        url: str | None = None,
        in_memory: bool = False,
    ):
        if in_memory:
            self.client = QdrantClient(":memory:")
        elif url:
            self.client = QdrantClient(url=url)
        else:
            self.client = QdrantClient(path=path or "./qdrant_data")
        self.collection_name = collection_name
        self.dim = dim
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
            )

    def upsert_chunks(
        self, chunks: list[ChunkSchema], vectors: list[list[float]], *, batch_size: int = 256,
    ) -> None:
        assert len(chunks) == len(vectors)
        points = [
            PointStruct(id=_point_id(c.chunk_id), vector=v, payload=_payload_of(c))
            for c, v in zip(chunks, vectors)
        ]
        for i in range(0, len(points), batch_size):
            self.client.upsert(collection_name=self.collection_name, points=points[i:i + batch_size])

    def search(
        self, query_vector: list[float], *, k: int = 10, flt: RetrievalFilter | None = None,
    ) -> list[tuple[str, float]]:
        """chunk_id, score 쌍을 반환한다 (chunk 본체는 caller 가 별도 lookup)."""
        result = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=k,
            query_filter=build_qdrant_filter(flt),
            with_payload=True,
        )
        return [(pt.payload["chunk_id"], pt.score) for pt in result.points]
