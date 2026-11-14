"""Two wrappers that any provider client can be put behind.

- `ThrottledLLM` keeps under the provider's requests-per-minute quota;
- `CachedLLM` stores every response, so that re-running an evaluation costs
  neither quota nor money and gives the same numbers.

Order matters: Cached(Throttled(provider)), so that a cache hit never waits.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from oabench.llm.types import LLMClient, LLMResponse, Message, ToolCall, ToolSpec
from oabench.portal.http import RateLimiter


class ThrottledLLM:
    def __init__(self, inner: LLMClient, limiter: RateLimiter) -> None:
        self.model = inner.model
        self._inner = inner
        self._limiter = limiter
        self.waited_s = 0.0  # time spent waiting for the quota, not working

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        self.waited_s += self._limiter.wait()
        return self._inner.complete(system, messages, tools)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)  # dump_raw / load_raw of the provider


def request_key(model: str, system: str, messages: list[Message], tools: list[ToolSpec]) -> str:
    """Hash of everything that determines the answer (the opaque `raw` is left out)."""
    payload = {
        "model": model,
        "system": system,
        "tools": [asdict(tool) for tool in tools],
        "messages": [
            {
                "role": m.role,
                "text": m.text,
                "calls": [[c.name, c.arguments] for c in m.tool_calls],
                "tool": m.tool_name,
                "result": m.result,
            }
            for m in messages
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CachedLLM:
    def __init__(self, inner: LLMClient, path: Path | str) -> None:
        self.model = inner.model
        self._inner = inner
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS llm_responses (key TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )
        self.hits = 0
        self.misses = 0

    @property
    def waited_s(self) -> float:
        return float(getattr(self._inner, "waited_s", 0.0))

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        key = request_key(self.model, system, messages, tools)
        row = self._db.execute("SELECT body FROM llm_responses WHERE key = ?", (key,)).fetchone()
        if row is not None:
            self.hits += 1
            return self._decode(json.loads(row[0]))
        self.misses += 1
        response = self._inner.complete(system, messages, tools)
        self._db.execute(
            "INSERT OR REPLACE INTO llm_responses (key, body) VALUES (?, ?)",
            (key, json.dumps(self._encode(response), ensure_ascii=False)),
        )
        self._db.commit()
        return response

    def _encode(self, response: LLMResponse) -> dict[str, Any]:
        dump = getattr(self._inner, "dump_raw", None)
        return {
            "text": response.text,
            "tool_calls": [asdict(call) for call in response.tool_calls],
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "raw": dump(response.raw) if dump and response.raw is not None else None,
        }

    def _decode(self, body: dict[str, Any]) -> LLMResponse:
        load = getattr(self._inner, "load_raw", None)
        return LLMResponse(
            text=body["text"],
            tool_calls=[ToolCall(**call) for call in body["tool_calls"]],
            input_tokens=body["input_tokens"],
            output_tokens=body["output_tokens"],
            raw=load(body["raw"]) if load and body["raw"] is not None else None,
            cached=True,
        )

    def close(self) -> None:
        self._db.close()
