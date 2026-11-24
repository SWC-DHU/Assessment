"""Role-enforced agent configuration resolution and secret-safe config snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.db_models import AgentExecutionSettings, AgentProfile
from app.models.schemas import AgentExecutionConfig
from app.services.llm_extractor import ExtractorConfig, is_official_deepseek, normalize_base_url
from app.services.redaction import safe_endpoint

from .runtime import AgentType, FallbackPolicy


class AgentNotFoundError(LookupError):
    """Raised when a requested agent_id or default role profile cannot be found."""


class RoleMismatchError(ValueError):
    """Raised when an agent profile's agent_type does not match the requested execution role."""

    def __init__(self, expected: AgentType, actual: str, agent_id: str):
        self.expected = expected
        self.actual = actual
        self.agent_id = agent_id
        super().__init__(
            f"智能体角色不匹配：任务要求 {expected}，但所选配置 {agent_id} 的角色为 {actual}，禁止跨角色混用。"
        )


@dataclass
class ResolvedAgentRuntime:
    agent_id: str | None
    agent_name: str
    agent_type: AgentType
    llm_config: ExtractorConfig
    execution: AgentExecutionConfig
    has_custom_api_key: bool
    using_env_api_key: bool
    fallback_policy: FallbackPolicy = "degrade_deterministic"

    def non_sensitive_snapshot(self, *, prompt_version: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a strictly secret-free configuration snapshot suitable for persistence and logs."""
        snapshot: dict[str, Any] = {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "agent_type": self.agent_type,
            "effective_base_url": safe_endpoint(self.llm_config.base_url),
            "effective_model": self.llm_config.model,
            "has_api_key": bool(self.llm_config.api_key.strip()),
            "has_custom_api_key": self.has_custom_api_key,
            "using_env_api_key": self.using_env_api_key,
            "temperature": self.llm_config.temperature,
            "max_tokens": self.llm_config.max_tokens,
            "json_mode": self.llm_config.json_mode,
            "timeout_seconds": self.llm_config.timeout_seconds,
            "request_budget": self.llm_config.request_budget,
            "fallback_policy": self.fallback_policy,
            "prompt_version": prompt_version,
        }
        if extra:
            snapshot.update(extra)
        return snapshot


def load_execution_settings(db: Session, agent_id: str) -> AgentExecutionConfig:
    row = db.get(AgentExecutionSettings, agent_id)
    return AgentExecutionConfig.model_validate(row.config) if row else AgentExecutionConfig()


def resolve_agent_profile(
    db: Session,
    expected_type: AgentType,
    agent_id: str | None = None,
) -> AgentProfile:
    """Strictly resolve an AgentProfile for expected_type; never cross-match roles."""
    if agent_id:
        agent = db.get(AgentProfile, agent_id)
        if agent is None:
            raise AgentNotFoundError(f"智能体配置不存在：{agent_id}")
        actual = getattr(agent, "agent_type", "requirement_parsing") or "requirement_parsing"
        if actual != expected_type:
            raise RoleMismatchError(expected_type, actual, agent_id)
        return agent

    agent = (
        db.query(AgentProfile)
        .filter(AgentProfile.agent_type == expected_type)
        .order_by(AgentProfile.created_at.asc())
        .first()
    )
    if agent is None:
        raise AgentNotFoundError(f"未找到角色为 {expected_type} 的默认智能体配置，请先创建对应角色的智能体配置。")
    return agent


def build_agent_runtime(
    db: Session,
    agent: AgentProfile,
    *,
    expected_type: AgentType,
    default_budget: int = 2,
    default_timeout: int | None = None,
    semantic_strategy: bool = True,
) -> ResolvedAgentRuntime:
    actual = getattr(agent, "agent_type", "requirement_parsing") or "requirement_parsing"
    if actual != expected_type:
        raise RoleMismatchError(expected_type, actual, agent.id)

    execution = load_execution_settings(db, agent.id)
    base_url = normalize_base_url(agent.base_url) or settings.default_llm_base_url
    has_custom = bool(agent.api_key.strip())
    using_env = not has_custom and bool(settings.default_llm_api_key.strip())
    api_key = agent.api_key.strip() or settings.default_llm_api_key
    model = agent.model.strip() or settings.default_llm_model

    effective_budget = execution.request_budget
    if effective_budget is None:
        if expected_type == "requirement_parsing" and semantic_strategy and is_official_deepseek(base_url):
            effective_budget = 4
        else:
            effective_budget = default_budget

    llm_cfg = ExtractorConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
        json_mode=agent.json_mode,
        require_source_quote=agent.require_source_quote,
        system_prompt=agent.system_prompt,
        business_rules=agent.business_rules,
        extraction_schema=agent.extraction_schema or {},
        timeout_seconds=execution.timeout_seconds or default_timeout or settings.llm_timeout_seconds,
        request_budget=effective_budget,
        semantic_batch_max_tokens=execution.batch_max_tokens,
        semantic_thinking=execution.semantic_thinking,
        thinking_parameter=execution.thinking_parameter,
        llm_disambiguation=execution.llm_disambiguation,
    )
    return ResolvedAgentRuntime(
        agent_id=agent.id,
        agent_name=agent.name,
        agent_type=expected_type,
        llm_config=llm_cfg,
        execution=execution,
        has_custom_api_key=has_custom,
        using_env_api_key=using_env,
        fallback_policy=execution.fallback_policy,
    )


def resolve_agent_runtime(
    db: Session,
    expected_type: AgentType,
    agent_id: str | None = None,
    *,
    default_budget: int = 2,
    default_timeout: int | None = None,
    semantic_strategy: bool = True,
) -> tuple[AgentProfile, ResolvedAgentRuntime]:
    agent = resolve_agent_profile(db, expected_type, agent_id)
    runtime = build_agent_runtime(
        db,
        agent,
        expected_type=expected_type,
        default_budget=default_budget,
        default_timeout=default_timeout,
        semantic_strategy=semantic_strategy,
    )
    return agent, runtime
