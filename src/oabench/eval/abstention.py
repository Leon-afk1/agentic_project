"""Does a system know when the catalog has no answer?

A search engine always returns its best matches. To let it "abstain", its top
score is compared with a threshold: below it, the engine is taken to say
"nothing relevant". The threshold is chosen on the dev questions and then
applied, unchanged, to the test questions.

Two rates are reported, because one alone can be gamed (always abstaining
gives a perfect first rate):
- correct abstention: share of no-answer questions where the system abstains;
- wrongful abstention: share of answerable questions where it abstains too.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from oabench.eval.golden import Question
from oabench.eval.metrics import relevant_set
from oabench.retrieval.engines import Engine


@dataclass(frozen=True)
class AbstentionReport:
    name: str
    threshold: float
    no_answer_questions: int
    answerable_questions: int
    correct_abstention: float
    wrongful_abstention: float


def split_by_answerability(
    questions: Sequence[Question], qrels: Mapping[str, Mapping[str, int]], min_grade: int = 2
) -> tuple[list[Question], list[Question]]:
    """(answerable, no-answer) among validated questions that have been judged.

    A question nobody judged yet is in neither list: no judgment is not the
    same thing as "no relevant record".
    """
    answerable: list[Question] = []
    no_answer: list[Question] = []
    for question in questions:
        if question.status != "validated" or question.id not in qrels:
            continue
        has_answer = bool(relevant_set(qrels[question.id], min_grade))
        (answerable if has_answer else no_answer).append(question)
    return answerable, no_answer


def top_score(engine: Engine, question: Question) -> float:
    """Score of the first result; minus infinity when nothing is returned."""
    ranking = engine.search(question.question, 1)
    return ranking[0][1] if ranking else -math.inf


def abstains(score: float, threshold: float) -> bool:
    return score < threshold or score == -math.inf


def rates(
    answerable_scores: Sequence[float], no_answer_scores: Sequence[float], threshold: float
) -> tuple[float, float]:
    """(correct abstention, wrongful abstention); NaN when a group is empty."""

    def share(scores: Sequence[float]) -> float:
        if not scores:
            return math.nan
        return sum(abstains(s, threshold) for s in scores) / len(scores)

    return share(no_answer_scores), share(answerable_scores)


def choose_threshold(
    answerable_scores: Sequence[float], no_answer_scores: Sequence[float]
) -> float:
    """Threshold maximising balanced accuracy on the given (dev) scores.

    Balanced accuracy = (correct abstention + correct non-abstention) / 2, so
    that the few no-answer questions weigh as much as the many answerable ones.
    Ties go to the lowest threshold: when in doubt, answer.
    """
    finite = sorted({s for s in [*answerable_scores, *no_answer_scores] if math.isfinite(s)})
    candidates = [-math.inf] + [(a + b) / 2 for a, b in itertools.pairwise(finite)]
    if finite:
        candidates.append(finite[-1] + 1.0)
    best, best_value = -math.inf, -1.0
    for threshold in candidates:
        correct, wrongful = rates(answerable_scores, no_answer_scores, threshold)
        if math.isnan(correct) or math.isnan(wrongful):
            return -math.inf  # nothing to tune on: never abstain on score alone
        value = (correct + (1 - wrongful)) / 2
        if value > best_value:
            best, best_value = threshold, value
    return best


def evaluate_abstention(
    engine: Engine,
    dev: Sequence[Question],
    target: Sequence[Question],
    qrels: Mapping[str, Mapping[str, int]],
    *,
    min_grade: int = 2,
) -> AbstentionReport:
    """Tune the threshold on `dev`, report the two rates on `target`."""
    dev_yes, dev_no = split_by_answerability(dev, qrels, min_grade)
    if getattr(engine, "abstains_only_when_empty", False):
        threshold = -math.inf
    else:
        threshold = choose_threshold(
            [top_score(engine, q) for q in dev_yes], [top_score(engine, q) for q in dev_no]
        )
    yes, no = split_by_answerability(target, qrels, min_grade)
    correct, wrongful = rates(
        [top_score(engine, q) for q in yes], [top_score(engine, q) for q in no], threshold
    )
    return AbstentionReport(engine.name, threshold, len(no), len(yes), correct, wrongful)


def format_abstention(reports: Sequence[AbstentionReport]) -> str:
    def pct(value: float) -> str:
        return "-" if math.isnan(value) else f"{value:.0%}"

    lines = [
        "| System | Threshold (tuned on dev) | Correct abstention | Wrongful abstention |",
        "|---|---|---|---|",
    ]
    for r in reports:
        threshold = "none (abstains only on zero result)"
        if math.isfinite(r.threshold):
            threshold = f"{r.threshold:.3f}"
        lines.append(
            f"| {r.name} | {threshold} | {pct(r.correct_abstention)} of {r.no_answer_questions} "
            f"| {pct(r.wrongful_abstention)} of {r.answerable_questions} |"
        )
    return "\n".join(lines)
