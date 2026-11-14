from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

from oabench.catalog import Catalog
from oabench.cli import main
from oabench.eval.golden import (
    Question,
    assign_split,
    load_questions,
    masked_view,
    sample_for_masking,
    save_questions,
    summary,
)
from oabench.eval.review import add_question, review
from oabench.snapshot.records import Dataset


def q(qid: str, level: str = "easy", status: str = "validated", **extra: object) -> Question:
    base = {"id": qid, "question": f"question {qid}", "level": level, "source": "need"}
    return Question.model_validate(base | {"status": status} | extra)


def proposed(*ids: str) -> list[Question]:
    return [q(qid, status="proposed") for qid in ids]


def scripted(*answers: str) -> Callable[[str], str]:
    remaining = list(answers)
    return lambda _prompt: remaining.pop(0)


def silent(_line: str) -> None:
    return None


def test_questions_round_trip_through_the_file(tmp_path: Path) -> None:
    path = tmp_path / "q.jsonl"
    questions = [q("n01"), q("m01", seed_uuid="abc", note="x")]
    save_questions(path, questions)
    assert load_questions(path) == questions
    assert load_questions(tmp_path / "missing.jsonl") == []


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        save_questions(tmp_path / "q.jsonl", [q("n01"), q("n01")])


def test_split_is_one_third_dev_within_each_level() -> None:
    questions = [q(f"e{i:02d}", "easy") for i in range(18)]
    questions += [q(f"h{i:02d}", "hard") for i in range(9)]
    assign_split(questions)
    assert Counter((x.level, x.split) for x in questions) == {
        ("easy", "dev"): 6,
        ("easy", "test"): 12,
        ("hard", "dev"): 3,
        ("hard", "test"): 6,
    }


def test_split_ignores_questions_that_are_not_validated() -> None:
    questions = [q("a"), q("b", status="proposed"), q("c", status="rejected")]
    assign_split(questions)
    assert [x.split for x in questions][1:] == [None, None]


def test_split_is_deterministic() -> None:
    first = assign_split([q(f"n{i:02d}") for i in range(30)])
    second = assign_split([q(f"n{i:02d}") for i in reversed(range(30))])
    assert {x.id: x.split for x in first} == {x.id: x.split for x in second}


def test_adding_questions_never_moves_an_existing_one() -> None:
    questions = assign_split([q(f"n{i:02d}") for i in range(30)])
    before = {x.id: x.split for x in questions}
    questions += [q(f"x{i:02d}") for i in range(15)]
    assign_split(questions)
    assert {x.id: x.split for x in questions if x.id in before} == before
    assert Counter(x.split for x in questions) == {"dev": 15, "test": 30}


def test_summary_counts_validated_questions_only() -> None:
    result = summary([q("a"), q("b", "hard"), q("c", status="rejected")])
    assert result["status"] == {"validated": 2, "rejected": 1}
    assert result["level"] == {"easy": 1, "hard": 1}
    assert result["split"] == {"unassigned": 2}


def test_masked_sample_is_deterministic_and_spread_over_topics(datasets: list[Dataset]) -> None:
    first = sample_for_masking(datasets, 3, seed=1, min_description_chars=10)
    assert first == sample_for_masking(datasets, 3, seed=1, min_description_chars=10)
    assert len({d.topics[0] if d.topics else "" for d in first}) == 3


def test_masked_sample_skips_short_descriptions(datasets: list[Dataset]) -> None:
    chosen = sample_for_masking(datasets, 10, seed=1, min_description_chars=200)
    assert [d.uuid for d in chosen] == ["bike-lanes"]


def test_masked_view_hides_title_id_and_links(datasets: list[Dataset]) -> None:
    dumped = masked_view(datasets[0], "M01").model_dump_json()
    assert datasets[0].uuid not in dumped
    assert datasets[0].title not in dumped
    assert "https://" not in dumped


def test_review_accepts_edits_relevels_and_rejects(tmp_path: Path, catalog: Catalog) -> None:
    path = tmp_path / "q.jsonl"
    save_questions(path, proposed("n01", "n02", "n03"))
    shown: list[str] = []
    answers = scripted("e", "écoles de Strasbourg", "l", "medium", "a", "r", "s")
    assert review(path, catalog=catalog, ask=answers, show=shown.append) == 2
    first, second, third = load_questions(path)
    assert (first.question, first.level) == ("écoles de Strasbourg", "medium")
    assert [first.status, second.status, third.status] == ["validated", "rejected", "proposed"]
    assert any("BM25 preview" in line for line in shown)


def test_review_quit_saves_and_stops(tmp_path: Path) -> None:
    path = tmp_path / "q.jsonl"
    save_questions(path, proposed("n01", "n02"))
    assert review(path, ask=scripted("a", "q"), show=silent) == 1
    assert [x.status for x in load_questions(path)] == ["validated", "proposed"]


def test_review_never_validates_without_an_explicit_answer(tmp_path: Path) -> None:
    path = tmp_path / "q.jsonl"
    save_questions(path, proposed("n01"))
    review(path, ask=scripted("", "x", "s"), show=silent)
    assert load_questions(path)[0].status == "proposed"


def test_add_question_numbers_annotator_questions(tmp_path: Path) -> None:
    path = tmp_path / "q.jsonl"
    save_questions(path, [q("n01")])
    assert add_question(path, "  ma question  ", "hard").id == "a01"
    added = add_question(path, "autre", "easy")
    assert (added.id, added.source, added.status) == ("a02", "annotator", "validated")
    with pytest.raises(ValueError):
        add_question(path, "x", "impossible")


def test_cli_add_split_and_stats(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "q.jsonl"
    base = ["golden", "--file", str(path)]
    assert main([*base, "add", "ma question", "--level", "easy"]) == 0
    assert main([*base, "split"]) == 0
    assert main([*base, "stats"]) == 0
    assert load_questions(path)[0].split in ("dev", "test")
    assert "validated=1" in capsys.readouterr().out
