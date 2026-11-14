"""The structured final answer and the tool the model calls to deliver it.

Ending through a tool call (rather than free text) gives an explicit stop
criterion and a machine-checkable answer: the citations can be verified
against what the tools really returned.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from oabench.llm.types import ToolSpec

Verdict = Literal["ok", "ko", "unknown"]
CHECKS = ("territory", "period", "granularity", "format")
MAX_DATASETS = 5


class Checks(BaseModel):
    territory: Verdict = "unknown"
    period: Verdict = "unknown"
    granularity: Verdict = "unknown"
    format: Verdict = "unknown"


class Citation(BaseModel):
    uuid: str
    title: str = ""
    reason: str = ""
    checks: Checks = Field(default_factory=Checks)
    evidence: str = ""


class AgentAnswer(BaseModel):
    status: Literal["answered", "abstained"]
    message: str
    datasets: list[Citation] = Field(default_factory=list, max_length=MAX_DATASETS)
    suggestions: list[str] = Field(default_factory=list)


_VERDICT = {"type": "string", "enum": ["ok", "ko", "unknown"]}

SUBMIT_ANSWER = ToolSpec(
    name="submit_answer",
    description=(
        "Deliver the final answer and end the task. Call it exactly once, when you have "
        "inspected the candidates or concluded that the catalog has nothing reliable."
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["answered", "abstained"],
                "description": "'abstained' when no dataset reliably fits the request",
            },
            "message": {
                "type": "string",
                "description": "Answer for the user, in French, 2 to 6 sentences",
            },
            "datasets": {
                "type": "array",
                "maxItems": MAX_DATASETS,
                "description": "Recommended datasets, best first. Empty when abstaining.",
                "items": {
                    "type": "object",
                    "properties": {
                        "uuid": {"type": "string", "description": "Exactly as returned by a tool"},
                        "title": {"type": "string"},
                        "reason": {"type": "string", "description": "Why it fits, in French"},
                        "checks": {
                            "type": "object",
                            "description": "Does the record cover the requested...",
                            "properties": {name: _VERDICT for name in CHECKS},
                        },
                        "evidence": {
                            "type": "string",
                            "description": "Short phrase from the record supporting the checks",
                        },
                    },
                    "required": ["uuid", "reason"],
                },
            },
            "suggestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "When abstaining: likely producer or other portal to try",
            },
        },
        "required": ["status", "message"],
    },
)
