"""Shared multi-agent runtime contracts, run metadata, and call budget control."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgentType = Literal["requirement_parsing", "resource_matching"]
FallbackPolicy = Literal["degrade_deterministic", "fail_fast"]


class AgentRunMetadata(BaseModel):
    """Standardized, secret-free audit metadata emitted by every agent execution."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    agent_type: AgentType
    agent_id: str | None = None
    agent_name: str | None = None
    model_requested: str | None = None
    model_used: str | None = None
    prompt_version: str
    input_version: str | None = None
    execution_mode: str = "deterministic"
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    tool_calls_count: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    llm_retries: int = Field(default=0, ge=0)
    fallback_triggered: bool = False
    fallback_reason: str | None = None
    elapsed_ms: float = Field(default=0.0, ge=0.0)


class RunBudget:
    """Finite call-budget and retry limiter to prevent unbounded LLM loops."""

    def __init__(self, max_calls: int = 3, max_retries: int = 1):
        self.max_calls = max(0, int(max_calls))
        self.max_retries = max(0, int(max_retries))
        self.calls_used = 0
        self.retries_used = 0

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def can_call(self) -> bool:
        return self.calls_used < self.max_calls

    def consume_call(self, count: int = 1) -> None:
        self.calls_used += max(0, int(count))

    def can_retry(self) -> bool:
        return self.retries_used < self.max_retries and self.can_call()

    def consume_retry(self) -> None:
        self.retries_used += 1
