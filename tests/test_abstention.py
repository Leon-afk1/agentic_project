from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

import pytest

from oabench.catalog import Catalog
from oabench.eval.abstention import (
    choose_threshold,
    evaluate_abstention,
    format_abstention,
    rates,
    split_by_answerability,
)
from oabench.eval.golden import Question
from oabench.eval.pool import Candidate, judge, load_judgments, save_pool
from oabench.retrieval.engines import HybridEngine, Ranking


class ScoreEngine:
    """Returns one fixed result per question text, with a chosen score."""

    name = "scores"

    def __init__(self, scores: dict[str, float | None]) -> None:
        self._scores = scores

    def search(self, query: str, k: int) -> Ranking:
        score = self._scores.get(query)
        return [] if score is None else [("x", score)]


class ListEngine:
    def __init__(self, name: str, uuids: list[str]) -> None:
        self.name = name
        self._uuids = uuids

    def search(self, query: str, k: int) -> Ranking:
        return [(uuid, 100.0 - i) for i, uuid in enumerate(self._uuids)][:k]


def question(qid: str, split: str | None = None) -> Question:
    return Question.model_validate(
        {"id": qid, "question": qid, "level": "easy", "source": "need", "status": "validated"}
        | ({"split": split} if split else {})
    )


def scripted(*answers: str) -> Callable[[str], str]:
    remaining = list(answers)
    return lambda _prompt: remaining.pop(0)


def test_questions_are_split_by_answerability_and_unjudged_ones_are_left_out() -> None:
    questions = [question("yes"), question("no"), question("unjudged")]
    qrels = {"yes": {"a": 2}, "no": {"a": 1, "b": 0}}
    answerable, no_answer = split_by_answerability(questions, qrels)
    assert [q.id for q in answerable] == ["yes"]
    assert [q.id for q in no_answer] == ["no"]
    lenient, _ = split_by_answerability(questions, qrels, min_grade=1)
    assert [q.id for q in lenient] == ["yes", "no"]


def test_rates_count_abstentions_in_each_group() -> None:
    correct, wrongful = rates([10.0, 20.0, 30.0, 40.0], [5.0, 25.0], threshold=22.0)
    assert (correct, wrongful) == (0.5, 0.5)


def test_an_engine_that_returns_nothing_always_abstains() -> None:
    correct, wrongful = rates([-math.inf], [-math.inf], threshold=-math.inf)
    assert (correct, wrongful) == (1.0, 1.0)


def test_threshold_separates_two_clean_groups() -> None:
    threshold = choose_threshold([30.0, 40.0, 50.0], [5.0, 10.0])
    assert 10.0 < threshold < 30.0
    assert rates([30.0, 40.0, 50.0], [5.0, 10.0], threshold) == (1.0, 0.0)


def test_threshold_prefers_answering_when_scores_cannot_separate() -> None:
    assert choose_threshold([10.0, 10.0], [10.0]) == -math.inf


def test_threshold_is_never_tuned_without_both_groups() -> None:
    assert choose_threshold([10.0, 20.0], []) == -math.inf


def test_threshold_is_tuned_on_dev_and_applied_unchanged_to_the_target() -> None:
    dev = [question("d-yes", "dev"), question("d-no", "dev")]
    test = [question("t-yes", "test"), question("t-no", "test"), question("t-no2", "test")]
    qrels = {
        "d-yes": {"a": 2},
        "d-no": {"a": 0},
        "t-yes": {"a": 2},
        "t-no": {"a": 0},
        "t-no2": {"a": 0},
    }
    engine = ScoreEngine({"d-yes": 40.0, "d-no": 10.0, "t-yes": 20.0, "t-no": 12.0, "t-no2": 60.0})
    report = evaluate_abstention(engine, dev, test, qrels)
    assert report.threshold == pytest.approx(25.0)
    # On test the threshold is wrong twice: that is the point of not tuning on it.
    assert (report.correct_abstention, report.wrongful_abstention) == (0.5, 1.0)
    assert (report.no_answer_questions, report.answerable_questions) == (2, 1)
    assert "50% of 2" in format_abstention([report])


def test_hybrid_fuses_ranks_and_rewards_agreement() -> None:
    lexical = ListEngine("lexical", ["a", "b", "c"])
    dense = ListEngine("dense", ["c", "b", "d"])
    ranking = HybridEngine([lexical, dense]).search("anything", 4)
    assert [uuid for uuid, _ in ranking] == ["c", "b", "a", "d"]
    assert ranking[0][1] == pytest.approx(1 / 61 + 1 / 63)  # ranks 1 and 3
    assert ranking[1][1] == pytest.approx(2 / 62)  # rank 2 twice


def test_hybrid_with_one_empty_engine_falls_back_to_the_other() -> None:
    ranking = HybridEngine([ListEngine("a", ["x", "y"]), ListEngine("b", [])]).search("q", 5)
    assert [uuid for uuid, _ in ranking] == ["x", "y"]


def test_judging_handles_dev_questions_first_and_can_undo(tmp_path: Path, catalog: Catalog) -> None:
    pool_path, judgments_path = tmp_path / "pool.jsonl", tmp_path / "judgments.jsonl"
    questions = [question("a-test", "test"), question("z-dev", "dev")]
    save_pool(
        pool_path,
        [
            Candidate(question_id="a-test", uuid="rivers", found_by={}),
            Candidate(question_id="z-dev", uuid="schools", found_by={}),
        ],
    )
    shown: list[str] = []
    answers = scripted("u", "2", "u", "0", "1")
    assert judge(pool_path, judgments_path, questions, catalog, ask=answers, show=shown.append) == 2
    saved = load_judgments(judgments_path)
    assert [(j.question_id, j.grade) for j in saved] == [("z-dev", 0), ("a-test", 1)]
    assert any("nothing to undo" in line for line in shown)
    assert any("candidate 1/2" in line for line in shown)
