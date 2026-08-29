"""§37, §46, §49: Semantic Router 배선. Router 는 "어느 문서를 검색할지" 를
정하는 Retriever 가 아니라 "이 질문이 어떤 작업 유형인지" 만 판단한다.

Router interface 를 분리해 Semantic Router / HCX Router / Router 없음(§49) 을
교체 가능하게 한다."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel
from semantic_router import Route, SemanticRouter

from disclosure_rag.retrieval.embeddings import EmbeddingProvider
from disclosure_rag.router.encoder_adapter import ProviderBackedEncoder
from disclosure_rag.router.routes import ROUTE_UTTERANCES

DEFAULT_THRESHOLD = 0.5


class RouteResult(BaseModel):
    route: str | None  # None = fallback (§46) -> HCX Agent 가 직접 판단
    score: float | None
    # 2026-08-29 추가(개선 후보 2/4): 이 route 판단이 "어디서" 나왔는지 기록.
    # "confidence" 요구사항(개선 후보 4)은 새 스키마 필드를 따로 만들지 않고
    # 이 source 로 흡수한다 — source=="hcx_unclear" 자체가 곧 "저확신" 신호다.
    # 값: "semantic_fast_path"(margin 커서 HCX 안 감) / "hcx_escalation"
    # (HCX가 유효 route 반환) / "hcx_unclear"(HCX가 unclear 또는 무효값 반환,
    # route=None) / None(NoRouter 등 source 개념이 없는 라우터). 하위호환을
    # 위해 기본값 None — 기존 `RouteResult(route=..., score=...)` 호출부가
    # 전부 그대로 동작한다.
    source: str | None = None


class Router(Protocol):
    name: str

    def route(self, normalized_query: str) -> RouteResult: ...


def build_semantic_router(
    provider: EmbeddingProvider, *, threshold: float = DEFAULT_THRESHOLD,
) -> SemanticRouter:
    encoder = ProviderBackedEncoder(provider)
    routes = [
        Route(name=name, utterances=utterances, score_threshold=threshold)
        for name, utterances in ROUTE_UTTERANCES.items()
    ]
    return SemanticRouter(encoder=encoder, routes=routes, auto_sync="local")


def set_all_thresholds(router: SemanticRouter, threshold: float) -> None:
    """모든 route 의 threshold 를 일괄 변경한다 (재임베딩 없이 threshold sweep 가능, §48)."""
    for route in router.routes:
        route.score_threshold = threshold


class SemanticRouterAdapter:
    name = "semantic_router"

    def __init__(self, provider: EmbeddingProvider, *, threshold: float = DEFAULT_THRESHOLD):
        self._router = build_semantic_router(provider, threshold=threshold)

    def route(self, normalized_query: str) -> RouteResult:
        choice = self._router(normalized_query)
        return RouteResult(route=choice.name, score=choice.similarity_score, source="semantic_fast_path")

    def set_threshold(self, threshold: float) -> None:
        set_all_thresholds(self._router, threshold)


class NoRouter:
    """§49 비교축: Router 없이 HCX Agent 가 직접 판단하는 baseline."""

    name = "none"

    def route(self, normalized_query: str) -> RouteResult:
        return RouteResult(route=None, score=None, source=None)
