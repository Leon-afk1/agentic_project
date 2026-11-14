"""Ranking metrics and bootstrap confidence intervals.

Definitions, fixed before any result was looked at:
- a record is *relevant* for a question when its grade is at least `min_grade`
  (2 = strict, the primary setting; 1 = lenient, reported as a second view);
- recall@k = relevant records in the top k / min(k, number of relevant records).
  The cap matters: with 12 relevant records, plain recall@5 could never exceed
  5/12 and would punish questions for having many good answers;
- MRR uses 1 / rank of the first relevant record, 0 when none is returned;
- questions without any relevant record are left out of both metrics. They are
  the "no answer" questions, measured separately by the abstention rate.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


def relevant_set(grades: Mapping[str, int], min_grade: int = 2) -> set[str]:
    return {uuid for uuid, grade in grades.items() if grade >= min_grade}


def recall_at_k(ranking: Sequence[str], relevant: set[str], k: int = 5) -> float:
    if not relevant:
        raise ValueError("recall is undefined without any relevant record")
    found = sum(1 for uuid in ranking[:k] if uuid in relevant)
    return found / min(k, len(relevant))


def reciprocal_rank(ranking: Sequence[str], relevant: set[str]) -> float:
    for rank, uuid in enumerate(ranking, 1):
        if uuid in relevant:
            return 1.0 / rank
    return 0.0


def unjudged_at_k(ranking: Sequence[str], grades: Mapping[str, int], k: int = 5) -> int:
    """Results in the top k that nobody graded: a score computed while this is
    not zero under-estimates the system."""
    return sum(1 for uuid in ranking[:k] if uuid not in grades)


@dataclass(frozen=True)
class Interval:
    mean: float
    low: float
    high: float
    n: int

    def __str__(self) -> str:
        return f"{self.mean:.3f} [{self.low:.3f}, {self.high:.3f}]"


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def bootstrap_ci(
    values: Sequence[float],
    *,
    resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 0,
    statistic: Callable[[Sequence[float]], float] = _mean,
) -> Interval:
    """Percentile bootstrap over questions: resample the questions with
    replacement, recompute the mean, and read the interval off the distribution."""
    if not values:
        raise ValueError("no value to average")
    rng = random.Random(seed)
    n = len(values)
    estimates = sorted(
        statistic([values[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples)
    )
    tail = (1 - confidence) / 2
    low = estimates[int(tail * resamples)]
    high = estimates[min(resamples - 1, int((1 - tail) * resamples))]
    return Interval(mean=statistic(values), low=low, high=high, n=n)


def paired_difference(
    system: Sequence[float], baseline: Sequence[float], *, resamples: int = 2000, seed: int = 0
) -> Interval:
    """Interval on mean(system - baseline), question by question.

    Both systems answer the same questions, so the per-question differences are
    resampled. If the interval contains 0, the comparison is inconclusive.
    """
    if len(system) != len(baseline):
        raise ValueError("both systems must be scored on the same questions")
    differences = [s - b for s, b in zip(system, baseline, strict=True)]
    return bootstrap_ci(differences, resamples=resamples, seed=seed)


def conclusive(interval: Interval) -> bool:
    return interval.low > 0 or interval.high < 0
