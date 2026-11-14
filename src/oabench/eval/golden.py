"""The golden set: questions, their status, and the dev/test split.

A question goes through three states: `proposed` (drafted, not yet read by the
annotator), then `validated` or `rejected`. Only validated questions are split
and evaluated. The split is assigned once and never recomputed, so that the
test questions cannot drift towards what the systems are good at.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from oabench.snapshot.records import Dataset

QUESTIONS_FILE = "questions.jsonl"

Level = Literal["easy", "medium", "hard", "no_answer"]
Source = Literal["need", "masked_record", "annotator"]
Status = Literal["proposed", "validated", "rejected"]
Split = Literal["dev", "test"]

LEVELS: tuple[Level, ...] = ("easy", "medium", "hard", "no_answer")
SPLIT_SEED = 20261114
DEV_FRACTION = 1 / 3  # 20 dev and 40 test questions out of 60


class Question(BaseModel):
    id: str
    question: str
    level: Level
    source: Source
    status: Status = "proposed"
    split: Split | None = None
    # For `masked_record` questions: the record the question was written from.
    # It is a hint for the annotator, not a judgment: relevance is decided later.
    seed_uuid: str | None = None
    note: str = ""


def load_questions(path: Path) -> list[Question]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [Question.model_validate_json(line) for line in handle if line.strip()]


def save_questions(path: Path, questions: Sequence[Question]) -> None:
    ids = [q.id for q in questions]
    duplicates = [i for i, n in Counter(ids).items() if n > 1]
    if duplicates:
        raise ValueError(f"duplicate question ids: {duplicates}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for question in questions:
            handle.write(question.model_dump_json(exclude_none=True) + "\n")


def assign_split(questions: Sequence[Question], *, seed: int = SPLIT_SEED) -> list[Question]:
    """Give every validated question a split, stratified by level.

    Questions that already have a split keep it: adding questions later never
    moves an existing one between dev and test.
    """
    rng = random.Random(seed)
    by_level: dict[str, list[Question]] = defaultdict(list)
    for question in questions:
        if question.status == "validated":
            by_level[question.level].append(question)
    for level in LEVELS:
        group = by_level.get(level, [])
        target_dev = round(len(group) * DEV_FRACTION)
        missing_dev = target_dev - sum(q.split == "dev" for q in group)
        unassigned = sorted((q for q in group if q.split is None), key=lambda q: q.id)
        rng.shuffle(unassigned)
        for position, question in enumerate(unassigned):
            question.split = "dev" if position < missing_dev else "test"
    return list(questions)


def summary(questions: Sequence[Question]) -> dict[str, dict[str, int]]:
    """Counts by status, and for validated questions by level, source and split."""
    validated = [q for q in questions if q.status == "validated"]
    return {
        "status": dict(Counter(q.status for q in questions)),
        "level": dict(Counter(q.level for q in validated)),
        "source": dict(Counter(q.source for q in validated)),
        "split": dict(Counter(q.split or "unassigned" for q in validated)),
    }


class MaskedView(BaseModel):
    """What the question writer is allowed to see of a record: no title, no id, no link."""

    ref: str
    description: str
    columns: list[str]
    organizations: list[str]
    topics: list[str]


def sample_for_masking(
    datasets: Sequence[Dataset], n: int, *, seed: int, min_description_chars: int = 200
) -> list[Dataset]:
    """Draw `n` records, cycling over topics so that no theme dominates the sample.

    Records with a very short description are left out: nobody could write a
    meaningful question from them without the title.
    """
    rng = random.Random(seed)
    by_topic: dict[str, list[Dataset]] = defaultdict(list)
    for dataset in sorted(datasets, key=lambda d: d.uuid):
        if len(dataset.abstract) >= min_description_chars:
            by_topic[dataset.topics[0] if dataset.topics else "(none)"].append(dataset)
    topics = sorted(by_topic)
    rng.shuffle(topics)
    for group in by_topic.values():
        rng.shuffle(group)
    chosen: list[Dataset] = []
    while len(chosen) < n and any(by_topic[t] for t in topics):
        for topic in topics:
            if by_topic[topic] and len(chosen) < n:
                chosen.append(by_topic[topic].pop())
    return chosen


def masked_view(dataset: Dataset, ref: str) -> MaskedView:
    return MaskedView(
        ref=ref,
        description=dataset.abstract[:1200],
        columns=[c.name for c in dataset.columns[:25]],
        organizations=dataset.organizations[:3],
        topics=dataset.topics,
    )
