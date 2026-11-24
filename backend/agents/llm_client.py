"""Unified OpenAI-compatible LLM client wrapper shared across all Assessment agents."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.services.llm_extractor import (
    ConnectionTestResult,
    ExtractorConfig,
    is_official_deepseek,
    list_available_models,
    normalize_base_url,
    run_extraction,
    test_llm_connection,
)
from app.services.redaction import redact, safe_endpoint

from .runtime import RunBudget


@dataclass
class LLMCallResult:
    ok: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    request_count: int = 0
    model_used: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


class UnifiedLLMClient:
    """Thin, framework-free JSON completion client with budget enforcement and redaction."""

    def __init__(self, config: ExtractorConfig):
        self.config = config

    @property
    def is_mock(self) -> bool:
        return self.config.base_url.strip().startswith("mock://")

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key.strip()) and not self.is_mock

    @property
    def effective_base_url(self) -> str:
        return safe_endpoint(normalize_base_url(self.config.base_url) or "https://api.openai.com/v1")

    @property
    def effective_model(self) -> str:
        return self.config.model.strip() or "gpt-4o-mini"

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        budget: RunBudget | None = None,
        max_tokens: int | None = None,
    ) -> LLMCallResult:
        if budget is not None and not budget.can_call():
            return LLMCallResult(
                ok=False,
                error_code="budget_exhausted",
                error_message=f"LLM 调用预算已耗尽（上限 {budget.max_calls} 次）",
            )
        if not self.is_configured:
            code = "mock_mode" if self.is_mock else "unconfigured"
            msg = (
                "当前智能体配置为离线 mock:// 模式，未启用在线 LLM 服务。"
                if self.is_mock
                else "当前智能体未配置可用 API Key，无法发起在线 LLM 调用。"
            )
            return LLMCallResult(ok=False, error_code=code, error_message=msg)

        configured = replace(
            self.config,
            domain_instruction=system_prompt,
            extraction_schema={},
            require_source_quote=False,
            request_budget=1,
            max_tokens=min(self.config.max_tokens, max_tokens or self.config.max_tokens),
        )
        res = await run_extraction(configured, user_content, [])
        debug = res.debug_info or {}
        req_count = int(debug.get("request_count") or 0)
        if budget is not None:
            budget.consume_call(max(1, req_count))

        safe_err = redact(res.error_message, self.config.api_key) if res.error_message else None
        if res.status == "success" and isinstance(res.extracted_json, dict):
            return LLMCallResult(
                ok=True,
                data=redact(res.extracted_json, self.config.api_key),
                request_count=req_count,
                model_used=debug.get("model_used"),
                finish_reason=debug.get("finish_reason"),
                usage=debug.get("usage") or {},
                duration_ms=int(debug.get("duration_ms") or 0),
            )
        return LLMCallResult(
            ok=False,
            error_code=debug.get("error_code") or "llm_call_failed",
            error_message=safe_err or "LLM 调用未能返回有效 JSON 结果",
            request_count=req_count,
            model_used=debug.get("model_used"),
            finish_reason=debug.get("finish_reason"),
            usage=debug.get("usage") or {},
            duration_ms=int(debug.get("duration_ms") or 0),
        )


__all__ = [
    "ConnectionTestResult",
    "ExtractorConfig",
    "LLMCallResult",
    "UnifiedLLMClient",
    "is_official_deepseek",
    "list_available_models",
    "normalize_base_url",
    "redact",
    "safe_endpoint",
    "test_llm_connection",
]
