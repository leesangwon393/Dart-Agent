"""100문항 테스트용: gpu_embeddings/shard_*.pkl 를 스트리밍으로 훑으면서
WANTED_COMPANIES 에 해당하는 chunk 만 골라 로컬 캐시로 저장한다.
메모리 안전을 위해 벡터는 즉시 numpy float32 로 변환한다 (python list보다
훨씬 적은 메모리)."""
import glob, pickle, time
import numpy as np

WANTED_COMPANIES = {
    # 기존 검증된 10개사
    "삼성전자", "삼성SDI", "LG에너지솔루션", "한미반도체", "KB금융",
    "알테오젠", "HD현대중공업", "현대자동차", "현대건설", "SK텔레콤",
    # 신규 28개사 (업종 다양화)
    "기아", "SK하이닉스", "NAVER", "카카오", "신한지주",
    "삼성바이오로직스", "셀트리온", "하이브", "와이지엔터테인먼트", "크래프톤",
    "두산로보틱스", "레인보우로보틱스", "한화에어로스페이스", "한국항공우주",
    "이마트", "LG생활건강", "아모레퍼시픽", "POSCO홀딩스", "고려아연",
    "HMM", "현대글로비스", "대우건설", "케이티", "LG유플러스",
    "미래에셋증권", "에코프로비엠", "현대모비스", "삼성전기",
}
print(f"대상 회사 수: {len(WANTED_COMPANIES)}", flush=True)

shard_files = sorted(glob.glob("gpu_embeddings/shard_*.pkl"))
print(f"shard 수: {len(shard_files)}", flush=True)

out_chunks = []
out_vectors = []
dim = None
t0 = time.time()
for i, fp in enumerate(shard_files):
    d = pickle.load(open(fp, "rb"))
    dim = d["dim"]
    kept = 0
    for c, v in zip(d["chunks"], d["vectors"]):
        if c.company in WANTED_COMPANIES:
            out_chunks.append(c)
            out_vectors.append(np.asarray(v, dtype=np.float32))
            kept += 1
    print(f"  [{i+1}/{len(shard_files)}] {fp}: {len(d['chunks'])}건 중 {kept}건 채택 "
          f"(누적 {len(out_chunks)}, {time.time()-t0:.0f}s 경과)", flush=True)
    del d

print(f"\n총 채택된 chunk: {len(out_chunks)}", flush=True)
by_company = {}
for c in out_chunks:
    by_company[c.company] = by_company.get(c.company, 0) + 1
missing = WANTED_COMPANIES - set(by_company)
if missing:
    print(f"⚠️ 임베딩에서 못 찾은 회사: {missing}", flush=True)
for comp, cnt in sorted(by_company.items(), key=lambda x: -x[1]):
    print(f"  {comp}: {cnt}", flush=True)

pickle.dump({"chunks": out_chunks, "vectors": out_vectors, "dim": dim},
            open("/tmp/hundred_q_vectors.pkl", "wb"), protocol=pickle.HIGHEST_PROTOCOL)
print(f"\n저장 완료: /tmp/hundred_q_vectors.pkl ({time.time()-t0:.0f}초 총소요)", flush=True)
