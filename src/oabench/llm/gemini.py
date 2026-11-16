"""Google Gemini provider, through the official `google-genai` SDK (optional extra).

Stateless use: the whole conversation is sent at every call, and the model's
own turns are replayed exactly as received.
"""

from __future__ import annotations

import time
from typing import Any

from oabench.llm.types import LLMError, LLMResponse, Message, ToolCall, ToolSpec

DEFAULT_MODEL = "gemini-3.5-flash-lite"
RETRY_DELAYS_S = (5.0, 20.0, 60.0)  # on 5xx answers only

# Public list prices in USD per million tokens (input, output), read on
# 15 Nov 2026. Used to report a theoretical cost even on the free tier.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
}


def to_contents(messages: list[Message]) -> list[Any]:
    """Translate our messages into SDK `Content` objects."""
    from google.genai import types

    contents: list[Any] = []
    for message in messages:
        if message.role == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=message.text)]))
        elif message.role == "assistant":
            if message.raw is not None:
                contents.append(message.raw)  # keeps thought signatures intact
                continue
            parts = [types.Part(text=message.text)] if message.text else []
            parts += [
                types.Part(function_call=types.FunctionCall(name=c.name, args=c.arguments))
                for c in message.tool_calls
            ]
            contents.append(types.Content(role="model", parts=parts))
        else:
            part = types.Part.from_function_response(
                name=message.tool_name, response=message.result
            )
            # Results of calls issued in the same model turn travel together.
            previous = contents[-1] if contents else None
            if previous is not None and previous.role == "user" and _is_tool_turn(previous):
                previous.parts.append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
    return contents


def _is_tool_turn(content: Any) -> bool:
    return bool(content.parts) and all(p.function_response is not None for p in content.parts)


def from_response(response: Any) -> LLMResponse:
    calls = [
        ToolCall(name=call.name or "", arguments=dict(call.args or {}), id=call.id or "")
        for call in (response.function_calls or [])
    ]
    usage = response.usage_metadata
    candidates = response.candidates or []
    text = ""
    if candidates and candidates[0].content and candidates[0].content.parts:
        text = "".join(
            part.text
            for part in candidates[0].content.parts
            if part.text and not getattr(part, "thought", False)
        )
    return LLMResponse(
        text=text,
        tool_calls=calls,
        input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
        output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0)
        + int(getattr(usage, "thoughts_token_count", 0) or 0),
        raw=candidates[0].content if candidates else None,
    )


def _dump_raw(raw: Any) -> str:
    return str(raw.model_dump_json(exclude_none=True))


def _load_raw(text: str) -> Any:
    from google.genai import types

    return types.Content.model_validate_json(text)


class GeminiClient:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, *, temperature: float = 0.0):
        from google import genai

        self.model = model
        self._temperature = temperature
        self._client = genai.Client(api_key=api_key)
        self._sleep = time.sleep

    # Used by the response cache to store and restore the model's own turn.
    dump_raw = staticmethod(_dump_raw)
    load_raw = staticmethod(_load_raw)

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        force_tool: str | None = None,
    ) -> LLMResponse:
        from google.genai import errors, types

        declarations = [
            types.FunctionDeclaration(
                name=tool.name, description=tool.description, parameters_json_schema=tool.parameters
            )
            for tool in tools
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self._temperature,
            tools=[types.Tool(function_declarations=declarations)] if declarations else None,
            # The agent loop runs the tools itself, so that every call is traced.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY, allowed_function_names=[force_tool]
                )
            )
            if force_tool
            else None,
        )
        contents = to_contents(messages)
        for attempt in range(len(RETRY_DELAYS_S) + 1):
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
                return from_response(response)
            except errors.ServerError as exc:
                # 5xx: the service is overloaded. Worth a few patient retries.
                if attempt == len(RETRY_DELAYS_S):
                    raise LLMError(f"{type(exc).__name__}: {exc}") from exc
                self._sleep(RETRY_DELAYS_S[attempt])
            except errors.APIError as exc:
                # 4xx (quota, bad request): retrying would not help.
                # The SDK message holds the status and reason, never the key.
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc
        raise LLMError("unreachable")


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Theoretical cost at list price; None when the model's price is unknown."""
    price = PRICES_USD_PER_MTOK.get(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
