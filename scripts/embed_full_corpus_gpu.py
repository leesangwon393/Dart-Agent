"""GPU 서버에서 전체 코퍼스(4,204건) BGE-M3 임베딩 (Stage 3 CPU 실측: 592ms/chunk
→ 전체 73시간 추정. GPU 배치 처리로 이 병목을 없앤다).

사용법:
  1. 이 프로젝트 전체(코드 + corpus/, 약 5.2GB)를 GPU 서버로 복사:
       rsync -avz --exclude '.venv' --exclude '.git' "공시 agent/" user@gpu-server:~/공시agent/
  2. GPU 서버에서 환경 구성:
       cd ~/공시agent && uv venv --python 3.11 .venv && uv pip install -e .
       .venv/bin/python -c "import torch; print(torch.cuda.is_available())"
       # False 가 나오면 서버의 CUDA 버전에 맞는 torch 를 다시 설치해야 함:
       # uv pip install torch --index-url https://download.pytorch.org/whl/cu121  (버전은 서버에 맞게)
  3. 실행: .venv/bin/python scripts/embed_full_corpus_gpu.py
     (batch_size 는 VRAM 에 맞춰 아래 GPU_BATCH_SIZE 조정 — 16GB급이면 128 정도가 안전)
  4. 중간에 끊겨도 재실행하면 progress.json 기준으로 이어서 진행됨(shard 단위 체크포인트).
  5. 결과를 다시 로컬로:
       rsync -avz user@gpu-server:~/공시agent/gpu_embeddings/ ./gpu_embeddings/
     shard_XXXX.pkl 파일들을 그대로 로컬에서 로드해서 QdrantVectorStore.upsert_chunks() 에
     이어붙이면 된다(하나로 합칠 필요 없음 — 메모리 절약을 위해 오히려 안 합치는 걸 권장).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

logging.basicConfig(level=logging.WARNING)

from disclosure_rag.pipeline import build_retrieval_chunks
from disclosure_rag.retrieval.embeddings import build_embedding_provider

OUT_DIR = "gpu_embeddings"
SHARD_SIZE = 20_000  # 이 개수마다 파일 하나 + 체크포인트 기록
GPU_BATCH_SIZE = 128  # CPU 기본값(16)보다 훨씬 크게 — VRAM 부족하면 낮출 것
DEVICE = "cuda"

os.makedirs(OUT_DIR, exist_ok=True)
progress_path = os.path.join(OUT_DIR, "progress.json")
chunks_cache_path = os.path.join(OUT_DIR, "_all_chunks.pkl")


def main() -> None:
    import pickle

    if os.path.exists(chunks_cache_path):
        print("캐시된 파싱 결과 재사용...", flush=True)
        chunks = pickle.load(open(chunks_cache_path, "rb"))
    else:
        print("전체 코퍼스 파싱+청킹 중 (약 9분 예상, 4,204건)...", flush=True)
        t0 = time.time()
        chunks = build_retrieval_chunks("corpus")
        print(f"leaf chunks: {len(chunks)} ({time.time() - t0:.0f}s)", flush=True)
        pickle.dump(chunks, open(chunks_cache_path, "wb"))

    provider = build_embedding_provider("bge-m3", device=DEVICE)
    print(f"embedding provider ready (device={DEVICE})", flush=True)

    # 사전 벤치마크: 실제 GPU 처리량을 먼저 재서 전체 예상 시간을 알려준다
    # (CPU 실측 592ms/chunk 기준 전체 76.8시간 — GPU 종류에 따라 편차가 커서
    # 무작정 몇 시간을 태우기 전에 실측치로 ETA 를 먼저 확인한다).
    bench_n = min(200, len(chunks))
    t0 = time.time()
    provider.embed([c.text for c in chunks[:bench_n]], batch_size=GPU_BATCH_SIZE)
    bench_elapsed = time.time() - t0
    bench_ms = bench_elapsed / bench_n * 1000
    print(
        f"[벤치마크] {bench_n}건 {bench_elapsed:.1f}s -> {bench_ms:.2f}ms/chunk "
        f"(CPU 대비 {591.7/bench_ms:.1f}배) | 전체 {len(chunks)}건 예상 시간: "
        f"{len(chunks)*bench_ms/1000/60:.1f}분",
        flush=True,
    )

    start_idx = 0
    if os.path.exists(progress_path):
        start_idx = json.load(open(progress_path))["next_idx"]
        print(f"이어서 진행: idx={start_idx} 부터 (총 {len(chunks)}건)", flush=True)

    total_t0 = time.time()
    for shard_start in range(start_idx, len(chunks), SHARD_SIZE):
        shard = chunks[shard_start : shard_start + SHARD_SIZE]
        t0 = time.time()
        vectors = provider.embed([c.text for c in shard], batch_size=GPU_BATCH_SIZE)
        elapsed = time.time() - t0
        shard_idx = shard_start // SHARD_SIZE
        out_path = os.path.join(OUT_DIR, f"shard_{shard_idx:04d}.pkl")
        pickle.dump({"chunks": shard, "vectors": vectors, "dim": provider.dim}, open(out_path, "wb"))
        ms_per_chunk = elapsed / len(shard) * 1000
        remaining = len(chunks) - (shard_start + len(shard))
        eta_sec = remaining * (ms_per_chunk / 1000)
        print(
            f"shard {shard_idx}: {len(shard)}건 {elapsed:.1f}s ({ms_per_chunk:.2f}ms/chunk, "
            f"CPU 대비 {591.7 / ms_per_chunk:.1f}배) -> {out_path} | 남은 예상 {eta_sec/60:.1f}분",
            flush=True,
        )
        json.dump({"next_idx": shard_start + SHARD_SIZE}, open(progress_path, "w"))

    print(f"\n전체 완료. 총 {time.time() - total_t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
