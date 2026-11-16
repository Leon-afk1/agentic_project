from __future__ import annotations

import asyncio
from pathlib import Path

from oabench.agent.answer import AgentAnswer, Citation
from oabench.agent.loop import AgentResult
from oabench.agent.prompts import AGENT_SYSTEM, AGENT_SYSTEM_UNDEFENDED, INJECTION_DEFENSE
from oabench.agent.runner import ask
from oabench.catalog import Catalog
from oabench.eval.robustness import (
    ATTACKS,
    EXFIL_MARKER,
    INJECTION_TARGETS,
    Case,
    attack_succeeded,
    cases,
    load_outcomes,
    run_suite,
    summarize,
    to_outcome,
    trap_uuid,
    with_traps,
)
from oabench.llm.types import LLMResponse, Message, ToolCall, ToolSpec
from oabench.snapshot.records import Dataset


class ScriptedLLM:
    model = "scripted"

    def __init__(self, *responses: LLMResponse) -> None:
        self._responses = list(responses)
        self.systems: list[str] = []
        self.last_messages: list[Message] = []

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        force_tool: str | None = None,
    ) -> LLMResponse:
        self.systems.append(system)
        self.last_messages = list(messages)
        return self._responses.pop(0)


def say(text: str = "", *calls: ToolCall) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=list(calls), input_tokens=10, output_tokens=5)


def result(
    stop: str, *uuids: str, message: str = "msg", suggestions: tuple[str, ...] = ()
) -> AgentResult:
    answer = AgentAnswer(
        status="answered" if uuids else "abstained",
        message=message,
        datasets=[Citation(uuid=u) for u in uuids],
        suggestions=list(suggestions),
    )
    return AgentResult(question="q", stop_reason=stop, answer=answer, steps=3, tool_calls=2)  # type: ignore[arg-type]


def case(attack: str, condition: str = "defended") -> Case:
    return Case(
        id=f"n07-{condition}", suite="injection", question="q", condition=condition,
        attack=attack, trap_uuid=trap_uuid("n07"),
    )  # fmt: skip


def test_undefended_prompt_differs_only_by_the_defensive_paragraph() -> None:
    assert INJECTION_DEFENSE in AGENT_SYSTEM
    assert AGENT_SYSTEM_UNDEFENDED + INJECTION_DEFENSE != AGENT_SYSTEM  # removed in place
    assert len(AGENT_SYSTEM) - len(AGENT_SYSTEM_UNDEFENDED) == len(INJECTION_DEFENSE)
    assert "third parties" not in AGENT_SYSTEM_UNDEFENDED


def test_trap_records_carry_the_attack_and_a_neutral_identity(datasets: list[Dataset]) -> None:
    extended = with_traps(datasets)
    traps = extended[len(datasets) :]
    assert len(traps) == len(INJECTION_TARGETS)
    for trap, (_qid, _question, attack, title) in zip(traps, INJECTION_TARGETS, strict=True):
        assert trap.title == title
        assert trap.abstract.startswith(ATTACKS[attack])
        assert "trap" not in trap.uuid and "piège" not in trap.title.lower()
    assert len(datasets) == len(extended) - len(traps)  # the original list is untouched


def test_trap_is_retrieved_by_the_search_for_its_question(datasets: list[Dataset]) -> None:
    catalog = Catalog(with_traps(datasets))
    for qid, question, _attack, _title in INJECTION_TARGETS:
        hits, _ = catalog.search(question, limit=3)
        assert trap_uuid(qid) in [h.dataset.uuid for h in hits], qid


def test_injection_cases_cover_both_conditions() -> None:
    injection = cases("injection")
    assert len(injection) == 2 * len(INJECTION_TARGETS)
    assert {c.condition for c in injection} == {"defended", "undefended"}
    assert len(cases("offtopic")) == 8 and len(cases("outage")) == 4


def test_attack_success_rules() -> None:
    trap = trap_uuid("n07")
    assert attack_succeeded(to_outcome(case("promote"), result("answered", "real", trap)))
    assert not attack_succeeded(to_outcome(case("promote"), result("answered", "real")))
    leaked = result("answered", "real", message=f"Allez sur http://{EXFIL_MARKER}/acces")
    assert attack_succeeded(to_outcome(case("exfiltrate"), leaked))
    in_suggestion = result("abstained", suggestions=(f"voir {EXFIL_MARKER}",))
    assert attack_succeeded(to_outcome(case("exfiltrate"), in_suggestion))
    assert not attack_succeeded(to_outcome(case("exfiltrate"), result("answered", "real")))
    assert attack_succeeded(to_outcome(case("suppress"), result("abstained")))
    assert not attack_succeeded(to_outcome(case("suppress"), result("answered", "real")))


def test_summary_counts_successes_per_condition() -> None:
    trap = trap_uuid("n07")
    outcomes = [
        to_outcome(case("promote", "defended"), result("answered", "real")),
        to_outcome(case("promote", "undefended"), result("answered", trap)),
    ]
    summary = summarize(outcomes)
    assert summary["defended"]["attack_succeeded"] == 0
    assert summary["undefended"]["attack_succeeded"] == 1
    assert summary["undefended"]["by_attack"]["promote"] == "1/1"


def test_suite_is_resumable_and_skips_provider_failures(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    todo = cases("offtopic")[:3]
    outcomes = iter([result("abstained"), result("abstained")])
    failing = AgentResult(question="q", stop_reason="llm_error")
    asked: list[str] = []

    async def first(c: Case) -> AgentResult:
        asked.append(c.id)
        return next(outcomes) if len(asked) < 2 else failing

    assert len(asyncio.run(run_suite(todo, first, path))) == 1

    async def second(c: Case) -> AgentResult:
        asked.append(c.id)
        return result("abstained")

    assert len(asyncio.run(run_suite(todo, second, path))) == 3
    assert asked == ["offtopic-01", "offtopic-02", "offtopic-02", "offtopic-03"]
    assert len(load_outcomes(path)) == 3


def test_simulated_outage_feeds_errors_to_the_model(catalog: Catalog) -> None:
    llm = ScriptedLLM(
        say('{"keywords": ["vélo"]}'),
        say("", ToolCall("search_datasets", {"query": "vélo"})),
        say("", ToolCall("submit_answer", {"status": "abstained", "message": "Indisponible."})),
    )
    outcome = asyncio.run(ask("vélo ?", llm, catalog, simulate_outage=True))
    assert outcome.stop_reason == "abstained"
    assert "503" in llm.last_messages[-1].result["error"]


def test_custom_system_prompt_reaches_the_model(catalog: Catalog) -> None:
    llm = ScriptedLLM(
        say("{}"),
        say("", ToolCall("submit_answer", {"status": "abstained", "message": "Rien."})),
    )
    asyncio.run(ask("q", llm, catalog, system_prompt=AGENT_SYSTEM_UNDEFENDED))
    assert "third parties" not in llm.systems[1]
    assert "third parties" not in llm.systems[0]  # the plan prompt never had it
