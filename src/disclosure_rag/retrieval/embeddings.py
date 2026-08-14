"""Dense Embedding Provider 추상화 (사용자 결정 #2).

baseline: BAAI/bge-m3. provider interface 를 분리해 추후 multilingual-e5-large-instruct,
HCX Embedding 과 Recall@K 비교가 가능하게 한다 (§74 평가 가능 모듈화).
"""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str], *, batch_size: int = 32) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class BgeM3EmbeddingProvider:
    """baseline. sentence-transformers 로 BAAI/bge-m3 dense embedding 만 사용한다
    (bge-m3 는 dense/sparse/colbert 3 종을 지원하지만, baseline 은 dense 만 사용하고
    sparse/multi-vector 는 향후 비교 대상으로 남겨둔다)."""

    name = "bge-m3"
    dim = 1024

    def __init__(self, device: str | None = None):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer("BAAI/bge-m3", device=device)

    def embed(self, texts: list[str], *, batch_size: int = 32) -> list[list[float]]:
        vecs = self._model.encode(
            texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False,
        )
        return vecs.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class E5InstructEmbeddingProvider:
    """비교 대상 #1 (사용자 결정 #2). multilingual-e5-large-instruct 는 query 앞에
    instruction prefix 를 붙여야 성능이 나온다는 점이 bge-m3 와 다르다."""

    name = "e5-instruct"
    dim = 1024
    _QUERY_PREFIX = "Instruct: Given a financial disclosure question, retrieve relevant passages\nQuery: "

    def __init__(self, device: str | None = None):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer("intfloat/multilingual-e5-large-instruct", device=device)

    def embed(self, texts: list[str], *, batch_size: int = 32) -> list[list[float]]:
        vecs = self._model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
        return vecs.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed([self._QUERY_PREFIX + text])[0]


class HCXEmbeddingProvider:
    """비교 대상 #2 (사용자 결정 #2). HCX API 연결이 별도 dependency 로 확정되기
    전까지는 placeholder — 호출 시 명시적으로 NotImplementedError 를 낸다
    (silent 하게 dummy 벡터를 반환하지 않는다, §7 원칙을 embedding 에도 적용)."""

    name = "hcx-embedding"
    dim = 1024  # TODO: 실제 API 연결 후 확정

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "HCX Embedding API 미연결 — .env 의 HCX_API_KEY 로 실제 API 클라이언트를 "
            "구현해야 사용 가능. Phase 15(HCX Agent) 연결 시 함께 배선 예정."
        )

    def embed(self, texts: list[str], *, batch_size: int = 32) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


def build_embedding_provider(name: str, **kwargs) -> EmbeddingProvider:
    if name == "bge-m3":
        return BgeM3EmbeddingProvider(**kwargs)
    if name == "e5-instruct":
        return E5InstructEmbeddingProvider(**kwargs)
    if name == "hcx-embedding":
        return HCXEmbeddingProvider(**kwargs)
    raise ValueError(f"알 수 없는 embedding provider: {name}")
