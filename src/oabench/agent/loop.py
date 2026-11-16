"""The agent loop: plan, search, inspect, verify, answer, under hard limits.

Written by hand on purpose. The whole control flow is in `run_agent`: every
model call and every tool call goes through it, is counted against a limit and
is written to the trace. The run always ends with exactly one stop reason.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from oabench.agent.answer import SUBMIT_ANSWER, AgentAnswer
from oabench.agent.prompts import AGENT_SYSTEM, FINAL_STEP, NUDGE, PLAN_SYSTEM
from oabench.llm.types import LLMClient, LLMError, LLMResponse, Message, ToolCall, ToolSpec
from oabench.tracing.tracer import Tracer

NL = "\n"

StopReason = Literal[
    "answered",
    "abstained",
    "max_steps",
    "max_tool_calls",
    "token_budget",
    "timeout",
    "loop_detected",
    "tool_error",
    "llm_error",
    "no_final_answer",
]


@dataclass(frozen=True)
class Limits:
    max_steps: int = 8  # model calls, the plan included
    max_tool_calls: int = 12
    max_input_tokens: int = 30_000  # cumulated over the run
    timeout_s: float = 90.0  # working time; waiting for the rate limit is not counted
    max_identical_calls: int = 2  # the same tool call a third time stops the run


@dataclass
class ToolOutcome:
    payload: dict[str, Any]
    is_error: bool = False


# How the loop reaches the tools. In production this is an MCP client session;
# the indirection keeps the loop independent from the transport.
ToolRunner = Callable[[str, dict[str, Any]], Awaitable[ToolOutcome]]


@dataclass
class AgentResult:
    question: str
    stop_reason: StopReason
    answer: AgentAnswer | None = None
    plan: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    seconds: float = 0.0
    invented_citations: list[str] = field(default_factory=list)

    @property
    def ranking(self) -> list[str]:
        """Recommended uuids, best first (empty when the agent abstained or was stopped)."""
        return [c.uuid for c in self.answer.datasets] if self.answer else []


def parse_plan(text: str) -> dict[str, Any]:
    """Read the JSON plan, tolerating a Markdown code fence around it."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            plan = json.loads(cleaned[start : end + 1])
            if isinstance(plan, dict):
                return plan
        except json.JSONDecodeError:
            pass
    return {"unparsed": text[:500]}


