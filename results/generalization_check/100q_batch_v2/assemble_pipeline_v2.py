"""100문항 배치 v2 전용: production ask() 파이프라인을 "전체 코퍼스"(70개사,
4,204문서, 626,497 leaf chunk, Kim이 재청킹+재임베딩한 pipeline_kim_v2/artifacts_v2)
로 조립한다.

v1(results/generalization_check/100q_batch/assemble_pipeline.py, 2026-08-18)과
**검색 스택 설계는 동일하게 유지한다** — 목적은 "이번 주 고친 것들(파싱버그,
semantic block, entity extraction 확장, CascadingRouter 배선, [YEAR] placeholder)
의 순수 효과"를 확인하는 것이라 검색 스택 자체를 바꾸면 비교가 무의미해진다:
- retriever: Kiwi BM25 + Dense(BGE-M3) fusion(RRF), reranker 없음
- Kim의 index_bundle.load_bundle()(sparse + normalized_weighted_fusion)은 쓰지
  않는다 — sparse/weighted fusion 도입은 별도의 나중 결정(PROJECT_STATE §10).
- router: CascadingRouter(build_cascading_router)
- agent 모델 = HCX-007, answer 모델 = HCX-005 (분리)

v1과 다른 점은 딱 하나: chunk/vector 출처가 "5~8개사 subset 캐시
(/tmp/hundred_q_vectors.pkl)"가 아니라 "Kim이 만든 전체 코퍼스 아티팩트"라는 것.
kim_v2_loader.py(같은 디렉터리, 우리가 새로 작성 — Kim 리포 코드는 import 안 함)
로 l1/chunks.jsonl.gz + emb/dense_*.npz를 읽는다.

알려진 손상: emb/sparse_0005.jsonl.gz gzip CRC 오류(사용자 확인, 재전송 요청 안 함).
sparse는 애초에 이 설계에서 안 쓰므로 영향 없음. dense는 32개 샤드 전부 정상
(626,497건 = leaf 전체와 정확히 일치, 사전 검증 완료).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BATCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BATCH_DIR.parents[2]
CORPUS_ROOT = REPO_ROOT / "corpus"
CONFIG_ROOT = REPO_ROOT / "config"
ENV_PATH = REPO_ROOT / ".env"
ARTIFACTS_ROOT = REPO_ROOT / "pipeline_kim_v2" / "artifacts_v2"
L1_DIR = ARTIFACTS_ROOT / "l1"
EMB_DIR = ARTIFACTS_ROOT / "emb"
DENSE_DIM = 1024  # BGE-M3

sys.path.insert(0, str(BATCH_DIR))
sys.path.insert(0, str(REPO_ROOT / "src"))


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def assemble():
    from kim_v2_loader import load_dense_into_store, load_leaf_chunks

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
    from disclosure_rag.router.hcx_router import build_cascading_router

    t0 = time.time()
    log(f"l1 chunk 로드 시작: {L1_DIR}")
    chunks = load_leaf_chunks(L1_DIR, log=log)
    log(f"leaf chunks={len(chunks)} ({time.time()-t0:.0f}s)")

    log("Kiwi tokenizer + BM25 인덱스 구축 시작 (전체 코퍼스, 626K 규모)")
    t1 = time.time()
    tok = build_tokenizer("kiwi", user_dict_path=CONFIG_ROOT / "financial_terms.txt")
    bm25 = BM25Retriever(chunks, tok)
    bm25_elapsed = time.time() - t1
    log(f"BM25 인덱스 구축 완료 ({bm25_elapsed:.0f}s) — 회귀 체크: 237K건 기준 3~4분이 "
        f"기존 실측이었다. 626K는 약 2.6배 규모이므로 선형이면 ~8~11분대가 정상 범위.")

    log("BGE-M3 embedding provider 로드 (query 임베딩 + router 인코더 겸용)")
    t2 = time.time()
    embed_provider = BgeM3EmbeddingProvider()
    log(f"embedding provider 로드 완료 ({time.time()-t2:.0f}s)")

    log(f"Dense store(in-memory Qdrant, dim={DENSE_DIM}) 에 emb/dense_*.npz 적재 시작")
    t3 = time.time()
    store = QdrantVectorStore(in_memory=True, dim=DENSE_DIM)
    n_dense = load_dense_into_store(EMB_DIR, chunks, store, log=log)
    dense = DenseRetriever(chunks, embed_provider, store)
    log(f"Dense store 준비 완료: {n_dense}건 적재 ({time.time()-t3:.0f}s)")
    if n_dense != len(chunks):
        log(f"  경고: dense 벡터 수({n_dense})가 leaf chunk 수({len(chunks)})와 다름 — "
            f"차이 {len(chunks) - n_dense}건은 BM25 단독으로만 검색됨")

    retriever = HybridRetriever(bm25, dense, reranker=None)

    log("manifest / correction index 구축 (전체 코퍼스, tool 조립용)")
    t4 = time.time()
    manifest = load_manifest(CORPUS_ROOT)
    resolver = PathResolver(CORPUS_ROOT)
    correction_index = build_correction_index(manifest, resolver)
    log(f"manifest={len(manifest)}건 correction_index={len(correction_index)}건 ({time.time()-t4:.0f}s)")

    tools = build_all_tools(retriever, manifest, correction_index)
    extractor = EntityExtractor(
        corpus_root=CORPUS_ROOT,
        metric_terms_path=CONFIG_ROOT / "metric_terms.txt",
        event_terms_path=CONFIG_ROOT / "event_terms.txt",
        ownership_terms_path=CONFIG_ROOT / "ownership_terms.txt",
    )

    class RetryableHCXClient(HCXClient):
        """100문항 연속 호출 특성상 429(rate limit) 대비 max_retries 를 6으로
        올린다 — hcx_client.py 자체는 건드리지 않고 chat() 기본값만 override."""

        def chat(self, *args, **kwargs):
            kwargs.setdefault("max_retries", 6)
            return super().chat(*args, **kwargs)

    agent_client = RetryableHCXClient(env_path=ENV_PATH)  # .env 의 HCX_MODEL=HCX-007
    answer_client = RetryableHCXClient(env_path=ENV_PATH, model="HCX-005")
    log(f"agent_client.model={agent_client.model} answer_client.model={answer_client.model}")

    log("CascadingRouter 구축 (semantic margin 게이팅 + HCX escalation)")
    t5 = time.time()
    router = build_cascading_router(embed_provider, agent_client)
    log(f"Router 구축 완료 ({time.time()-t5:.0f}s)")

    log(f"파이프라인 조립 총 소요 {time.time()-t0:.0f}s")
    return {
        "agent_client": agent_client,
        "answer_client": answer_client,
        "tools": tools,
        "extractor": extractor,
        "router": router,
        "bm25_elapsed": bm25_elapsed,
        "n_leaf_chunks": len(chunks),
        "n_dense_vectors": n_dense,
    }
