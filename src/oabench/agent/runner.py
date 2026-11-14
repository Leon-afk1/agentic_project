"""Wires the loop to its real dependencies: the MCP server and the model provider.

The agent reaches the catalog through an MCP client session, the same path as
Claude Desktop: what is evaluated is what is demonstrated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp import Client

from oabench.agent.loop import AgentResult, Limits, ToolOutcome, run_agent
from oabench.catalog import Catalog
from oabench.llm.types import LLMClient, ToolSpec
from oabench.mcp_server.server import build_server
from oabench.portal.http import RateLimiter
from oabench.tracing.tracer import Tracer

REQUESTS_PER_MINUTE = 15  # free-tier quota read by the project owner on 15 Nov 2026
SAFETY_MARGIN_S = 0.5


def strip_titles(schema: Any) -> Any:
    """Drop the auto-generated "title" entries of a JSON schema: tokens without information."""
    if isinstance(schema, dict):
        return {
            key: strip_titles(value)
            for key, value in schema.items()
            if not (key == "title" and isinstance(value, str))
        }
    if isinstance(schema, list):
        return [strip_titles(item) for item in schema]
    return schema


def to_tool_specs(mcp_tools: list[Any]) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=tool.name,
            description=(tool.description or "").strip(),
            parameters=strip_titles(tool.input_schema),
        )
        for tool in mcp_tools
    ]


async def ask(
    question: str,
    llm: LLMClient,
    catalog: Catalog,
    *,
    tracer: Tracer | None = None,
    limits: Limits | None = None,
) -> AgentResult:
    from oabench.llm.gemini import cost_usd

    async with Client(build_server(catalog)) as client:
        specs = to_tool_specs((await client.list_tools()).tools)

        async def run_tool(name: str, arguments: dict[str, Any]) -> ToolOutcome:
            result = await client.call_tool(name, arguments)
            text = "".join(getattr(block, "text", "") for block in result.content)
            if result.is_error:
                # A refused call (bad argument, unknown id) goes back to the model,
                # which can correct itself. It is not a failure of the run.
                return ToolOutcome({"error": text[:500]}, is_error=True)
            payload = result.structured_content
            return ToolOutcome(payload if isinstance(payload, dict) else {"text": text[:4000]})

        return await run_agent(
            question,
            llm,
            specs,
            run_tool,
            tracer=tracer,
            limits=limits,
            price=lambda tokens_in, tokens_out: cost_usd(llm.model, tokens_in, tokens_out),
        )


def build_llm(api_key: str, model: str, *, cache: Path | None) -> LLMClient:
    """Provider client behind the rate limiter and, unless disabled, the cache."""
    from oabench.llm.gemini import GeminiClient
    from oabench.llm.wrappers import CachedLLM, ThrottledLLM

    interval = 60.0 / REQUESTS_PER_MINUTE + SAFETY_MARGIN_S
    throttled = ThrottledLLM(GeminiClient(api_key, model), RateLimiter(interval))
    return CachedLLM(throttled, cache) if cache is not None else throttled
