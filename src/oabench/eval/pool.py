"""Pooling of candidates and relevance judgments (the TREC method).

For each question, the top results of every engine are merged into one pool.
The annotator grades each candidate without knowing which engine returned it,
in a shuffled order, so that no engine is favoured by position.

Known limit: a relevant record that no pooled engine returned is never judged.
Any system evaluated later must have its unjudged results added to the pool
(`build_pool` extends an existing pool) and judged before scores are computed.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from oabench.catalog import Catalog
from oabench.eval.golden import Question
from oabench.retrieval.engines import Engine

POOL_FILE = "pool.jsonl"
JUDGMENTS_FILE = "judgments.jsonl"
POOL_DEPTH = 10

Grade = Literal[0, 1, 2]
GRADE_LABELS = {2: "relevant", 1: "partially relevant", 0: "not relevant"}

HELP = (
    "[2] relevant   [1] partially relevant   [0] not relevant   "
    "[m] show more   [u] undo the previous grade   [s] skip   [q] save and quit"
)


class Candidate(BaseModel):
    question_id: str
    uuid: str
    found_by: dict[str, int]  # engine name -> rank (1 = first); never shown when judging


class Judgment(BaseModel):
    question_id: str
    uuid: str
    grade: Grade


def _load(path: Path, model: type[BaseModel]) -> list[BaseModel]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [model.model_validate_json(line) for line in handle if line.strip()]


def _save(path: Path, rows: Sequence[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(row.model_dump_json() + "\n")


def load_pool(path: Path) -> list[Candidate]:
    return [row for row in _load(path, Candidate) if isinstance(row, Candidate)]


def load_judgments(path: Path) -> list[Judgment]:
    return [row for row in _load(path, Judgment) if isinstance(row, Judgment)]


def build_pool(
    questions: Sequence[Question],
    engines: Sequence[Engine],
    *,
    depth: int = POOL_DEPTH,
    existing: Sequence[Candidate] = (),
) -> list[Candidate]:
    """Merge the top `depth` results of each engine for every validated question."""
    pool: dict[tuple[str, str], Candidate] = {(c.question_id, c.uuid): c for c in existing}
    for question in questions:
        if question.status != "validated":
            continue
        for engine in engines:
            for rank, (uuid, _score) in enumerate(engine.search(question.question, depth), 1):
                key = (question.id, uuid)
                if key not in pool:
                    pool[key] = Candidate(question_id=question.id, uuid=uuid, found_by={})
                pool[key].found_by[engine.name] = rank
    return sorted(pool.values(), key=lambda c: (c.question_id, c.uuid))


def qrels(judgments: Sequence[Judgment]) -> dict[str, dict[str, int]]:
    """question id -> {uuid: grade}."""
    result: dict[str, dict[str, int]] = {}
    for judgment in judgments:
        result.setdefault(judgment.question_id, {})[judgment.uuid] = judgment.grade
    return result


def pending(pool: Sequence[Candidate], judgments: Sequence[Judgment]) -> list[Candidate]:
    done = {(j.question_id, j.uuid) for j in judgments}
    return [c for c in pool if (c.question_id, c.uuid) not in done]


def _card(catalog: Catalog, uuid: str, chars: int) -> str:
    dataset = catalog.get(uuid)
    if dataset is None:
        return f"  (record {uuid} is not in the snapshot)"
    dates = ", ".join(f"{k} {v[:10]}" for k, v in dataset.resource_dates.items())
    abstract = dataset.abstract.replace("\n", " ")
    return "\n".join(
        [
            f"  TITLE   {dataset.title}",
            f"  BY      {', '.join(dataset.organizations[:3]) or '-'}",
            f"  DATES   {dates or '-'}    FORMATS {', '.join(dataset.formats[:5]) or '-'}",
            f"  TEXT    {abstract[:chars]}{'…' if len(abstract) > chars else ''}",
            f"  PAGE    https://www.datagrandest.fr/datahub/dataset/{uuid}",
        ]
    )


def judge(
    pool_path: Path,
    judgments_path: Path,
    questions: Sequence[Question],
    catalog: Catalog,
    *,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
    split: str | None = None,
    suggestions: Mapping[tuple[str, str], int] | None = None,
) -> int:
    """Grade pending candidates, question by question. Returns how many were graded.

    `split` restricts the session to the dev or the test questions.
    `suggestions` switches to review mode: the grade given by another annotator
    (a language model) is shown and Enter accepts it. Faster, but the reviewer is
    influenced by what is shown: such grades are a human *review*, not an
    independent human judgment, and must not be used to measure agreement.
    """
    judgments = load_judgments(judgments_path)
    wanted = {q.id for q in questions if split is None or q.split == split}
    todo = [c for c in pending(load_pool(pool_path), judgments) if c.question_id in wanted]
    by_question: dict[str, list[Candidate]] = {}
    for candidate in todo:
        by_question.setdefault(candidate.question_id, []).append(candidate)
    texts = {q.id: q for q in questions}
    total = len(todo)
    graded = 0
    show(f"{total} candidate(s) to judge over {len(by_question)} question(s). {HELP}")

    def order(question_id: str) -> tuple[int, str]:
        # Dev questions first: the first table (and the tuning) only needs them.
        known = texts.get(question_id)
        return (0 if known is not None and known.split == "dev" else 1, question_id)

    queue: list[Candidate] = []
    for question_id in sorted(by_question, key=order):
        candidates = by_question[question_id]
        # Shuffled with a seed tied to the question: the order carries no hint
        # about ranks, and resuming a session shows the same order again.
        random.Random(question_id).shuffle(candidates)
        queue.extend(candidates)

    position = 0
    while position < len(queue):
        candidate = queue[position]
        question = texts.get(candidate.question_id)
        header = question.question if question else candidate.question_id
        show(f"\n[{candidate.question_id}] {header}   (candidate {position + 1}/{total})")
        show(_card(catalog, candidate.uuid, 400))
        suggested = (suggestions or {}).get((candidate.question_id, candidate.uuid))
        if suggested is not None:
            show(f"  MODEL   graded {suggested} ({GRADE_LABELS[suggested]}). Enter = accept.")
        answer = ask("> ").strip().lower()
        if answer == "" and suggested is not None:
            answer = str(suggested)
        if answer in ("0", "1", "2"):
            grade: Grade = 2 if answer == "2" else 1 if answer == "1" else 0
            judgments.append(
                Judgment(question_id=candidate.question_id, uuid=candidate.uuid, grade=grade)
            )
            _save(judgments_path, judgments)
            graded += 1
            position += 1
        elif answer == "m":
            show(_card(catalog, candidate.uuid, 3000))
        elif answer == "u":
            previous = queue[position - 1] if position > 0 else None
            last = judgments[-1] if judgments else None
            if (
                previous
                and last
                and (last.question_id, last.uuid)
                == (
                    previous.question_id,
                    previous.uuid,
                )
            ):
                judgments.pop()
                _save(judgments_path, judgments)
                graded -= 1
                position -= 1
            else:
                show("  nothing to undo in this session")
        elif answer == "s":
            position += 1
        elif answer == "q":
            break
        else:
            show(HELP)
    show(f"{graded} graded, saved to {judgments_path}")
    return graded


def save_pool(path: Path, pool: Sequence[Candidate]) -> None:
    _save(path, pool)
