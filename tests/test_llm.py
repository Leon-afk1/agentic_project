from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from oabench.llm.check import ECHO, run_check
from oabench.llm.config import MissingKeyError, describe_key, load_env, require_key
from oabench.llm.gemini import cost_usd
from oabench.llm.types import LLMResponse, Message, ToolCall, ToolSpec

FAKE_KEY = "test-key-not-a-real-secret"


class ScriptedLLM:
    """Plays back prepared responses and records what it was sent."""

    model = "scripted"

    def __init__(self, *responses: LLMResponse) -> None:
        self._responses = list(responses)
        self.seen: list[list[Message]] = []

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        force_tool: str | None = None,
    ) -> LLMResponse:
        self.seen.append(list(messages))
        return self._responses.pop(0)


def reply(text: str = "", *calls: ToolCall) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=list(calls), input_tokens=10, output_tokens=5)


def test_load_env_reads_pairs_and_never_overrides(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.delenv("OABENCH_T_A", raising=False)
    monkeypatch.delenv("OABENCH_T_B", raising=False)
    monkeypatch.setenv("OABENCH_T_C", "from-shell")
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nOABENCH_T_A=plain\nOABENCH_T_B = 'quoted value'\nOABENCH_T_C=from-file\n"
        "OABENCH_T_EMPTY=\nnot a pair\n",
        encoding="utf-8",
    )
    assert load_env(env) == ["OABENCH_T_A", "OABENCH_T_B"]
    import os

    assert os.environ["OABENCH_T_A"] == "plain"
    assert os.environ["OABENCH_T_B"] == "quoted value"
    assert os.environ["OABENCH_T_C"] == "from-shell"
    assert load_env(tmp_path / "missing.env") == []


def test_missing_key_gives_an_actionable_error(monkeypatch: Any) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(MissingKeyError, match=r"\.env"):
        require_key()
    assert describe_key() == "GEMINI_API_KEY: not set"


def test_key_description_never_contains_the_key(monkeypatch: Any) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    assert require_key() == FAKE_KEY
    assert FAKE_KEY not in describe_key()
    assert str(len(FAKE_KEY)) in describe_key()


def test_check_passes_when_the_model_calls_the_tool_then_answers() -> None:
    llm = ScriptedLLM(reply("", ToolCall("echo_word", {"word": "bonjour"}, "c1")), reply("done"))
    result = run_check(llm)
    assert result.ok
    assert (result.input_tokens, result.output_tokens) == (20, 10)
    roles = [m.role for m in llm.seen[1]]
    assert roles == ["user", "assistant", "tool"]
    assert llm.seen[1][2].result == {"word": "bonjour"}


def test_check_fails_when_the_model_answers_without_calling_the_tool() -> None:
    result = run_check(ScriptedLLM(reply("I cannot call tools.")))
    assert not result.ok and not result.tool_called


def test_check_fails_on_wrong_arguments() -> None:
    llm = ScriptedLLM(reply("", ToolCall("echo_word", {"word": "hello"})), reply("done"))
    result = run_check(llm)
    assert result.tool_called and not result.arguments_ok and not result.ok


def test_cost_uses_list_prices_and_admits_unknown_models() -> None:
    assert cost_usd("gemini-3.5-flash-lite", 1_000_000, 1_000_000) == pytest.approx(2.80)
    assert cost_usd("some-future-model", 10, 10) is None


def test_gemini_message_translation_groups_tool_results() -> None:
    pytest.importorskip("google.genai")
    from oabench.llm.gemini import to_contents

    messages = [
        Message("user", text="question"),
        Message("assistant", tool_calls=[ToolCall("a", {"x": 1}), ToolCall("b", {})]),
        Message("tool", tool_name="a", result={"ok": True}),
        Message("tool", tool_name="b", result={"ok": False}),
        Message("assistant", text="answer"),
    ]
    contents = to_contents(messages)
    assert [c.role for c in contents] == ["user", "model", "user", "model"]
    assert [p.function_call.name for p in contents[1].parts] == ["a", "b"]
    assert [p.function_response.name for p in contents[2].parts] == ["a", "b"]
    assert contents[3].parts[0].text == "answer"


def test_gemini_replays_the_raw_model_turn_unchanged() -> None:
    pytest.importorskip("google.genai")
    from google.genai import types

    from oabench.llm.gemini import to_contents

    raw = types.Content(role="model", parts=[types.Part(text="kept as is")])
    assert to_contents([Message("assistant", text="ignored", raw=raw)])[0] is raw


def test_gemini_response_parsing() -> None:
    pytest.importorskip("google.genai")
    from google.genai import types

    from oabench.llm.gemini import from_response

    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="Looking."),
                        types.Part(
                            function_call=types.FunctionCall(name=ECHO.name, args={"word": "x"})
                        ),
                    ],
                )
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12, candidates_token_count=7, thoughts_token_count=3
        ),
    )
    parsed = from_response(response)
    assert parsed.text == "Looking."
    assert parsed.tool_calls == [ToolCall(ECHO.name, {"word": "x"}, "")]
    assert (parsed.input_tokens, parsed.output_tokens) == (12, 10)


SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google API key
    re.compile(r"sk-[A-Za-z0-9_\-]{32,}"),  # common provider key shape
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def test_no_secret_is_tracked_by_git() -> None:
    root = Path(__file__).resolve().parents[1]
    listing = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=False
    )
    if listing.returncode != 0:
        pytest.skip("not a git checkout")
    tracked = listing.stdout.split()
    assert ".env" not in tracked
    for name in tracked:
        path = root / name
        if not path.is_file() or path.stat().st_size > 2_000_000 or name == "uv.lock":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(text), f"possible secret in {name}"


def test_transport_errors_are_told_apart_from_bugs() -> None:
    import httpx

    from oabench.llm.gemini import is_transport_error

    assert is_transport_error(httpx.RemoteProtocolError("Server disconnected"))
    assert is_transport_error(ConnectionResetError())
    assert not is_transport_error(KeyError("bug"))
    assert not is_transport_error(ValueError("bug"))


def test_gemini_client_retries_a_dropped_connection_then_gives_up_cleanly() -> None:
    pytest.importorskip("google.genai")
    import httpx

    from oabench.llm.gemini import RETRY_DELAYS_S, GeminiClient
    from oabench.llm.types import LLMError

    client = GeminiClient(FAKE_KEY)
    sleeps: list[float] = []
    client._sleep = sleeps.append
    attempts = 0

    def always_dropped(**_kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    client._client.models.generate_content = always_dropped
    with pytest.raises(LLMError, match="RemoteProtocolError"):
        client.complete("system", [Message("user", text="hi")], [])
    assert attempts == len(RETRY_DELAYS_S) + 1
    assert sleeps == list(RETRY_DELAYS_S)


def test_gemini_client_does_not_hide_a_programming_error() -> None:
    pytest.importorskip("google.genai")
    from oabench.llm.gemini import GeminiClient

    client = GeminiClient(FAKE_KEY)

    def broken(**_kwargs: Any) -> Any:
        raise KeyError("a bug, not a network problem")

    client._client.models.generate_content = broken
    with pytest.raises(KeyError):
        client.complete("system", [Message("user", text="hi")], [])
