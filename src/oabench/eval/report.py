"""Run engines over judged questions and build the comparison table."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from oabench.eval.golden import Question
from oabench.eval.metrics import (
    Interval,
    bootstrap_ci,
    conclusive,
    paired_difference,
    recall_at_k,
    reciprocal_rank,
    relevant_set,
    unjudged_at_k,
)
from oabench.retrieval.engines import Engine

K = 5


@dataclass
class EngineReport:
    name: str
    recall: dict[str, float] = field(default_factory=dict)  # question id -> recall@5
    rr: dict[str, float] = field(default_factory=dict)  # question id -> reciprocal rank
    unjudged: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def recall_ci(self) -> Interval:
        return bootstrap_ci(list(self.recall.values()))

    @property
    def mrr_ci(self) -> Interval:
        return bootstrap_ci(list(self.rr.values()))


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile, p in [0, 100]."""
    if not values:
        return math.nan
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def answerable(
    questions: Sequence[Question], qrels: Mapping[str, Mapping[str, int]], min_grade: int
) -> list[Question]:
    """Validated questions with at least one relevant record."""
    return [
        q
        for q in questions
        if q.status == "validated" and relevant_set(qrels.get(q.id, {}), min_grade)
    ]


def evaluate_engine(
    engine: Engine,
    questions: Sequence[Question],
    qrels: Mapping[str, Mapping[str, int]],
    *,
    min_grade: int = 2,
) -> EngineReport:
    report = EngineReport(name=engine.name)
    for question in answerable(questions, qrels, min_grade):
        grades = qrels[question.id]
        relevant = relevant_set(grades, min_grade)
        start = time.perf_counter()
        ranking = [uuid for uuid, _ in engine.search(question.question, K)]
        elapsed_ms = (time.perf_counter() - start) * 1000
        # A cached network answer says nothing about the service's speed.
        if not getattr(engine, "last_call_cached", False):
            report.latencies_ms.append(elapsed_ms)
        report.recall[question.id] = recall_at_k(ranking, relevant, K)
        report.rr[question.id] = reciprocal_rank(ranking, relevant)
        report.unjudged += unjudged_at_k(ranking, grades, K)
    return report


def format_table(reports: Sequence[EngineReport]) -> str:
    lines = [
        "| System | Questions | Recall@5 [95% CI] | MRR [95% CI] | p50 ms | p95 ms "
        "| Unjudged in top 5 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        if not r.recall:
            lines.append(f"| {r.name} | 0 | - | - | - | - | - |")
            continue
        p50, p95 = percentile(r.latencies_ms, 50), percentile(r.latencies_ms, 95)
        timing = ("-", "-") if math.isnan(p50) else (f"{p50:.0f}", f"{p95:.0f}")
        lines.append(
            f"| {r.name} | {len(r.recall)} | {r.recall_ci} | {r.mrr_ci} | "
            f"{timing[0]} | {timing[1]} | {r.unjudged} |"
        )
    return "\n".join(lines)


def format_comparisons(reports: Sequence[EngineReport], baseline: str) -> str:
    """Paired differences in recall@5 against one baseline, with the verdict."""
    reference = next((r for r in reports if r.name == baseline), None)
    if reference is None or not reference.recall:
        return ""
    ids = sorted(reference.recall)
    lines = [f"Recall@5 difference against {baseline} (paired bootstrap):"]
    for r in reports:
        if r.name == baseline or sorted(r.recall) != ids:
            continue
        diff = paired_difference([r.recall[i] for i in ids], [reference.recall[i] for i in ids])
        verdict = "conclusive" if conclusive(diff) else "inconclusive"
        lines.append(f"- {r.name}: {diff.mean:+.3f} [{diff.low:+.3f}, {diff.high:+.3f}] {verdict}")
    return "\n".join(lines)
