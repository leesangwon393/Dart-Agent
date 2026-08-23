"""Phase 13: 전체 코퍼스 재임베딩 — 로컬 Apple Silicon MPS 사용.

PROJECT_STATE.md §7 의 GPU 서버(mileb-v100)는 이 세션 환경에 SSH 인증키가
없어 접근 불가 — 원격 서버 접속을 시도하지 않고, 이 Mac 의 MPS(Apple
Silicon GPU)로 대신한다.

**실측 이슈(2026-08-24) 및 대응**: 초회 실행에서 MPS
`Insufficient Memory (kIOGPUCommandBufferCallbackErrorOutOfMemory)` 로
크래시. 원인 조사 결과 corpus 안에 표 셀 하나에 긴 각주성 텍스트가 통째로
들어간 극단적으로 큰 chunk 가 소수 존재함(최대 153,345자 — 신한지주
관계기업투자주식 현황 각주 등). 이건 **이번 표 chunking 수정과 무관한
기존(pre-existing) 데이터 특성**이다 — 옛 gpu_embeddings/ 샤드에도 동일한
크기의 outlier chunk 가 이미 있었음을 확인(60,000건 샘플 중 48건 >10,000자,
최대 30,013자). 표 안 한 셀(cell) 자체가 원래 크므로 render_table_node 는
행(row) 단위로만 나눌 수 있어 옛 로직/새 로직 둘 다 이 한 행을 더 쪼갤 수
없다 — 이번 세션 범위 밖의 별도 이슈로 PROJECT_STATE.md §12 TODO 에 기록.

이 스크립트는 그 자체를 고치는 대신(코어 chunking 로직은 이미 검증
완료라 건드리지 않음), **임베딩 단계에서만** 방어적으로 대응한다:
- 임베딩 입력 텍스트를 `EMBED_MAX_CHARS` 로 잘라서 모델에 넣는다(저장되는
  ChunkSchema.text/raw_text 자체는 원본 그대로 — BM25 나 화면 표시용
  원문은 손실 없음, dense 벡터만 앞부분 기준으로 계산됨).
- 그래도 실패하면 배치를 반으로 쪼개 재시도(exponential backoff), 그래도
  실패하는 개별 chunk 는 `failed_chunk_ids.jsonl` 에 기록하고 zero-vector
  로 스킵(전체 run 이 죽지 않게).

기존 gpu_embeddings/ 는 덮어쓰지 않고 gpu_embeddings_v2/ 에 새로 저장한다.
재개 가능: gpu_embeddings_v2/progress.json 의 next_idx 부터 이어서 진행.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logging.basicConfig(level=logging.WARNING)

from disclosure_rag.retrieval.embeddings import build_embedding_provider

OUT_DIR = "gpu_embeddings_v2"
# 실측(2026-08-24): batch_size=8, clip=6000자 기준 약 4.5~5 items/sec(모델
# warm-up 이후 안정화 추정치보다 느림 — MPS 가 GPU 서버보다 훨씬 느리다).
# SHARD_SIZE=1000 이면 shard 하나가 약 3~4분 걸려 진행상황을 자주 확인할
# 수 있고, 중간에 죽어도 손실이 작다.
SHARD_SIZE = 1_000
BATCH_SIZE = 8  # OOM 이후 보수적으로 낮춤(§ 위 이슈 참고)
EMBED_MAX_CHARS = 6000  # 극단적으로 긴 outlier chunk 의 임베딩 입력만 앞부분으로 clip
DEVICE = "mps"
CHUNKS_V2_LEAF = "chunks_v2/leaf_chunks.pkl"

os.makedirs(OUT_DIR, exist_ok=True)
progress_path = os.path.join(OUT_DIR, "progress.json")
log_path = os.path.join(OUT_DIR, "embed_progress.log")
failed_path = os.path.join(OUT_DIR, "failed_chunk_ids.jsonl")


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(log_path, "a") as f:
        f.write(line + "\n")


MINI_BATCH = 64  # embed() 한 번에 넘기는 최대 개수 — 실측(2026-08-24): 50~64개 단위로
# 나눠 호출하면 안정적으로 진행됨을 확인. 큰 shard(1000+)를 한 번에 통째로 넘기면
# 진행 상황이 안 보여(shard 끝날 때까지 로그 없음) 멈춘 것처럼 보이는 문제도 겸사겸사 해결.


def embed_with_retry(provider, chunk_ids: list[str], texts: list[str], batch_size: int, depth: int = 0):
    """batch_size 로 embed 시도, MPS OOM 등으로 실패하면 절반으로 쪼개 재시도.
    batch_size 가 1 인데도 실패하는 개별 chunk 는 zero-vector 로 대체하고 기록한다."""
    try:
        return provider.embed(texts, batch_size=min(batch_size, len(texts)))
    except Exception as e:  # noqa: BLE001 — MPS 백엔드 예외 타입이 RuntimeError 등 다양함
        if len(texts) == 1:
            log(f"임베딩 실패(개별 chunk, 포기하고 zero-vector 로 대체): chunk_id={chunk_ids[0]} error={e!r}")
            with open(failed_path, "a") as f:
                f.write(json.dumps({"chunk_id": chunk_ids[0], "error": repr(e)}, ensure_ascii=False) + "\n")
            return [[0.0] * provider.dim]
        mid = len(texts) // 2
        log(f"임베딩 실패(depth={depth}, n={len(texts)}) — 절반으로 쪼개 재시도: {e!r}")
        left = embed_with_retry(provider, chunk_ids[:mid], texts[:mid], batch_size, depth + 1)
        right = embed_with_retry(provider, chunk_ids[mid:], texts[mid:], batch_size, depth + 1)
        return left + right


def embed_shard(provider, chunk_ids: list[str], texts: list[str], batch_size: int) -> list:
    """shard 하나를 MINI_BATCH 단위로 쪼개 순차 처리 — 진행상황을 자주 로그로
    남겨 "멈춘 것처럼 보이는데 사실은 진행 중"인지 바로 판단 가능하게 한다."""
    vectors: list = []
    for i in range(0, len(texts), MINI_BATCH):
        mini_ids = chunk_ids[i : i + MINI_BATCH]
        mini_texts = texts[i : i + MINI_BATCH]
        t0 = time.time()
        vectors.extend(embed_with_retry(provider, mini_ids, mini_texts, batch_size))
        dt = time.time() - t0
        log(f"  mini-batch [{i}:{i + len(mini_texts)}] {dt:.1f}s ({dt/len(mini_texts)*1000:.1f}ms/chunk)")
    return vectors


def main() -> None:
    if not os.path.exists(CHUNKS_V2_LEAF):
        raise SystemExit(
            f"{CHUNKS_V2_LEAF} 없음 — 먼저 scripts/rebuild_chunks_v2.py 를 실행해서 "
            "새 semantic block chunking 결과를 만들어야 한다."
        )
    log(f"leaf chunks 로드: {CHUNKS_V2_LEAF}")
    chunks = pickle.load(open(CHUNKS_V2_LEAF, "rb"))
    log(f"총 leaf chunk 수: {len(chunks)}")

    n_clipped = sum(1 for c in chunks if len(c.text) > EMBED_MAX_CHARS)
    log(f"임베딩 입력이 clip 될 chunk 수(len(text) > {EMBED_MAX_CHARS}): {n_clipped} ({n_clipped/len(chunks)*100:.2f}%)")

    provider = build_embedding_provider("bge-m3", device=DEVICE)
    log(f"embedding provider ready (device={DEVICE}, batch_size={BATCH_SIZE})")

    start_idx = 0
    if os.path.exists(progress_path):
        start_idx = json.load(open(progress_path))["next_idx"]
        log(f"이어서 진행: idx={start_idx} 부터 (총 {len(chunks)}건)")

    total_t0 = time.time()
    n_done_this_run = 0
    for shard_start in range(start_idx, len(chunks), SHARD_SIZE):
        shard = chunks[shard_start : shard_start + SHARD_SIZE]
        texts = [c.text[:EMBED_MAX_CHARS] for c in shard]
        chunk_ids = [c.chunk_id for c in shard]

        t0 = time.time()
        vectors = embed_shard(provider, chunk_ids, texts, BATCH_SIZE)
        elapsed = time.time() - t0

        shard_idx = shard_start // SHARD_SIZE
        out_path = os.path.join(OUT_DIR, f"shard_{shard_idx:04d}.pkl")
        pickle.dump({"chunks": shard, "vectors": vectors, "dim": provider.dim}, open(out_path, "wb"))

        ms_per_chunk = elapsed / len(shard) * 1000
        n_done_this_run += len(shard)
        done_total = shard_start + len(shard)
        remaining = len(chunks) - done_total
        eta_sec = remaining * (ms_per_chunk / 1000)
        pct = done_total / len(chunks) * 100
        log(
            f"shard {shard_idx}: {len(shard)}건 {elapsed:.1f}s ({ms_per_chunk:.2f}ms/chunk) -> "
            f"{out_path} | 누적 {done_total}/{len(chunks)} ({pct:.1f}%) | 남은 예상 {eta_sec/60:.1f}분"
        )
        json.dump({"next_idx": done_total}, open(progress_path, "w"))

    log(f"전체 완료. 이번 실행 처리량 {n_done_this_run}건, 총 {time.time() - total_t0:.0f}s")


if __name__ == "__main__":
    main()
