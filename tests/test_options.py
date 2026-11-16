"""Judging options (split, review) and the ranking engine behind the search tool."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp import Client

from oabench.catalog import Catalog
from oabench.eval.golden import Question
from oabench.eval.pool import Candidate, judge, load_judgments, save_pool
from oabench.mcp_server.server import build_server
from oabench.retrieval.engines import Ranking


def question(qid: str, split: str) -> Question:
    return Question.model_validate(
        {"id": qid, "question": qid, "level": "easy", "source": "need", "status": "validated"}
        | {"split": split}
    )


def scripted(*answers: str) -> Callable[[str], str]:
    remaining = list(answers)
    return lambda _prompt: remaining.pop(0)


def make_pool(tmp_path: Path) -> tuple[Path, Path, list[Question]]:
    pool = tmp_path / "pool.jsonl"
    save_pool(
        pool,
        [
            Candidate(question_id="d1", uuid="schools", found_by={}),
            Candidate(question_id="d1", uuid="rivers", found_by={}),
            Candidate(question_id="t1", uuid="trap", found_by={}),
        ],
    )
    return pool, tmp_path / "judgments.jsonl", [question("d1", "dev"), question("t1", "test")]


def test_judging_can_be_restricted_to_one_split(tmp_path: Path, catalog: Catalog) -> None:
    pool, out, questions = make_pool(tmp_path)
    shown: list[str] = []
    graded = judge(
        pool, out, questions, catalog, ask=scripted("2"), show=shown.append, split="test"
    )
    assert graded == 1
    assert [j.question_id for j in load_judgments(out)] == ["t1"]
    assert "1 candidate(s) to judge" in shown[0]


def test_review_mode_shows_the_model_grade_and_enter_accepts_it(
    tmp_path: Path, catalog: Catalog
) -> None:
    pool, out, questions = make_pool(tmp_path)
    shown: list[str] = []
    suggestions = {("d1", "schools"): 2, ("d1", "rivers"): 0}
    judge(
        pool, out, questions, catalog,
        ask=scripted("", "1"), show=shown.append, split="dev", suggestions=suggestions,
    )  # fmt: skip
    assert any("MODEL   graded" in line and "Enter = accept" in line for line in shown)


def test_review_mode_accepts_the_suggestion_exactly(tmp_path: Path, catalog: Catalog) -> None:
    pool, out, questions = make_pool(tmp_path)
    judge(
        pool, out, questions, catalog,
        ask=scripted("", ""), show=lambda _line: None, split="dev",
        suggestions={("d1", "schools"): 2, ("d1", "rivers"): 0},
    )  # fmt: skip
    assert {j.uuid: j.grade for j in load_judgments(out)} == {"schools": 2, "rivers": 0}


def test_blind_mode_never_shows_a_model_grade_and_ignores_enter(
    tmp_path: Path, catalog: Catalog
) -> None:
    pool, out, questions = make_pool(tmp_path)
    shown: list[str] = []
    judge(pool, out, questions, catalog, ask=scripted("", "q"), show=shown.append, split="test")
    assert load_judgments(out) == []
    assert not any("MODEL" in line for line in shown)


class FixedRanker:
    name = "fixed"

    def search(self, query: str, k: int) -> Ranking:
        return [("rivers", 0.9), ("unknown-uuid", 0.8), ("schools", 0.7), ("charging", 0.6)][:k]


def call(catalog: Catalog, ranker: Any, arguments: dict[str, Any]) -> Any:
    async def run() -> Any:
        async with Client(build_server(catalog, ranker)) as client:
            tools = (await client.list_tools()).tools
            result = await client.call_tool("search_datasets", arguments)
            return tools[0].description, result.structured_content

    return asyncio.run(run())


def test_search_tool_uses_the_given_ranker_and_skips_unknown_records(catalog: Catalog) -> None:
    description, data = call(catalog, FixedRanker(), {"query": "anything", "limit": 2})
    assert [r["uuid"] for r in data["results"]] == ["rivers", "schools"]
    assert data["total_matches"] == 3
    assert "keywords and meaning" in description


def test_search_tool_applies_filters_on_top_of_the_ranker(catalog: Catalog) -> None:
    _, data = call(catalog, FixedRanker(), {"query": "x", "topic": "transportation"})
    assert [r["uuid"] for r in data["results"]] == ["charging"]
    _, data = call(catalog, FixedRanker(), {"query": "x", "organization": "strasbourg"})
    assert [r["uuid"] for r in data["results"]] == ["schools"]


def test_default_search_tool_stays_lexical(catalog: Catalog) -> None:
    description, data = call(catalog, None, {"query": "écoles"})
    assert "lexical" in description
    assert data["results"][0]["uuid"] == "schools"
