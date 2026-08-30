"""Phase 9 회귀 테스트: BGE-M3 embedding provider + Qdrant vector store.

BGE-M3 모델(~2.3GB) 다운로드/로딩이 필요해 다른 테스트보다 느리다.
network/모델 캐시가 없는 환경에서는 skip 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from disclosure_rag.chunking.chunk_schema import filter_leaf_chunks
from disclosure_rag.common.manifest_loader import load_manifest
from disclosure_rag.pipeline import build_all_chunks
from disclosure_rag.retrieval.dense_retriever import DenseRetriever
from disclosure_rag.retrieval.metadata_filter import RetrievalFilter
from disclosure_rag.retrieval.qdrant_store import QdrantVectorStore, build_qdrant_filter

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
pytestmark = pytest.mark.skipif(not CORPUS_ROOT.is_dir(), reason="corpus/ 없음")

SAMPLE_DOC_IDS = {
    "periodic_20240312000736",
    "major_20241118000171",
    "exchange_20250728800035",
    "holding_20241025000530",
}


def _try_load_bge_m3():
    try:
        from disclosure_rag.retrieval.embeddings import BgeM3EmbeddingProvider

        return BgeM3EmbeddingProvider(device="cpu")  # MPS 는 다른 프로세스와 동시 사용 시 OOM 발생 확인됨
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"BGE-M3 모델 로딩 불가 (네트워크/캐시 없음): {e}")


@pytest.fixture(scope="module")
def sample_chunks():
    """검색 인덱스에는 leaf chunk 만 넣는다 (parent 는 매우 길어질 수 있어
    그대로 임베딩하면 비정상적으로 느려짐 — 실측으로 확인된 회귀)."""
    manifest = load_manifest(CORPUS_ROOT)
    rows = [r for r in manifest if r.doc_id in SAMPLE_DOC_IDS]
    all_chunks = build_all_chunks(str(CORPUS_ROOT), rows=rows, validate=False)
    return filter_leaf_chunks(all_chunks)


def test_qdrant_filter_translation():
    flt = RetrievalFilter(companies=["삼성전자"], doc_groups=["major"], latest_only=True)
    qf = build_qdrant_filter(flt)
    assert qf is not None
    keys = {c.key for c in qf.must}
    assert {"company", "report_type", "is_latest"} <= keys


def test_qdrant_filter_translates_report_ids():
    """회귀(2026-08-30): build_qdrant_filter()가 RetrievalFilter.report_ids를
    아예 안 옮기고 있었다 — search_disclosures가 특정 문서를 report_id로
    정확히 지정해도(예: get_latest_report로 doc_id를 알아낸 뒤 그 본문만
    보려는 호출) Dense 쪽 Qdrant 질의에는 그 조건이 통째로 빠져서(must=[]
    -> None) 필터 없이 전체 코퍼스를 검색했다. BM25는 flt.matches()를 매
    후보에 직접 호출해 정상 필터링됐으므로, RRF로 합쳐지면 Dense가 끌고
    온 다른 문서의 chunk가 최종 결과에 섞여 들어왔다(사용자 실측 보고)."""
    flt = RetrievalFilter(report_ids=["periodic_20260515001572"])
    qf = build_qdrant_filter(flt)
    assert qf is not None, "report_ids만 있어도 필터가 None이 되면 안 됨"
    keys = {c.key for c in qf.must}
    assert "report_id" in keys


def test_qdrant_filter_none_when_empty():
    assert build_qdrant_filter(None) is None
    assert build_qdrant_filter(RetrievalFilter()) is None


def test_qdrant_in_memory_upsert_and_search():
    """실제 embedding 없이 dummy 벡터로 Qdrant store 배선만 검증 (빠름)."""
    from disclosure_rag.chunking.chunk_schema import ChunkSchema

    chunks = [
        ChunkSchema(
            chunk_id="c1", report_id="r1", text="t1", raw_text="t1",
            company="삼성전자", report_type="major", is_correction=False, is_latest=True,
        ),
        ChunkSchema(
            chunk_id="c2", report_id="r2", text="t2", raw_text="t2",
            company="SK하이닉스", report_type="major", is_correction=False, is_latest=True,
        ),
    ]
    vectors = [[1.0, 0.0], [0.0, 1.0]]
    store = QdrantVectorStore(dim=2, in_memory=True, collection_name="test")
    store.upsert_chunks(chunks, vectors)

    results = store.search([1.0, 0.0], k=2)
    assert results[0][0] == "c1"

    flt = RetrievalFilter(companies=["SK하이닉스"])
    results_filtered = store.search([1.0, 0.0], k=2, flt=flt)
    assert [r[0] for r in results_filtered] == ["c2"]


def test_qdrant_search_scoped_to_specific_report_id_excludes_other_documents():
    """회귀(2026-08-30): 위 필터 변환 버그가 실제 검색 결과에도 영향을
    미쳤는지 end-to-end로 확인 — 두 문서(r1/r2)가 섞인 인덱스에서 r1만
    report_id로 지정해 검색하면 r2 chunk가 하나도 섞이면 안 된다. 버그가
    있었을 때는(패치 전) report_ids 필터가 무시돼 두 문서가 다 나왔다."""
    from disclosure_rag.chunking.chunk_schema import ChunkSchema

    chunks_r1 = [
        ChunkSchema(chunk_id=f"r1::c{i}", report_id="r1", text=f"t{i}", raw_text=f"t{i}",
                    company="삼성전자", report_type="periodic", is_correction=False, is_latest=True)
        for i in range(15)
    ]
    chunks_r2 = [
        ChunkSchema(chunk_id=f"r2::c{i}", report_id="r2", text=f"t{i}", raw_text=f"t{i}",
                    company="삼성전자", report_type="periodic", is_correction=False, is_latest=True)
        for i in range(15)
    ]
    chunks = chunks_r1 + chunks_r2
    vectors = [[1.0, 0.0]] * len(chunks_r1) + [[1.0, 0.0]] * len(chunks_r2)  # 전부 같은 방향 -> 필터 없으면 섞여 나옴
    store = QdrantVectorStore(dim=2, in_memory=True, collection_name="test_scope")
    store.upsert_chunks(chunks, vectors)

    flt = RetrievalFilter(report_ids=["r1"])
    results = store.search([1.0, 0.0], k=10, flt=flt)
    assert len(results) == 10
    assert all(chunk_id.startswith("r1::") for chunk_id, _score in results), \
        f"r1만 지정했는데 다른 문서 chunk가 섞여 나옴: {results}"


def test_search_disclosures_tool_with_report_id_stays_within_single_document():
    """End-to-end 회귀(2026-08-30): 실제 사용자 시나리오 재현 — Agent가
    get_latest_report 등으로 report_id를 이미 알아낸 뒤 search_disclosures를
    report_id로 호출하면, BM25+Dense를 합친 HybridRetriever 최종 결과가
    전부 그 문서(report_id) 안에서만 나와야 하고 10개가 나와야 한다."""
    from disclosure_rag.agent.tools import make_search_disclosures_tool
    from disclosure_rag.chunking.chunk_schema import ChunkSchema
    from disclosure_rag.retrieval.bm25_retriever import BM25Retriever
    from disclosure_rag.retrieval.dense_retriever import DenseRetriever
    from disclosure_rag.retrieval.hybrid_retriever import HybridRetriever
    from disclosure_rag.retrieval.tokenizers import WhitespaceTokenizer

    target_texts = [f"삼성전자 영업이익 항목 {i} 매출액 관련 내용" for i in range(15)]
    other_texts = [f"삼성전자 영업이익 항목 {i} 매출액 관련 내용" for i in range(15)]  # 의도적으로 텍스트까지 동일

    target_chunks = [
        ChunkSchema(chunk_id=f"target::c{i}", report_id="target", text=t, raw_text=t,
                    company="삼성전자", report_type="periodic", is_correction=False, is_latest=True)
        for i, t in enumerate(target_texts)
    ]
    other_chunks = [
        ChunkSchema(chunk_id=f"other::c{i}", report_id="other", text=t, raw_text=t,
                    company="삼성전자", report_type="periodic", is_correction=False, is_latest=True)
        for i, t in enumerate(other_texts)
    ]
    all_chunks = target_chunks + other_chunks

    bm25 = BM25Retriever(all_chunks, WhitespaceTokenizer())
    store = QdrantVectorStore(dim=2, in_memory=True, collection_name="test_tool_scope")
    # 모든 chunk를 같은 방향 벡터로 넣어 dense 필터가 실제로 안 걸리면
    # target/other가 반반씩 섞여 나오도록 만든다(누락 시 바로 드러남).
    vectors = [[1.0, 0.0]] * len(all_chunks)
    store.upsert_chunks(all_chunks, vectors)

    class _FixedEmbeddingProvider:
        def embed_query(self, query: str) -> list[float]:
            return [1.0, 0.0]

    dense = DenseRetriever(all_chunks, _FixedEmbeddingProvider(), store)
    retriever = HybridRetriever(bm25, dense)

    tool = make_search_disclosures_tool(retriever, expand_table_siblings=False)
    result = tool.handler(query="매출액", report_id="target")

    report_ids_in_result = {r["report_id"] for r in result["results"]}
    assert report_ids_in_result == {"target"}, f"target 문서만 지정했는데 다른 문서가 섞임: {report_ids_in_result}"
    assert len(result["results"]) == 10


@pytest.mark.slow
def test_bge_m3_dense_retriever_finds_relevant_chunk(sample_chunks):
    provider = _try_load_bge_m3()
    store = QdrantVectorStore(dim=provider.dim, in_memory=True, collection_name="test_dense")
    retriever = DenseRetriever.build(sample_chunks, provider, store)

    # semantic query: "R&D 에 얼마 썼어" 는 "연구개발비" 와 표현이 다르지만
    # 의미가 같아야 Dense 가 잡아내야 한다 (§33).
    results = retriever.search("R&D에 얼마나 투자했어?", k=5)
    assert results
    texts = [c.raw_text for c, _ in results]
    assert any("연구개발" in t for t in texts), f"Dense retrieval 이 의미 매칭 실패: {texts}"
