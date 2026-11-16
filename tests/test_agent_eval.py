from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from oabench.agent.answer import AgentAnswer, Citation
from oabench.agent.loop import AgentResult
from oabench.eval.agent_eval import (
    AgentRun,
    format_agent_report,
    load_runs,
    run_questions,
    unjudged_pairs,
)
from oabench.eval.agreement import compare, format_agreement, weighted_kappa
from oabench.eval.golden import Question
from oabench.eval.pool import Judgment


def question(qid: str) -> Question:
    return Question.model_validate(
        {"id": qid, "question": qid, "level": "easy", "source": "need", "status": "validated"}
    )


def result(stop: str, *uuids: str) -> AgentResult:
    answer = None
    if stop in ("answered", "abstained"):
        answer = AgentAnswer(
            status=stop,  # type: ignore[arg-type]
            message="msg",
            datasets=[Citation(uuid=u) for u in uuids],
        )
    return AgentResult(
        question="q", stop_reason=stop, answer=answer, steps=3, tool_calls=2,  # type: ignore[arg-type]
        input_tokens=100, output_tokens=10, cost_usd=0.001, seconds=2.0,
    )  # fmt: skip


def judgments(*rows: tuple[str, str, int]) -> list[Judgment]:
    return [Judgment(question_id=q, uuid=u, grade=g) for q, u, g in rows]  # type: ignore[arg-type]


def test_kappa_is_one_for_identical_annotators_and_zero_at_chance() -> None:
    assert weighted_kappa([[5, 0, 0], [0, 5, 0], [0, 0, 5]]) == pytest.approx(1.0)
    assert weighted_kappa([[4, 4, 4], [4, 4, 4], [4, 4, 4]]) == pytest.approx(0.0)


def test_kappa_matches_a_hand_computed_case() -> None:
    # 2 grades, 10 pairs: observed disagreement 0.2; marginals 0.5/0.5 and 0.7/0.3
    # give an expected disagreement of 0.5*0.3 + 0.5*0.7 = 0.5, hence 1 - 0.2/0.5.
    assert weighted_kappa([[5, 0], [2, 3]]) == pytest.approx(0.6)


def test_weighted_kappa_penalises_distant_disagreements_more() -> None:
    near = weighted_kappa([[5, 2, 0], [0, 5, 0], [0, 0, 5]])
    far = weighted_kappa([[5, 0, 2], [0, 5, 0], [0, 0, 5]])
    assert near > far


def test_compare_uses_only_pairs_graded_by_both() -> None:
    first = judgments(("q1", "a", 2), ("q1", "b", 0), ("q1", "c", 1))
    second = judgments(("q1", "a", 2), ("q1", "b", 1), ("q2", "z", 0))
    outcome = compare(first, second)
    assert outcome.pairs == 2
    assert outcome.raw == 0.5
    assert outcome.matrix[0][1] == 1 and outcome.matrix[2][2] == 1
    assert "weighted kappa" in format_agreement(outcome, "human", "llm")
    with pytest.raises(ValueError):
        compare(first, judgments(("q9", "x", 0)))


def test_runs_are_saved_one_by_one_and_resumed(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    asked: list[str] = []

    async def ask_one(q: Question) -> AgentResult:
        asked.append(q.id)
        return result("answered", "a")

    questions = [question("q1"), question("q2")]
    asyncio.run(run_questions(questions[:1], "m", ask_one, path))
    runs = asyncio.run(run_questions(questions, "m", ask_one, path))
    assert asked == ["q1", "q2"]  # q1 was not asked a second time
    assert [r.question_id for r in runs] == ["q1", "q2"]
    assert len(load_runs(path)) == 2


def test_provider_failure_is_not_recorded_and_stops_the_batch(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    outcomes = iter([result("answered", "a"), result("llm_error"), result("answered", "b")])

    async def ask_one(_q: Question) -> AgentResult:
        return next(outcomes)

    runs = asyncio.run(
        run_questions([question("q1"), question("q2"), question("q3")], "m", ask_one, path)
    )
    assert [r.question_id for r in runs] == ["q1"]
    assert [r.question_id for r in load_runs(path)] == ["q1"]


def run(qid: str, stop: str, *ranking: str, invented: int = 0) -> AgentRun:
    return AgentRun(
        question_id=qid, model="m", stop_reason=stop, ranking=list(ranking), steps=4,
        tool_calls=3, input_tokens=1000, output_tokens=50, cost_usd=0.002, seconds=3.0,
        invented_citations=["x"] * invented,
    )  # fmt: skip


def test_agent_report_scores_ranking_abstention_and_costs() -> None:
    questions = [question("yes1"), question("yes2"), question("no1")]
    qrels = {"yes1": {"a": 2, "b": 0}, "yes2": {"c": 2}, "no1": {"d": 0}}
    runs = [
        run("yes1", "answered", "b", "a"),  # relevant at rank 2
        run("yes2", "abstained"),  # wrongful abstention
        run("no1", "max_steps", invented=1),  # stopped without answer: counts as abstaining
    ]
    report = format_agent_report(runs, questions, qrels)
    assert "answerable questions scored : 2" in report
    assert "recall@5 [95% CI]           : 0.500" in report
    assert "MRR [95% CI]                : 0.250" in report
    assert "correct abstention          : 100% of 1" in report
    assert "wrongful abstention         : 50% of 2" in report
    assert "invented citations          : 1" in report
    assert "'max_steps': 1" in report


def test_unjudged_pairs_lists_cited_records_nobody_graded() -> None:
    runs = [run("q1", "answered", "a", "new")]
    assert unjudged_pairs(runs, {"q1": {"a": 2}}) == [("q1", "new")]
