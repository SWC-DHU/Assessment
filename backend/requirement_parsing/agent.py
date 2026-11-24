"""Standalone entry point for the Requirement Parsing Agent."""
from __future__ import annotations

from time import perf_counter

from agents.config import ResolvedAgentRuntime
from agents.runtime import AgentRunMetadata
from app.services.llm_extractor import ExtractionResult, ExtractorConfig
from app.services.semantic_execution import PROMPT_VERSION, execute
from domain_semantics.handoff import ResourceMatchingInput, build_resource_matching_input
from domain_semantics.schema import BatchReport, SemanticModel
from domain_semantics.sources import Document


async def run_requirement_parsing(
    config: ExtractorConfig,
    message: Document,
    files: list[Document],
    run_id: str,
    base: SemanticModel | None,
    *,
    runtime: ResolvedAgentRuntime | None = None,
    offline: bool = False,
    failed: list[str] | None = None,
    session_id: str | None = None,
    publish=None,
    resume: list[BatchReport] | None = None,
) -> tuple[SemanticModel | None, ExtractionResult, ResourceMatchingInput | None, AgentRunMetadata]:
    """Execute the verified requirement parsing pipeline with standardized multi-agent run metadata."""
    started = perf_counter()
    model, result = await execute(
        config,
        message,
        files,
        run_id,
        base,
        offline=offline,
        failed=failed,
        session_id=session_id,
        publish=publish,
        resume=resume,
    )
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    handoff = build_resource_matching_input(model) if model is not None else None
    debug = result.debug_info or {}

    snapshot = (
        runtime.non_sensitive_snapshot(prompt_version=PROMPT_VERSION)
        if runtime is not None
        else {
            "agent_type": "requirement_parsing",
            "effective_model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "timeout_seconds": config.timeout_seconds,
            "request_budget": config.request_budget,
            "prompt_version": PROMPT_VERSION,
        }
    )
    metadata = AgentRunMetadata(
        agent_type="requirement_parsing",
        agent_id=runtime.agent_id if runtime else None,
        agent_name=runtime.agent_name if runtime else None,
        model_requested=config.model,
        model_used=debug.get("model_used"),
        prompt_version=PROMPT_VERSION,
        input_version=base.run_id if base else None,
        execution_mode="offline_deterministic" if offline else "semantic_hybrid",
        config_snapshot=snapshot,
        tool_calls_count=len(debug.get("tool_trace") or []),
        llm_calls=int(debug.get("request_count") or 0),
        llm_retries=0,
        fallback_triggered=bool(debug.get("json_mode_fallback")),
        fallback_reason="json_mode_fallback" if debug.get("json_mode_fallback") else None,
        elapsed_ms=elapsed_ms,
    )
    result.debug_info["agent_metadata"] = metadata.model_dump(mode="json")
    return model, result, handoff, metadata
