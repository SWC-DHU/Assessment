"""Shared multi-agent infrastructure: role registry, role-enforced config, unified LLM client, and runtime metadata."""
from .config import (
    AgentNotFoundError,
    ResolvedAgentRuntime,
    RoleMismatchError,
    build_agent_runtime,
    load_execution_settings,
    resolve_agent_profile,
    resolve_agent_runtime,
)
from .llm_client import LLMCallResult, UnifiedLLMClient
from .registry import (
    AGENT_ROLES,
    AgentRoleSpec,
    DEFAULT_MATCHING_PROMPT,
    DEFAULT_MATCHING_RULES,
    DEFAULT_REQUIREMENT_PROMPT,
    DEFAULT_REQUIREMENT_RULES,
    get_role_spec,
    list_role_specs,
)
from .runtime import AgentRunMetadata, AgentType, FallbackPolicy, RunBudget

__all__ = [
    "AGENT_ROLES",
    "AgentNotFoundError",
    "AgentRoleSpec",
    "AgentRunMetadata",
    "AgentType",
    "DEFAULT_MATCHING_PROMPT",
    "DEFAULT_MATCHING_RULES",
    "DEFAULT_REQUIREMENT_PROMPT",
    "DEFAULT_REQUIREMENT_RULES",
    "FallbackPolicy",
    "LLMCallResult",
    "ResolvedAgentRuntime",
    "RoleMismatchError",
    "RunBudget",
    "UnifiedLLMClient",
    "build_agent_runtime",
    "get_role_spec",
    "list_role_specs",
    "load_execution_settings",
    "resolve_agent_profile",
    "resolve_agent_runtime",
]
