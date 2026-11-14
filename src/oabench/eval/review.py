"""Interactive review of proposed questions, one at a time, in the terminal.

The annotator decides; nothing is validated automatically. Progress is saved
after every answer, so the session can be interrupted at any point.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from oabench.catalog import Catalog
from oabench.eval.golden import LEVELS, Question, load_questions, save_questions

HELP = (
    "[a] accept   [e] edit the wording   [l] change the level   [r] reject   "
    "[s] skip for now   [q] save and quit"
)


def next_annotator_id(questions: list[Question]) -> str:
    numbers = [int(q.id[1:]) for q in questions if q.id[0] == "a" and q.id[1:].isdigit()]
    return f"a{max(numbers, default=0) + 1:02d}"


def describe(question: Question, catalog: Catalog | None) -> str:
    lines = [
        "",
        f"[{question.id}] level={question.level}  source={question.source}",
        f"  {question.question}",
    ]
    if question.note:
        lines.append(f"  note: {question.note}")
    if question.seed_uuid and catalog is not None:
        seed = catalog.get(question.seed_uuid)
        if seed is not None:
            lines.append(f"  written from: {seed.title} ({', '.join(seed.organizations[:2])})")
    if catalog is not None:
        hits, total = catalog.search(question.question, limit=3)
        lines.append(f"  BM25 preview ({total} matches), only to help you judge the wording:")
        lines += [f"    - {h.dataset.title[:90]}" for h in hits]
    return "\n".join(lines)


def review(
    path: Path,
    *,
    catalog: Catalog | None = None,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> int:
    """Walk through proposed questions. Returns how many were decided."""
    questions = load_questions(path)
    decided = 0
    pending = [q for q in questions if q.status == "proposed"]
    show(f"{len(pending)} proposed question(s) to review. {HELP}")
    for question in pending:
        show(describe(question, catalog))
        while True:
            answer = ask("> ").strip().lower()
            if answer == "e":
                text = ask("new wording: ").strip()
                if text:
                    question.question = text
                    show(f"  {question.question}")
            elif answer == "l":
                level = ask(f"level {LEVELS}: ").strip()
                if level in LEVELS:
                    question.level = level
                    show(f"  level={question.level}")
                else:
                    show("  unknown level, unchanged")
            elif answer in ("a", "r"):
                question.status = "validated" if answer == "a" else "rejected"
                decided += 1
                break
            elif answer in ("s", "q"):
                break
            else:
                show(HELP)
        save_questions(path, questions)
        if answer == "q":
            break
    show(f"{decided} decided, saved to {path}")
    return decided


def add_question(path: Path, text: str, level: str) -> Question:
    """Add a question written by the annotator. It is validated by construction."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    questions = load_questions(path)
    question = Question(
        id=next_annotator_id(questions),
        question=text.strip(),
        level=level,
        source="annotator",
        status="validated",
    )
    questions.append(question)
    save_questions(path, questions)
    return question
