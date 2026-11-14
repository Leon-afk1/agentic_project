"""A two-request smoke test: does the key work, and does the model call tools?"""

from __future__ import annotations

import time
from dataclasses import dataclass

from oabench.llm.types import LLMClient, Message, ToolSpec

ECHO = ToolSpec(
    name="echo_word",
    description="Returns the word it is given.",
    parameters={
        "type": "object",
        "properties": {"word": {"type": "string", "description": "The word to echo"}},
        "required": ["word"],
    },
)
SYSTEM = (
    "You are part of an automated connectivity test. First call the tool echo_word with the "
    "word 'bonjour'. When you receive its result, answer with the single word: done."
)


@dataclass(frozen=True)
class CheckResult:
    tool_called: bool
    arguments_ok: bool
    final_text: str
    input_tokens: int
    output_tokens: int
    seconds: float

    @property
    def ok(self) -> bool:
        return self.tool_called and self.arguments_ok and bool(self.final_text.strip())


def run_check(client: LLMClient) -> CheckResult:
    start = time.perf_counter()
    messages = [Message("user", text="Start the test.")]
    first = client.complete(SYSTEM, messages, [ECHO])
    calls = [call for call in first.tool_calls if call.name == ECHO.name]
    if not calls:
        return CheckResult(
            False, False, first.text, first.input_tokens, first.output_tokens,
            time.perf_counter() - start,
        )  # fmt: skip
    call = calls[0]
    word = str(call.arguments.get("word", ""))
    messages.append(first.as_message())
    messages.append(
        Message("tool", tool_name=call.name, tool_call_id=call.id, result={"word": word})
    )
    second = client.complete(SYSTEM, messages, [ECHO])
    return CheckResult(
        tool_called=True,
        arguments_ok=word.lower() == "bonjour",
        final_text=second.text,
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        seconds=time.perf_counter() - start,
    )
