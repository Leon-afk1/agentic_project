from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from oabench.catalog import Catalog
from oabench.eval.golden import Question
from oabench.eval.metrics import (
    bootstrap_ci,
    conclusive,
    paired_difference,
    recall_at_k,
    reciprocal_rank,
    relevant_set,
    unjudged_at_k,
)
from oabench.eval.pool import (
    Candidate,
    Judgment,
    build_pool,
    judge,
    load_judgments,
    load_pool,
    pending,
    qrels,
    save_pool,
)
from oabench.eval.report import evaluate_engine, format_comparisons, format_table, percentile
from oabench.retrieval.engines import BM25Engine, Ranking


class FixedEngine:
    def __init__(self, name: str, answers: dict[str, list[str]]) -> None:
        self.name = name
        self._answers = answers

    def search(self, query: str, k: int) -> Ranking:
        return [(uuid, 1.0) for uuid in self._answers.get(query, [])[:k]]


def question(qid: str, text: str, status: str = "validated") -> Question:
    return Question.model_validate(
        {"id": qid, "question": text, "level": "easy", "source": "need", "status": status}
    )


def scripted(*answers: str) -> Callable[[str], str]:
    remaining = list(answers)
    return lambda _prompt: remaining.pop(0)


def test_relevant_set_strict_and_lenient() -> None:
    grades = {"a": 2, "b": 1, "c": 0}
    assert relevant_set(grades) == {"a"}
    assert relevant_set(grades, min_grade=1) == {"a", "b"}


def test_recall_at_k_counts_relevant_records_in_the_top_k() -> None:
    assert recall_at_k(["x", "a", "y", "b", "z", "c"], {"a", "b", "c"}, k=5) == pytest.approx(2 / 3)
    assert recall_at_k([], {"a"}) == 0.0


def test_recall_is_capped_when_more_than_k_records_are_relevant() -> None:
    relevant = {f"r{i}" for i in range(12)}
    assert recall_at_k([f"r{i}" for i in range(5)], relevant, k=5) == 1.0


def test_recall_refuses_a_question_without_relevant_record() -> None:
    with pytest.raises(ValueError):
        recall_at_k(["a"], set())


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x"], {"a"}) == 0.0


def test_unjudged_counts_results_nobody_graded() -> None:
    assert unjudged_at_k(["a", "new", "b"], {"a": 2, "b": 0}) == 1


def test_bootstrap_interval_contains_the_mean_and_is_reproducible() -> None:
    values = [0.0, 1.0, 1.0, 0.5, 0.0, 1.0, 0.2, 0.8]
    interval = bootstrap_ci(values, seed=1)
    assert interval.low <= interval.mean <= interval.high
    assert interval.mean == pytest.approx(sum(values) / len(values))
    assert interval == bootstrap_ci(values, seed=1)


def test_bootstrap_interval_narrows_with_more_questions() -> None:
    small = bootstrap_ci([0.0, 1.0] * 5)
    large = bootstrap_ci([0.0, 1.0] * 100)
    assert (large.high - large.low) < (small.high - small.low)


def test_constant_values_give_a_zero_width_interval() -> None:
    interval = bootstrap_ci([0.4] * 10)
    assert (interval.low, interval.high) == (pytest.approx(0.4), pytest.approx(0.4))


def test_paired_difference_detects_a_consistent_gain() -> None:
    baseline = [0.2, 0.4, 0.0, 0.6, 0.2, 0.4, 0.0, 0.2]
    better = [b + 0.2 for b in baseline]
    assert conclusive(paired_difference(better, baseline))
    assert not conclusive(paired_difference(baseline, baseline))
    with pytest.raises(ValueError):
        paired_difference([1.0], [1.0, 0.0])


def test_pool_merges_engines_and_records_who_found_what() -> None:
    questions = [question("q1", "vélo"), question("q2", "eau", status="proposed")]
    engines = [FixedEngine("e1", {"vélo": ["a", "b"]}), FixedEngine("e2", {"vélo": ["b", "c"]})]
    pool = build_pool(questions, engines, depth=2)
    assert [(c.uuid, c.found_by) for c in pool] == [
        ("a", {"e1": 1}),
        ("b", {"e1": 2, "e2": 1}),
        ("c", {"e2": 2}),
    ]


