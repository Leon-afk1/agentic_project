"""Run the agent over a set of questions and score it like the other systems.

One JSON line per question is written as soon as it finishes, so that a run cut
short by the daily quota can be resumed: questions already done are skipped.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel

from oabench.agent.loop import AgentResult
from oabench.eval.abstention import split_by_answerability
from oabench.eval.golden import Question
from oabench.eval.metrics import (
    bootstrap_ci,
    recall_at_k,
    reciprocal_rank,
    relevant_set,
    unjudged_at_k,
)
from oabench.eval.report import K, percentile


class AgentRun(BaseModel):
    question_id: str
    model: str
    stop_reason: str
    ranking: list[str]
    steps: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    seconds: float
    invented_citations: list[str] = []
    message: str = ""


def load_runs(path: Path) -> list[AgentRun]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [AgentRun.model_validate_json(line) for line in handle if line.strip()]


def to_run(question: Question, model: str, result: AgentResult) -> AgentRun:
    return AgentRun(
        question_id=question.id,
        model=model,
        stop_reason=result.stop_reason,
        ranking=result.ranking,
        steps=result.steps,
        tool_calls=result.tool_calls,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        seconds=round(result.seconds, 2),
        invented_citations=result.invented_citations,
        message=result.answer.message if result.answer else "",
    )


async def run_questions(
    questions: Sequence[Question],
    model: str,
    ask_one: Callable[[Question], Awaitable[AgentResult]],
    path: Path,
    *,
    on_done: Callable[[AgentRun], None] | None = None,
) -> list[AgentRun]:
    done = {run.question_id: run for run in load_runs(path)}
    path.parent.mkdir(parents=True, exist_ok=True)
    for question in questions:
        if question.id in done:
            continue
        run = to_run(question, model, await ask_one(question))
        # A provider failure (quota, network) is not a result: do not record it,
        # so that the question is tried again at the next launch.
        if run.stop_reason == "llm_error":
            if on_done:
                on_done(run)
            break
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(run.model_dump_json() + "\n")
        done[question.id] = run
        if on_done:
            on_done(run)
    return [done[q.id] for q in questions if q.id in done]


def unjudged_pairs(
    runs: Sequence[AgentRun], qrels: Mapping[str, Mapping[str, int]]
) -> list[tuple[str, str]]:
    """(question, record) pairs in the agent's top 5 that nobody graded yet."""
    return [
        (run.question_id, uuid)
        for run in runs
        for uuid in run.ranking[:K]
        if uuid not in qrels.get(run.question_id, {})
    ]


def format_agent_report(
    runs: Sequence[AgentRun],
    questions: Sequence[Question],
    qrels: Mapping[str, Mapping[str, int]],
    *,
    min_grade: int = 2,
) -> str:
    by_id = {run.question_id: run for run in runs}
    answerable, no_answer = split_by_answerability(questions, qrels, min_grade)
    scored = [q for q in answerable if q.id in by_id]
    lines = [f"agent runs: {len(runs)} of {len(questions)} questions"]
    if scored:
        recalls, rrs, unjudged = [], [], 0
        for question in scored:
            run, grades = by_id[question.id], qrels[question.id]
            relevant = relevant_set(grades, min_grade)
            recalls.append(recall_at_k(run.ranking, relevant, K))
            rrs.append(reciprocal_rank(run.ranking, relevant))
            unjudged += unjudged_at_k(run.ranking, grades, K)
        lines += [
            f"answerable questions scored : {len(scored)}",
            f"recall@5 [95% CI]           : {bootstrap_ci(recalls)}",
            f"MRR [95% CI]                : {bootstrap_ci(rrs)}",
            f"unjudged results in top 5   : {unjudged}",
        ]

    def abstained(run: AgentRun) -> bool:
        return not run.ranking  # explicit abstention, or stopped without an answer

    no_runs = [by_id[q.id] for q in no_answer if q.id in by_id]
    yes_runs = [by_id[q.id] for q in answerable if q.id in by_id]
    if no_runs:
        share = sum(abstained(r) for r in no_runs) / len(no_runs)
        lines.append(f"correct abstention          : {share:.0%} of {len(no_runs)}")
    if yes_runs:
        share = sum(abstained(r) for r in yes_runs) / len(yes_runs)
        lines.append(f"wrongful abstention         : {share:.0%} of {len(yes_runs)}")
    if runs:
        costs = [r.cost_usd for r in runs if r.cost_usd is not None]
        seconds = [r.seconds for r in runs]
        calls = [r.tool_calls for r in runs]
        mean_cost = sum(costs) / len(costs) if costs else math.nan
        lines += [
            f"invented citations          : {sum(len(r.invented_citations) for r in runs)}",
            f"list-price cost per question: {mean_cost:.5f} USD (total {sum(costs):.3f})",
            f"latency p50 / p95           : {percentile(seconds, 50):.1f} s / "
            f"{percentile(seconds, 95):.1f} s (rate-limit waits excluded)",
            f"tool calls mean / max       : {sum(calls) / len(calls):.1f} / {max(calls)}",
            f"model calls mean            : {sum(r.steps for r in runs) / len(runs):.1f}",
            f"stop reasons                : {dict(Counter(r.stop_reason for r in runs))}",
        ]
    return "\n".join(lines)


def dump_summary(runs: Sequence[AgentRun]) -> str:
    return json.dumps([run.model_dump() for run in runs], ensure_ascii=False)
