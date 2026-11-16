"""Behaviours added in agent v2: clarification, grounded suggestions, outage stop."""

from __future__ import annotations

import asyncio
from typing import Any

from oabench.agent.loop import (
    OUTAGE_ANSWER,
    AgentResult,
    Limits,
    ToolOutcome,
    collect_names,
    is_grounded,
    run_agent,
)
from oabench.agent.prompts import AGENT_SYSTEM, AGENT_VERSION
from oabench.agent.runner import ask
from oabench.catalog import Catalog
from oabench.eval.robustness import cases, summarize, to_outcome
from oabench.llm.types import LLMResponse, Message, ToolCall, ToolSpec
from oabench.tracing.tracer import Tracer, render_trace

PLAN = '{"keywords": ["x"]}'


class ScriptedLLM:
    model = "scripted"

    def __init__(self, *responses: LLMResponse) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        force_tool: str | None = None,
    ) -> LLMResponse:
        self.calls += 1
        return self._responses.pop(0)


def say(text: str = "", *calls: ToolCall) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=list(calls), input_tokens=10, output_tokens=5)


def search(query: str) -> ToolCall:
    return ToolCall("search_datasets", {"query": query})


def submit(**arguments: Any) -> ToolCall:
    return ToolCall("submit_answer", {"message": "Message.", **arguments})


def run(catalog: Catalog, llm: ScriptedLLM, **kwargs: Any) -> tuple[AgentResult, Tracer]:
    tracer = Tracer()
    return asyncio.run(ask("question", llm, catalog, tracer=tracer, **kwargs)), tracer


def test_prompt_carries_the_three_new_rules_and_a_version() -> None:
    assert AGENT_VERSION == "v2"
    assert "needs_clarification" in AGENT_SYSTEM
    assert "not a search for data" in AGENT_SYSTEM
    assert "only name a producer you saw" in AGENT_SYSTEM


def test_clarification_is_a_distinct_ending_with_its_question(catalog: Catalog) -> None:
    llm = ScriptedLLM(
        say(PLAN),
        say(
            "",
            submit(
                status="needs_clarification",
                clarification_question="Quel territoire vous intéresse ?",
            ),
        ),
    )
    result, tracer = run(catalog, llm)
    assert result.stop_reason == "needs_clarification"
    assert result.answer is not None
    assert result.answer.clarification_question == "Quel territoire vous intéresse ?"
    assert result.ranking == []
    assert "ASKS      Quel territoire" in render_trace(tracer.events)


def test_clarification_never_comes_with_recommended_datasets(catalog: Catalog) -> None:
    datasets = [{"uuid": "schools", "reason": "au hasard"}]
    llm = ScriptedLLM(
        say(PLAN),
        say("", search("écoles")),
        say("", submit(status="needs_clarification", datasets=datasets)),
    )
    result, _ = run(catalog, llm)
    assert result.stop_reason == "needs_clarification"
    assert result.ranking == []


def test_names_are_collected_from_search_and_organization_results() -> None:
    payload = {
        "results": [{"uuid": "a", "organizations": ["Ville de Strasbourg", "ATMO Grand Est"]}],
        "organizations": [{"name": "Metz Métropole", "datasets": 3}],
    }
    assert collect_names(payload) == {"Ville de Strasbourg", "ATMO Grand Est", "Metz Métropole"}


def test_suggestion_grounding_rules() -> None:
    seen = {"Ville de Strasbourg", "IGN"}
    assert is_grounded("Portail de la ville de strasbourg", seen)  # case and accents ignored
    assert is_grounded("Voir data.gouv.fr, base DVF", set())  # always allowed
    assert is_grounded("Données de l'INSEE", set())
    assert not is_grounded("Geogravity", seen)
    assert not is_grounded("Portail Open Data de Mulhouse", seen)


def test_unverifiable_suggestions_are_removed_and_reported(catalog: Catalog) -> None:
    llm = ScriptedLLM(
        say(PLAN),
        say("", search("écoles")),  # returns a record of "Ville de Strasbourg"
        say(
            "",
            submit(
                status="abstained",
                suggestions=["Ville de Strasbourg", "Geogravity", "data.gouv.fr"],
            ),
        ),
    )
    result, tracer = run(catalog, llm)
    assert result.answer is not None
    assert result.answer.suggestions == ["Ville de Strasbourg", "data.gouv.fr"]
    assert result.removed_suggestions == ["Geogravity"]
    assert tracer.events[-1]["removed_suggestions"] == ["Geogravity"]


def test_outage_stops_after_two_unavailable_results_with_a_fixed_message(
    catalog: Catalog,
) -> None:
    llm = ScriptedLLM(say(PLAN), say("", search("a")), say("", search("b")), say("", search("c")))
    result, _ = run(catalog, llm, simulate_outage=True)
    assert result.stop_reason == "tool_error"
    assert result.tool_calls == 2
    assert llm.calls == 3  # the plan and two search turns: no further model call
    assert result.answer is not None and result.answer.message == OUTAGE_ANSWER
    assert result.ranking == []


def test_a_single_unavailable_result_does_not_stop_the_run() -> None:
    outcomes = iter(
        [
            ToolOutcome({"error": "503"}, is_error=True, unavailable=True),
            ToolOutcome({"results": [{"uuid": "a", "organizations": ["Org"]}]}),
        ]
    )

    async def flaky(_name: str, _arguments: dict[str, Any]) -> ToolOutcome:
        return next(outcomes)

    tool = ToolSpec("search_datasets", "search", {"type": "object", "properties": {}})
    llm = ScriptedLLM(
        say(PLAN),
        say("", search("a")),
        say("", search("b")),
        say("", submit(status="answered", datasets=[{"uuid": "a", "reason": "ok"}])),
    )
    result = asyncio.run(run_agent("q", llm, [tool], flaky))
    assert result.stop_reason == "answered"
    assert result.ranking == ["a"]


def test_a_refused_call_is_not_counted_as_an_outage(catalog: Catalog) -> None:
    missing = ToolCall("get_dataset_metadata", {"uuid": "nope-1"})
    other = ToolCall("get_dataset_metadata", {"uuid": "nope-2"})
    third = ToolCall("get_dataset_metadata", {"uuid": "nope-3"})
    llm = ScriptedLLM(
        say(PLAN),
        say("", missing),
        say("", other),
        say("", third),
        say("", submit(status="abstained")),
    )
    result, _ = run(catalog, llm, limits=Limits())
    assert result.stop_reason == "abstained"
    assert result.tool_calls == 3


def test_ambiguity_summary_counts_clarifications(catalog: Catalog) -> None:
    llm = ScriptedLLM(
        say(PLAN),
        say("", submit(status="needs_clarification", clarification_question="Lequel ?")),
    )
    result, _ = run(catalog, llm)
    outcome = to_outcome(cases("ambiguous")[0], result)
    assert outcome.clarification_question == "Lequel ?"
    assert summarize([outcome])["asked_for_clarification"] == 1
