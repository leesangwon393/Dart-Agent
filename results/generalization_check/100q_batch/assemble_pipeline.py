"""100문항 배치 전용: production ask() 파이프라인을 조립한다.

최종 확정 baseline(PROJECT_STATE §8) 그대로:
- retriever: Kiwi BM25 + Dense(BGE-M3) fusion (reranker 없음)
- chunking: 이미 GPU 임베딩 캐시(/tmp/hundred_q_vectors.pkl)에 leaf chunk 로
  들어있음 — 재청킹하지 않고 그대로 사용
- router: semantic_router_wrapper.SemanticRouterAdapter
- agent 모델 = HCX-007 (.env 의 HCX_MODEL), answer 모델 = HCX-005
  (ask()의 answer_client 파라미터로 분리, 2026-08-18 추가)
"""
from __future__ import annotations

import pickle
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_ROOT = REPO_ROOT / "corpus"
CONFIG_ROOT = REPO_ROOT / "config"
ENV_PATH = REPO_ROOT / ".env"
VECTOR_CACHE = Path("/tmp/hundred_q_vectors.pkl")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def assemble():
    import sys
    sys.path.insert(0, str(REPO_ROOT / "src"))

    from disclosure_rag.agent.hcx_client import HCXClient
    from disclosure_rag.agent.tools import build_all_tools
    from disclosure_rag.common.manifest_loader import load_manifest
    from disclosure_rag.common.unicode_utils import PathResolver
    from disclosure_rag.correction.correction_graph_builder import build_correction_index
    from disclosure_rag.entity.entity_extractor import EntityExtractor
    from disclosure_rag.retrieval.bm25_retriever import BM25Retriever
    from disclosure_rag.retrieval.dense_retriever import DenseRetriever
    from disclosure_rag.retrieval.embeddings import BgeM3EmbeddingProvider
    from disclosure_rag.retrieval.hybrid_retriever import HybridRetriever
    from disclosure_rag.retrieval.qdrant_store import QdrantVectorStore
    from disclosure_rag.retrieval.tokenizers import build_tokenizer
    from disclosure_rag.router.semantic_router_wrapper import SemanticRouterAdapter

    t0 = time.time()
    log(f"캐시 로드: {VECTOR_CACHE}")
    with open(VECTOR_CACHE, "rb") as f:
        cache = pickle.load(f)
    chunks, vectors, dim = cache["chunks"], cache["vectors"], cache["dim"]
    log(f"chunks={len(chunks)} dim={dim} ({time.time()-t0:.0f}s)")

    log("Kiwi tokenizer + BM25 인덱스 구축 시작")
    t1 = time.time()
    tok = build_tokenizer("kiwi", user_dict_path=CONFIG_ROOT / "financial_terms.txt")
    bm25 = BM25Retriever(chunks, tok)
    bm25_elapsed = time.time() - t1
    log(f"BM25 인덱스 구축 완료 ({bm25_elapsed:.0f}s)")

    log("BGE-M3 embedding provider 로드 (query 임베딩 + router 인코더 겸용)")
    t2 = time.time()
    embed_provider = BgeM3EmbeddingProvider()
    log(f"embedding provider 로드 완료 ({time.time()-t2:.0f}s)")

    log("Dense store(in-memory Qdrant) 에 캐시된 벡터 upsert")
    t3 = time.time()
    store = QdrantVectorStore(in_memory=True, dim=dim)
    store.upsert_chunks(chunks, vectors)
    dense = DenseRetriever(chunks, embed_provider, store)
    log(f"Dense store 준비 완료 ({time.time()-t3:.0f}s)")

    retriever = HybridRetriever(bm25, dense, reranker=None)

    log("manifest / correction index 구축 (전체 코퍼스, tool 조립용)")
    t4 = time.time()
    manifest = load_manifest(CORPUS_ROOT)
    resolver = PathResolver(CORPUS_ROOT)
    correction_index = build_correction_index(manifest, resolver)
    log(f"manifest={len(manifest)}건 correction_index={len(correction_index)}건 ({time.time()-t4:.0f}s)")

    tools = build_all_tools(retriever, manifest, correction_index)
    extractor = EntityExtractor(corpus_root=CORPUS_ROOT, metric_terms_path=CONFIG_ROOT / "metric_terms.txt")

    log("Semantic Router 구축")
    t5 = time.time()
    router = SemanticRouterAdapter(embed_provider)
    log(f"Router 구축 완료 ({time.time()-t5:.0f}s)")

    class RetryableHCXClient(HCXClient):
        """100문항 연속 호출 특성상 429(rate limit) 대비 max_retries 를 6으로
        올린다(README/작업지시 요구사항) — hcx_client.py 자체는 건드리지 않고
        chat() 기본값만 이 배치 스크립트 안에서 override."""

        def chat(self, *args, **kwargs):
            kwargs.setdefault("max_retries", 6)
            return super().chat(*args, **kwargs)

    agent_client = RetryableHCXClient(env_path=ENV_PATH)  # .env 의 HCX_MODEL=HCX-007
    answer_client = RetryableHCXClient(env_path=ENV_PATH, model="HCX-005")
    log(f"agent_client.model={agent_client.model} answer_client.model={answer_client.model}")

    log(f"파이프라인 조립 총 소요 {time.time()-t0:.0f}s")
    return {
        "agent_client": agent_client,
        "answer_client": answer_client,
        "tools": tools,
        "extractor": extractor,
        "router": router,
        "bm25_elapsed": bm25_elapsed,
    }
