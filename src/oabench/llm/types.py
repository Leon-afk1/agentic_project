"""The small vocabulary shared by the agent loop and every model provider.

The agent only ever sees these types. A provider module translates them to and
from its own SDK, so that changing provider touches one file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass
class Message:
    """One turn of the conversation.

    - user: `text`;
    - assistant: `text` and/or `tool_calls`, plus `raw`, the provider's own
      object, replayed as-is on the next call (some providers attach opaque
      reasoning signatures that must be sent back unchanged);
    - tool: the `result` of the call identified by `tool_name` / `tool_call_id`.
    """

    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_name: str = ""
    tool_call_id: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    raw: Any = None

    def as_message(self) -> Message:
        return Message("assistant", text=self.text, tool_calls=self.tool_calls, raw=self.raw)


class LLMError(RuntimeError):
    """The provider refused or failed the request."""


class LLMClient(Protocol):
    model: str

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> LLMResponse: ...