def collect_uuids(payload: Any) -> set[str]:
    """Every value stored under a "uuid" key, at any depth of a tool result."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "uuid" and isinstance(value, str):
                found.add(value)
            else:
                found |= collect_uuids(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= collect_uuids(item)
    return found


def summarize(name: str, payload: dict[str, Any]) -> str:
    """One line for the trace: what a tool result contained."""
    if "error" in payload:
        return f"error: {str(payload['error'])[:120]}"
    if name == "search_datasets":
        titles = [r.get("title", "")[:40] for r in payload.get("results", [])[:3]]
        return f"{payload.get('total_matches', '?')} matches; top: {titles}"
    if name == "get_dataset_metadata":
        return f"{payload.get('title', '')[:80]}"
    return ""


async def run_agent(
    question: str,
    llm: LLMClient,
    tools: list[ToolSpec],
    run_tool: ToolRunner,
    *,
    tracer: Tracer | None = None,
    limits: Limits | None = None,
    clock: Callable[[], float] = time.perf_counter,
    price: Callable[[int, int], float | None] | None = None,
    system_prompt: str = AGENT_SYSTEM,
) -> AgentResult:
    limits = limits or Limits()
    tracer = tracer or Tracer()
    result = AgentResult(question=question, stop_reason="no_final_answer")
    started = clock()
    seen_uuids: set[str] = set()
    call_counts: Counter[str] = Counter()

    # The client's waiting time is cumulated over its whole life: only what was
    # waited during this run may be subtracted.
    waited_before_run = float(getattr(llm, "waited_s", 0.0))

    def working_time() -> float:
        waited = float(getattr(llm, "waited_s", 0.0)) - waited_before_run
        return clock() - started - waited

    def limit_reached() -> StopReason | None:
        if result.steps >= limits.max_steps:
            return "max_steps"
        if result.input_tokens >= limits.max_input_tokens:
            return "token_budget"
        if working_time() > limits.timeout_s:
            return "timeout"
        return None

    def call_model(
        system: str, messages: list[Message], specs: list[ToolSpec], force: str | None = None
    ) -> LLMResponse:
        before, waited_before = clock(), float(getattr(llm, "waited_s", 0.0))
        if force is None:
            response = llm.complete(system, messages, specs)
        else:
            response = llm.complete(system, messages, specs, force_tool=force)
        # Time spent waiting for the rate limit is not the model's latency.
        waited = float(getattr(llm, "waited_s", 0.0)) - waited_before
        duration_ms = round((clock() - before - waited) * 1000, 1)
        result.steps += 1
        result.input_tokens += response.input_tokens
        result.output_tokens += response.output_tokens
        tracer.emit(
            "llm_call",
            step=result.steps,
            duration_ms=duration_ms,
            cached=response.cached,
            text=response.text[:500],
            tool_calls=len(response.tool_calls),
            **{
                "gen_ai.request.model": llm.model,
                "gen_ai.usage.input_tokens": response.input_tokens,
                "gen_ai.usage.output_tokens": response.output_tokens,
            },
        )
        return response

    def finish(reason: StopReason) -> AgentResult:
        result.stop_reason = reason
        result.seconds = working_time()
        result.cost_usd = price(result.input_tokens, result.output_tokens) if price else None
        tracer.emit(
            "final",
            stop_reason=reason,
            answer=result.answer.model_dump() if result.answer else None,
            invented_citations=result.invented_citations,
            steps=result.steps,
            tool_calls=result.tool_calls,
            cost_usd=result.cost_usd,
            seconds=round(result.seconds, 2),
            **{
                "gen_ai.usage.input_tokens": result.input_tokens,
                "gen_ai.usage.output_tokens": result.output_tokens,
            },
        )
        return result

    tracer.emit(
        "run_start",
        question=question,
        limits=vars(limits),
        tools=[tool.name for tool in tools],
        **{"gen_ai.request.model": llm.model},
    )

    try:
        # 1. Plan: one call without tools.
        planning = call_model(PLAN_SYSTEM, [Message("user", text=question)], [])
        result.plan = parse_plan(planning.text)
        tracer.emit("plan", plan=result.plan)

        plan_text = json.dumps(result.plan, ensure_ascii=False)
        messages = [Message("user", text=f"Request: {question}\n\nYour search plan: {plan_text}")]
        all_tools = [*tools, SUBMIT_ANSWER]
        nudged = False

        # 2. Search / inspect / verify, until submit_answer or a limit.
        while True:
            reason = limit_reached()
            if reason:
                return finish(reason)
            # The last allowed model call can only deliver the answer: a run should
            # end with a conclusion (possibly an abstention), not in mid-search.
            last_step = result.steps == limits.max_steps - 1
            if last_step:
                response = call_model(
                    system_prompt + FINAL_STEP, messages, [SUBMIT_ANSWER], SUBMIT_ANSWER.name
                )
            else:
                left = limits.max_steps - result.steps - 1
                pacing = (
                    f"{NL}Model calls left after this one: {left}. The last one must be the answer."
                )
                response = call_model(system_prompt + pacing, messages, all_tools)
            messages.append(response.as_message())
            submitted = any(c.name == SUBMIT_ANSWER.name for c in response.tool_calls)
            if last_step and not submitted:
                return finish("max_steps")

            if not response.tool_calls:
                if nudged:
                    return finish("no_final_answer")
                nudged = True
                messages.append(Message("user", text=NUDGE))
                continue

            submit = next((c for c in response.tool_calls if c.name == SUBMIT_ANSWER.name), None)
            if submit is not None:
                try:
                    answer = AgentAnswer.model_validate(submit.arguments)
                except ValidationError as exc:
                    # Malformed final answer: tell the model, let it retry within its limits.
                    messages += _answer_all(
                        response.tool_calls, {"error": f"invalid answer: {exc.errors()[:3]}"}
                    )
                    continue
                # 3. Deterministic check: a cited uuid must come from a tool result.
                kept = [c for c in answer.datasets if c.uuid in seen_uuids]
                result.invented_citations = [
                    c.uuid for c in answer.datasets if c.uuid not in seen_uuids
                ]
                answer.datasets = kept
                if answer.status == "answered" and not kept:
                    answer.status = "abstained"  # nothing verifiable is left to recommend
                result.answer = answer
                return finish(answer.status)

            for call in response.tool_calls:
                if result.tool_calls >= limits.max_tool_calls:
                    return finish("max_tool_calls")
                signature = call.name + json.dumps(call.arguments, sort_keys=True)
                call_counts[signature] += 1
                if call_counts[signature] > limits.max_identical_calls:
                    return finish("loop_detected")
                before = clock()
                try:
                    outcome = await run_tool(call.name, call.arguments)
                except Exception as exc:
                    tracer.emit(
                        "tool_call",
                        arguments=call.arguments,
                        duration_ms=round((clock() - before) * 1000, 1),
                        result_chars=0,
                        is_error=True,
                        summary=f"{type(exc).__name__}: {exc}"[:200],
                        **{"gen_ai.tool.name": call.name},
                    )
                    return finish("tool_error")
                result.tool_calls += 1
                seen_uuids |= collect_uuids(outcome.payload)
                tracer.emit(
                    "tool_call",
                    arguments=call.arguments,
                    duration_ms=round((clock() - before) * 1000, 1),
                    result_chars=len(json.dumps(outcome.payload, ensure_ascii=False)),
                    is_error=outcome.is_error,
                    summary=summarize(call.name, outcome.payload),
                    **{"gen_ai.tool.name": call.name},
                )
                messages.append(
                    Message(
                        "tool", tool_name=call.name, tool_call_id=call.id, result=outcome.payload
                    )
                )
    except LLMError as exc:
        tracer.emit("error", message=str(exc)[:300])
        return finish("llm_error")


def _answer_all(calls: list[ToolCall], payload: dict[str, Any]) -> list[Message]:
    """Every call of a model turn must receive a result before the next turn."""
    return [Message("tool", tool_name=c.name, tool_call_id=c.id, result=payload) for c in calls]
