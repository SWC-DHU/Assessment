"""Deterministic tool catalog and execution trace recorder for the Resource Matching Agent."""
from __future__ import annotations

from time import perf_counter
from typing import Callable, TypeVar

from domain_semantics.catalog import semantic_mappings
from domain_semantics.handoff import ResourceMatchingInput
from model_assets.catalog import AssetCatalog

from .constraints import build_line_takt_reference, evaluate_asset_constraints
from .ranking import compute_score_breakdown, rank_evaluations
from .retrieval import dereference_input, retrieve_semantic_candidates
from .schema import AgentTraceStep, CandidateEvaluation, ResourceMatchingConfig

T = TypeVar("T")

MATCHING_TOOL_CATALOG: dict[str, tuple[str, str]] = {
    "dereference_requirements": (
        "ResourceMatchingInput",
        "解引用工序能力、物料、状态、信息流及工序/能力/整线三层约束，检查交接就绪状态",
    ),
    "query_ontology_mappings": (
        "ontology_version",
        "查询共享本体与受控词表中的需求侧↔资源侧语义映射规则",
    ),
    "search_assets_by_capability": (
        "operation_id, required_capability_codes",
        "按共享标准制造能力编码检索完整覆盖与部分覆盖候选模型资产",
    ),
    "verify_asset_constraints": (
        "operation, model_asset, partial_capability?",
        "执行能力、物料、状态转换、数值参数（含单位换算）及信息接口的三值（PASS/FAIL/UNKNOWN）约束校验",
    ),
    "score_and_rank_candidates": (
        "evaluations, scoring_weights, top_k",
        "在严格保持硬约束分层前提下计算综合评分并输出 Top-k 排序",
    ),
    "llm_semantic_disambiguation": (
        "operation_summary, candidate_summaries",
        "（可选）由大模型辅助理解复杂定性工艺要求、设备文本条件并生成工程匹配说明",
    ),
}


class MatchingTrace:
    def __init__(self) -> None:
        self.steps: list[AgentTraceStep] = []

    def call(self, step: str, tool: str, fn: Callable[..., T], *args, summary_fn: Callable[[T], str] | None = None, **kwargs) -> T:
        started = perf_counter()
        result = fn(*args, **kwargs)
        elapsed = round((perf_counter() - started) * 1000, 2)
        summary = summary_fn(result) if summary_fn else f"{tool} 执行完成"
        self.steps.append(AgentTraceStep(step=step, tool=tool, status="success", duration_ms=elapsed, summary=summary))
        return result

    def record(self, step: str, tool: str, status: str, duration_ms: float, summary: str) -> None:
        self.steps.append(
            AgentTraceStep(
                step=step,
                tool=tool,
                status=status,  # type: ignore[arg-type]
                duration_ms=round(max(0.0, duration_ms), 2),
                summary=summary,
            )
        )


def list_matching_tools() -> list[dict[str, str]]:
    return [{"id": key, "input": args, "output": desc} for key, (args, desc) in MATCHING_TOOL_CATALOG.items()]


def tool_evaluate_candidate(
    op,
    asset,
    peers,
    context,
    config: ResourceMatchingConfig,
    *,
    partial_capability: str | None = None,
) -> CandidateEvaluation:
    hard_verdict, checks, match_reasons, rejection_reasons, missing_info = evaluate_asset_constraints(
        op, asset, context, partial_capability=partial_capability
    )
    scores = compute_score_breakdown(
        op, asset, peers, checks, config.weights, partial_capability=partial_capability
    )
    req_caps = [partial_capability] if partial_capability else list(op.required_capability_codes)
    provided = set(asset.capability_codes)
    covered = [c for c in req_caps if c in provided]
    missing_caps = [c for c in req_caps if c not in provided]
    coverage = round(len(covered) / max(1, len(req_caps)), 4)

    if hard_verdict == "FAIL":
        disposition = "rejected"
    elif hard_verdict == "UNKNOWN":
        disposition = "pending_confirmation"
    elif partial_capability is not None:
        disposition = "partial_qualified"
    else:
        disposition = "qualified"

    return CandidateEvaluation(
        asset_id=asset.asset_id,
        asset_name=asset.identity.name,
        asset_category=asset.identity.category,
        data_source=asset.data_source,
        binding_status=asset.model_binding.binding_status,
        disposition=disposition,
        covered_capabilities=covered,
        missing_capabilities=missing_caps,
        capability_coverage=coverage,
        hard_constraint_verdict=hard_verdict,
        constraint_checks=checks,
        score_breakdown=scores,
        match_reasons=match_reasons,
        rejection_reasons=rejection_reasons,
        missing_information=missing_info,
        line_takt_reference=build_line_takt_reference(op, asset, context),
    )


__all__ = [
    "MATCHING_TOOL_CATALOG",
    "MatchingTrace",
    "dereference_input",
    "list_matching_tools",
    "rank_evaluations",
    "retrieve_semantic_candidates",
    "semantic_mappings",
    "tool_evaluate_candidate",
]
