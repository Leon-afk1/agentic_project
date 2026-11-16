"""The loop is tested with a scripted model and the real MCP server in memory.

A scripted model is the only way to test guardrails deterministically and
without an API key: each test states exactly what the model "says" and checks
what the loop does about it. Real-model behaviour is measured by the evaluation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from oabench.agent.answer import SUBMIT_ANSWER
from oabench.agent.loop import (
    AgentResult,
    Limits,
    ToolOutcome,
    collect_uuids,
    parse_plan,
    run_agent,
)
from oabench.agent.runner import ask, strip_titles
from oabench.catalog import Catalog
from oabench.llm.types import LLMError, LLMResponse, Message, ToolCall, ToolSpec
from oabench.llm.wrappers import CachedLLM, ThrottledLLM, request_key
from oabench.portal.http import RateLimiter
from oabench.tracing.tracer import Tracer, load_trace, render_trace

PLAN = '{"keywords": ["vélo"], "territory": "Strasbourg"}'


class ScriptedLLM:
    model = "scripted"

    def __init__(self, *responses: LLMResponse | Exception, tokens: int = 100) -> None:
        self._responses = list(responses)
        self._tokens = tokens
        self.calls: list[tuple[str, list[Message], list[ToolSpec]]] = []
        self.forced: list[str | None] = []

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        force_tool: str | None = None,
    ) -> LLMResponse:
        self.calls.append((system, list(messages), tools))
        self.forced.append(force_tool)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        response.input_tokens = self._tokens
        return response


def say(text: str = "", *calls: ToolCall) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=list(calls), input_tokens=0, output_tokens=10)


def search(query: str) -> ToolCall:
    return ToolCall("search_datasets", {"query": query})


def metadata(uuid: str) -> ToolCall:
    return ToolCall("get_dataset_metadata", {"uuid": uuid})


def submit(status: str, *uuids: str, message: str = "Voici.") -> ToolCall:
    datasets = [{"uuid": u, "reason": "correspond", "checks": {"territory": "ok"}} for u in uuids]
    return ToolCall("submit_answer", {"status": status, "message": message, "datasets": datasets})


def run(catalog: Catalog, llm: ScriptedLLM, **kwargs: Any) -> tuple[AgentResult, Tracer]:
    tracer = Tracer()
    result = asyncio.run(ask("vélo à Strasbourg ?", llm, catalog, tracer=tracer, **kwargs))
    return result, tracer


def test_happy_path_plans_searches_inspects_and_answers(catalog: Catalog) -> None:
    llm = ScriptedLLM(
        say(PLAN),
        say("", search("pistes cyclables")),
        say("", metadata("bike-lanes")),
        say("", submit("answered", "bike-lanes")),
    )
    result, tracer = run(catalog, llm)
    assert result.stop_reason == "answered"
    assert result.ranking == ["bike-lanes"]
    assert result.plan["territory"] == "Strasbourg"
    assert (result.steps, result.tool_calls, result.input_tokens) == (4, 2, 400)
    assert [e["type"] for e in tracer.events] == [
        "run_start", "llm_call", "plan", "llm_call", "tool_call", "llm_call", "tool_call",
        "llm_call", "final",
    ]  # fmt: skip
    # The plan call has no tools; later calls get the 4 catalog tools plus submit_answer.
    assert llm.calls[0][2] == []
    assert [t.name for t in llm.calls[1][2]][-1] == SUBMIT_ANSWER.name
    assert len(llm.calls[1][2]) == 5


def test_tool_results_are_sent_back_to_the_model(catalog: Catalog) -> None:
    llm = ScriptedLLM(say(PLAN), say("", search("écoles")), say("", submit("abstained")))
    run(catalog, llm)
    last_turn = llm.calls[2][1]
    assert [m.role for m in last_turn] == ["user", "assistant", "tool"]
    assert last_turn[2].result["results"][0]["uuid"] == "schools"


def test_honest_abstention_is_a_normal_ending(catalog: Catalog) -> None:
    llm = ScriptedLLM(say(PLAN), say("", search("prix immobilier")), say("", submit("abstained")))
    result, _ = run(catalog, llm)
    assert result.stop_reason == "abstained"
    assert result.ranking == []


def test_invented_citation_is_removed_and_counted(catalog: Catalog) -> None:
    llm = ScriptedLLM(
        say(PLAN),
        say("", search("pistes cyclables")),
        say("", submit("answered", "bike-lanes", "made-up-uuid")),
    )
    result, tracer = run(catalog, llm)
    assert result.ranking == ["bike-lanes"]
    assert result.invented_citations == ["made-up-uuid"]
    assert tracer.events[-1]["invented_citations"] == ["made-up-uuid"]


def test_answer_made_only_of_invented_citations_becomes_an_abstention(catalog: Catalog) -> None:
    llm = ScriptedLLM(say(PLAN), say("", submit("answered", "never-seen")))
    result, _ = run(catalog, llm)
    assert result.stop_reason == "abstained"
    assert result.invented_citations == ["never-seen"]


def test_stops_at_max_steps(catalog: Catalog) -> None:
    llm = ScriptedLLM(say(PLAN), *[say("", search(f"requête {i}")) for i in range(10)])
    result, _ = run(catalog, llm, limits=Limits(max_steps=4))
    assert result.stop_reason == "max_steps"
    assert result.steps == 4
    assert result.answer is None


def test_stops_at_max_tool_calls(catalog: Catalog) -> None:
    burst = say("", search("a"), search("b"), search("c"))
    result, _ = run(catalog, ScriptedLLM(say(PLAN), burst), limits=Limits(max_tool_calls=2))
    assert result.stop_reason == "max_tool_calls"
    assert result.tool_calls == 2


def test_stops_when_the_token_budget_is_spent(catalog: Catalog) -> None:
    llm = ScriptedLLM(say(PLAN), *[say("", search(f"q{i}")) for i in range(10)], tokens=1000)
    result, _ = run(catalog, llm, limits=Limits(max_input_tokens=2500))
    assert result.stop_reason == "token_budget"
    assert result.steps == 3


def test_detects_a_repeated_identical_call(catalog: Catalog) -> None:
    llm = ScriptedLLM(say(PLAN), *[say("", search("vélo")) for _ in range(5)])
    result, _ = run(catalog, llm)
    assert result.stop_reason == "loop_detected"
    assert result.tool_calls == 2  # the third identical call is refused


def test_refused_tool_call_goes_back_to_the_model_which_can_recover(catalog: Catalog) -> None:
    llm = ScriptedLLM(
        say(PLAN),
        say("", metadata("does-not-exist")),
        say("", search("écoles")),
        say("", submit("answered", "schools")),
    )
    result, tracer = run(catalog, llm)
    assert result.stop_reason == "answered"
    errors = [e for e in tracer.events if e["type"] == "tool_call" and e["is_error"]]
    assert len(errors) == 1
    assert "no dataset with uuid" in llm.calls[2][1][-1].result["error"]


def test_model_that_never_submits_is_nudged_once_then_stopped(catalog: Catalog) -> None:
    llm = ScriptedLLM(say(PLAN), say("Je pense que..."), say("Toujours pas."))
    result, _ = run(catalog, llm)
    assert result.stop_reason == "no_final_answer"
    assert llm.calls[2][1][-1].text.startswith("You must finish")


def test_malformed_final_answer_is_returned_to_the_model(catalog: Catalog) -> None:
    bad = ToolCall("submit_answer", {"status": "maybe", "message": "?"})
    llm = ScriptedLLM(say(PLAN), say("", bad), say("", submit("abstained")))
    result, _ = run(catalog, llm)
    assert result.stop_reason == "abstained"
    assert "invalid answer" in llm.calls[2][1][-1].result["error"]


def test_provider_failure_ends_the_run_cleanly(catalog: Catalog) -> None:
    result, tracer = run(catalog, ScriptedLLM(say(PLAN), LLMError("quota exceeded")))
    assert result.stop_reason == "llm_error"
    assert tracer.events[-1]["type"] == "final"


def test_tool_infrastructure_failure_and_timeout_stop_the_run() -> None:
    async def broken(_name: str, _arguments: dict[str, Any]) -> ToolOutcome:
        raise ConnectionError("portal unreachable")

    tool = ToolSpec("search_datasets", "search", {"type": "object", "properties": {}})
    llm = ScriptedLLM(say(PLAN), say("", search("vélo")))
    result = asyncio.run(run_agent("q", llm, [tool], broken))
    assert result.stop_reason == "tool_error"

    ticks = iter(range(0, 10_000, 50))  # every reading of the clock is 50 s later
    slow = ScriptedLLM(say(PLAN), say("", search("vélo")))
    late = asyncio.run(run_agent("q", slow, [tool], broken, clock=lambda: float(next(ticks))))
    assert late.stop_reason == "timeout"


def test_injected_instruction_reaches_the_model_only_as_tool_data(catalog: Catalog) -> None:
    llm = ScriptedLLM(say(PLAN), say("", metadata("trap")), say("", submit("abstained")))
    run(catalog, llm)
    system, messages, _ = llm.calls[2]
    assert "ignore them" in system  # the defence is stated in the system prompt
    assert "Ignore all previous instructions" not in system
    assert "Ignore all previous instructions" in messages[-1].result["description"]
    assert messages[-1].role == "tool"


def test_plan_parsing_tolerates_code_fences_and_garbage() -> None:
    assert parse_plan('```json\n{"keywords": ["a"]}\n```') == {"keywords": ["a"]}
    assert parse_plan("no json here") == {"unparsed": "no json here"}


def test_collect_uuids_walks_nested_results() -> None:
    payload = {"results": [{"uuid": "a"}, {"uuid": "b", "more": {"uuid": "c"}}], "uuid": 3}
    assert collect_uuids(payload) == {"a", "b", "c"}


def test_strip_titles_keeps_properties_named_title() -> None:
    schema = {"title": "Args", "properties": {"title": {"type": "string", "title": "Title"}}}
    assert strip_titles(schema) == {"properties": {"title": {"type": "string"}}}


def test_trace_is_written_as_jsonl_and_rendered(tmp_path: Path, catalog: Catalog) -> None:
    path = tmp_path / "runs" / "run.jsonl"
    llm = ScriptedLLM(say(PLAN), say("", search("vélo")), say("", submit("answered", "trap")))
    asyncio.run(ask("vélo ?", llm, catalog, tracer=Tracer(path)))
    events = load_trace(path)
    assert events[0]["gen_ai.request.model"] == "scripted"
    tool = next(e for e in events if e["type"] == "tool_call")
    assert tool["gen_ai.tool.name"] == "search_datasets"
    assert tool["result_chars"] > 0 and tool["duration_ms"] >= 0
    text = render_trace(events)
    assert "QUESTION  vélo ?" in text
    assert "STOP      answered" in text
    assert "search_datasets" in text and "TOTALS    3 LLM call(s), 1 tool call(s)" in text


def test_cache_replays_a_response_without_calling_the_model() -> None:
    inner = ScriptedLLM(say("first"))
    cached = CachedLLM(inner, ":memory:")
    messages = [Message("user", text="hello")]
    first = cached.complete("sys", messages, [])
    second = cached.complete("sys", messages, [])
    assert (first.cached, second.cached) == (False, True)
    assert second.text == "first"
    assert (cached.hits, cached.misses, len(inner.calls)) == (1, 1, 1)


def test_cache_key_changes_with_anything_that_changes_the_answer() -> None:
    base = request_key("m", "sys", [Message("user", text="a")], [])
    assert base == request_key("m", "sys", [Message("user", text="a", raw=object())], [])
    assert base != request_key("other", "sys", [Message("user", text="a")], [])
    assert base != request_key("m", "sys2", [Message("user", text="a")], [])
    assert base != request_key("m", "sys", [Message("user", text="b")], [])
    assert base != request_key("m", "sys", [Message("user", text="a")], [SUBMIT_ANSWER])


def test_throttle_waits_between_calls_and_reports_the_waiting_time() -> None:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    throttled = ThrottledLLM(
        ScriptedLLM(say("a"), say("b")), RateLimiter(4.5, clock=lambda: now[0], sleep=sleep)
    )
    throttled.complete("s", [], [])
    throttled.complete("s", [], [])
    assert throttled.waited_s == 4.5
    assert throttled.model == "scripted"


def test_last_step_offers_only_the_final_answer_tool(catalog: Catalog) -> None:
    llm = ScriptedLLM(
        say(PLAN), say("", search("a")), say("", search("b")), say("", submit("abstained"))
    )
    result, _ = run(catalog, llm, limits=Limits(max_steps=4))
    assert result.stop_reason == "abstained"
    system, _, tools = llm.calls[3]
    assert [t.name for t in tools] == [SUBMIT_ANSWER.name]
    assert "LAST STEP" in system
    assert "LAST STEP" not in llm.calls[2][0]
    assert llm.forced == [None, None, None, SUBMIT_ANSWER.name]  # the final call is forced
    assert "Model calls left after this one: 2" in llm.calls[1][0]
    assert "Model calls left after this one: 1" in llm.calls[2][0]


def test_working_time_ignores_waits_from_earlier_runs(catalog: Catalog) -> None:
    class Waited(ScriptedLLM):
        waited_s = 500.0  # accumulated while answering previous questions

    llm = Waited(say(PLAN), say("", submit("abstained")))
    result, _ = run(catalog, llm)
    assert 0 <= result.seconds < 60


def test_retry_pauses_of_the_provider_count_as_waiting_not_working() -> None:
    inner = ScriptedLLM(say("a"))
    inner.retry_waited_s = 25.0  # type: ignore[attr-defined]
    throttled = ThrottledLLM(inner, RateLimiter(0.0))
    throttled.complete("s", [], [])
    assert throttled.waited_s == 25.0
