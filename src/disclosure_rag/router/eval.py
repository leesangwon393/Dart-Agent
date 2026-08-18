"""§48, §76: Router 평가 — Accuracy / Macro F1 / Confusion Matrix / Fallback Rate,
threshold sweep 지원."""

from __future__ import annotations

from dataclasses import dataclass

from sklearn.metrics import confusion_matrix, f1_score

from disclosure_rag.router.eval_dataset import EvalExample
from disclosure_rag.router.semantic_router_wrapper import Router

FALLBACK_LABEL = "__FALLBACK__"


@dataclass
class AmbiguousEvalReport:
    """§47 AMBIGUOUS_SET 전용 평가. 정답이 여러 개(acceptable route 목록)이고,
    라우터가 그 중 하나를 골랐거나 정직하게 fallback(route=None) 했으면
    '적절한 처리'로 카운트한다 — 진짜 애매한 질문에서는 fallback 도 오답이
    아니라 정답으로 본다(§48/hcx_router.py 참고, 2026-08-18)."""

    n: int
    appropriate_rate: float  # (acceptable에 속하거나 fallback인) 비율
    fallback_rate: float
    forced_wrong_rate: float  # acceptable도 아니고 fallback도 아닌, 명백한 오답
    details: list[dict]


def evaluate_router_ambiguous(
    router: Router, examples: list[tuple[str, list[str]]],
) -> AmbiguousEvalReport:
    """`router/eval_dataset.py`의 `AMBIGUOUS_SET`을 평가한다. `evaluate_router()`와
    분리한 이유: AMBIGUOUS_SET은 정답이 단일 label이 아니라 acceptable route
    목록이라 accuracy/F1 계산 방식 자체가 다르다(이전까지 이 데이터셋은
    정의만 되고 아무 데서도 안 쓰이던 죽은 코드였다)."""
    details = []
    n_ok, n_fallback, n_wrong = 0, 0, 0
    for query, acceptable in examples:
        result = router.route(query)
        if result.route is None:
            n_fallback += 1
            n_ok += 1
            outcome = "fallback"
        elif result.route in acceptable:
            n_ok += 1
            outcome = "acceptable"
        else:
            n_wrong += 1
            outcome = "forced_wrong"
        details.append({"query": query, "acceptable": acceptable, "predicted": result.route,
                         "score": result.score, "outcome": outcome})
    n = len(examples)
    return AmbiguousEvalReport(
        n=n, appropriate_rate=n_ok / n, fallback_rate=n_fallback / n,
        forced_wrong_rate=n_wrong / n, details=details,
    )


@dataclass
class RouterEvalReport:
    accuracy: float
    macro_f1: float
    fallback_rate: float
    labels: list[str]
    confusion_matrix: list[list[int]]
    n: int

    def render(self) -> str:
        lines = [
            f"n={self.n}  accuracy={self.accuracy:.3f}  macro_f1={self.macro_f1:.3f}  fallback_rate={self.fallback_rate:.3f}",
            "",
            "Confusion Matrix (rows=true, cols=pred):",
            "labels: " + ", ".join(self.labels),
        ]
        for label, row in zip(self.labels, self.confusion_matrix):
            lines.append(f"  {label:20s} {row}")
        return "\n".join(lines)


def evaluate_router(router: Router, examples: list[EvalExample]) -> RouterEvalReport:
    y_true, y_pred = [], []
    fallback_count = 0
    for ex in examples:
        result = router.route(ex.query)
        pred = result.route or FALLBACK_LABEL
        y_pred.append(pred)
        y_true.append(ex.expected_route or FALLBACK_LABEL)
        if result.route is None:
            fallback_count += 1

    labels = sorted(set(y_true) | set(y_pred))
    accuracy = sum(p == t for p, t in zip(y_pred, y_true)) / len(examples)
    macro_f1 = f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=labels).tolist()

    return RouterEvalReport(
        accuracy=accuracy, macro_f1=macro_f1, fallback_rate=fallback_count / len(examples),
        labels=labels, confusion_matrix=cm, n=len(examples),
    )


def threshold_sweep(
    router: Router, examples: list[EvalExample], thresholds: list[float],
) -> dict[float, RouterEvalReport]:
    """router 를 재구축(재임베딩)하지 않고 threshold 만 바꿔가며 평가한다
    (SemanticRouterAdapter.set_threshold 사용)."""
    results = {}
    for t in thresholds:
        if hasattr(router, "set_threshold"):
            router.set_threshold(t)
        results[t] = evaluate_router(router, examples)
    return results