def test_pool_can_be_extended_by_a_later_system_without_losing_candidates() -> None:
    questions = [question("q1", "vélo")]
    first = build_pool(questions, [FixedEngine("e1", {"vélo": ["a"]})])
    extended = build_pool(questions, [FixedEngine("agent", {"vélo": ["z"]})], existing=first)
    assert [c.uuid for c in extended] == ["a", "z"]
    assert extended[0].found_by == {"e1": 1}


def test_judging_saves_each_grade_and_resumes(tmp_path: Path, catalog: Catalog) -> None:
    pool_path, judgments_path = tmp_path / "pool.jsonl", tmp_path / "judgments.jsonl"
    questions = [question("q1", "vélo à Strasbourg")]
    pool = [
        Candidate(question_id="q1", uuid=u, found_by={"bm25": 1})
        for u in ("bike-lanes", "schools", "trap")
    ]
    save_pool(pool_path, pool)
    shown: list[str] = []
    graded = judge(
        pool_path,
        judgments_path,
        questions,
        catalog,
        ask=scripted("x", "m", "2", "q"),
        show=shown.append,
    )
    assert graded == 1
    assert len(pending(load_pool(pool_path), load_judgments(judgments_path))) == 2
    judge(pool_path, judgments_path, questions, catalog, ask=scripted("0", "1"), show=shown.append)
    assert sorted(j.grade for j in load_judgments(judgments_path)) == [0, 1, 2]
    assert pending(load_pool(pool_path), load_judgments(judgments_path)) == []


def test_judging_never_reveals_which_engine_found_a_candidate(
    tmp_path: Path, catalog: Catalog
) -> None:
    pool_path = tmp_path / "pool.jsonl"
    save_pool(
        pool_path, [Candidate(question_id="q1", uuid="schools", found_by={"secret-engine": 1})]
    )
    shown: list[str] = []
    judge(
        pool_path,
        tmp_path / "j.jsonl",
        [question("q1", "écoles")],
        catalog,
        ask=scripted("s"),
        show=shown.append,
    )
    assert "secret-engine" not in "\n".join(shown)
    assert "Écoles primaires" in "\n".join(shown)


def test_qrels_groups_grades_by_question() -> None:
    judgments = [
        Judgment(question_id="q1", uuid="a", grade=2),
        Judgment(question_id="q1", uuid="b", grade=0),
    ]
    assert qrels(judgments) == {"q1": {"a": 2, "b": 0}}


def test_evaluate_engine_scores_answerable_questions_only(catalog: Catalog) -> None:
    questions = [
        question("q1", "pistes cyclables"),
        question("q2", "prix immobilier"),  # judged, nothing relevant: a "no answer" question
        question("q3", "écoles", status="rejected"),
    ]
    grades = {"q1": {"bike-lanes": 2, "trap": 1}, "q2": {"rivers": 0}, "q3": {"schools": 2}}
    report = evaluate_engine(BM25Engine(catalog), questions, grades)
    assert report.recall == {"q1": 1.0}
    assert report.rr == {"q1": 1.0}
    assert len(report.latencies_ms) == 1


def test_evaluate_engine_reports_unjudged_results(catalog: Catalog) -> None:
    report = evaluate_engine(
        BM25Engine(catalog), [question("q1", "vélo Strasbourg")], {"q1": {"bike-lanes": 2}}
    )
    assert report.unjudged >= 1


def test_table_and_comparison_are_rendered(catalog: Catalog) -> None:
    questions = [question(f"q{i}", "vélo") for i in range(6)]
    grades = {q.id: {"a": 2} for q in questions}
    good = evaluate_engine(FixedEngine("good", {"vélo": ["a"]}), questions, grades)
    bad = evaluate_engine(FixedEngine("bm25", {"vélo": ["x", "y"]}), questions, grades)
    table = format_table([good, bad])
    assert "| good | 6 | 1.000 [1.000, 1.000] |" in table
    assert "+1.000" in format_comparisons([good, bad], "bm25")
    assert "conclusive" in format_comparisons([good, bad], "bm25")


def test_percentile_nearest_rank() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 50) == 20.0
    assert percentile(values, 95) == 40.0
