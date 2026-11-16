"""Agreement between two annotators on the same (question, record) pairs.

Used to answer: can judgments made by a language model stand in for human ones?
Reported: raw agreement, Cohen's kappa with linear weights (the 0/1/2 scale is
ordered, so confusing 0 with 2 costs more than 0 with 1), and the confusion matrix.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from oabench.eval.pool import Judgment

GRADES = (0, 1, 2)


@dataclass(frozen=True)
class Agreement:
    pairs: int
    raw: float
    kappa: float
    matrix: list[list[int]]  # matrix[a][b]: first annotator said a, second said b


def weighted_kappa(matrix: Sequence[Sequence[int]]) -> float:
    """Cohen's kappa with linear weights. 1 = perfect, 0 = chance level."""
    size = len(matrix)
    total = sum(sum(row) for row in matrix)
    if total == 0:
        raise ValueError("no pair to compare")
    rows = [sum(row) for row in matrix]
    cols = [sum(matrix[i][j] for i in range(size)) for j in range(size)]
    observed = expected = 0.0
    for i in range(size):
        for j in range(size):
            weight = abs(i - j) / (size - 1)
            observed += weight * matrix[i][j] / total
            expected += weight * rows[i] * cols[j] / (total * total)
    if expected == 0:
        return 1.0  # both annotators used a single, identical grade
    return 1.0 - observed / expected


def compare(first: Sequence[Judgment], second: Sequence[Judgment]) -> Agreement:
    """Compare two annotators on the pairs both of them graded."""
    grades = {(j.question_id, j.uuid): j.grade for j in first}
    matrix = [[0] * len(GRADES) for _ in GRADES]
    for judgment in second:
        key = (judgment.question_id, judgment.uuid)
        if key in grades:
            matrix[grades[key]][judgment.grade] += 1
    pairs = sum(sum(row) for row in matrix)
    if pairs == 0:
        raise ValueError("the two files share no judged pair")
    same = sum(matrix[g][g] for g in GRADES)
    return Agreement(pairs=pairs, raw=same / pairs, kappa=weighted_kappa(matrix), matrix=matrix)


def format_agreement(result: Agreement, first: str, second: str) -> str:
    lines = [
        f"pairs graded by both: {result.pairs}",
        f"raw agreement       : {result.raw:.1%}",
        f"weighted kappa      : {result.kappa:.3f}",
        f"rows = {first}, columns = {second}",
        "        0     1     2",
    ]
    lines += [
        f"  {grade}  " + "".join(f"{count:6d}" for count in result.matrix[grade])
        for grade in GRADES
    ]
    return "\n".join(lines)
