"""One JSON line per event, one file per run.

Event types: run_start, plan, llm_call, tool_call, final. Model and token
fields follow the OpenTelemetry GenAI naming (`gen_ai.request.model`,
`gen_ai.usage.input_tokens`...) so that the files can be exported later.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class Tracer:
    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.events: list[dict[str, Any]] = []
        self._clock = clock
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        event = {"type": event_type, "ts": round(self._clock(), 3), **fields}
        self.events.append(event)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return event


def load_trace(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _short(value: Any, limit: int = 110) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_trace(events: list[dict[str, Any]]) -> str:
    """Plain-text view of a trace: what the agent did, in order, with its costs."""
    lines: list[str] = []
    for event in events:
        kind = event["type"]
        if kind == "run_start":
            lines.append(f"QUESTION  {event['question']}")
            lines.append(f"MODEL     {event['gen_ai.request.model']}   limits: {event['limits']}")
        elif kind == "plan":
            lines.append(f"PLAN      {_short(event['plan'], 300)}")
        elif kind == "llm_call":
            cached = " (cached)" if event.get("cached") else ""
            lines.append(
                f"LLM  #{event['step']}   {event['duration_ms']:.0f} ms{cached}   "
                f"{event['gen_ai.usage.input_tokens']} in / "
                f"{event['gen_ai.usage.output_tokens']} out   "
                f"-> {event['tool_calls']} tool call(s)"
            )
            if event.get("text"):
                lines.append(f"          says: {_short(event['text'])}")
        elif kind == "tool_call":
            status = "ERROR" if event["is_error"] else "ok"
            lines.append(
                f"  TOOL    {event['gen_ai.tool.name']}({_short(event['arguments'], 90)})   "
                f"{event['duration_ms']:.0f} ms   {event['result_chars']} chars   {status}"
            )
            if event.get("summary"):
                lines.append(f"          {_short(event['summary'], 150)}")
        elif kind == "final":
            lines.append(f"STOP      {event['stop_reason']}")
            answer = event.get("answer")
            if answer:
                lines.append(f"ANSWER    [{answer['status']}] {_short(answer['message'], 400)}")
                for dataset in answer.get("datasets", []):
                    lines.append(f"  - {dataset['uuid']}: {_short(dataset['reason'], 140)}")
                    lines.append(f"      checks: {dataset['checks']}")
            if event.get("invented_citations"):
                lines.append(f"REMOVED   invented citations: {event['invented_citations']}")
            cost = event.get("cost_usd")
            lines.append(
                f"TOTALS    {event['steps']} LLM call(s), {event['tool_calls']} tool call(s), "
                f"{event['gen_ai.usage.input_tokens']} in / "
                f"{event['gen_ai.usage.output_tokens']} out tokens, "
                f"{'cost unknown' if cost is None else f'{cost:.5f} USD at list price'}, "
                f"{event['seconds']:.1f} s"
            )
    return "\n".join(lines)
