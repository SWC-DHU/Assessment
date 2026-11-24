"""Deterministic tool catalog, LLM strategy verification gate, and execution trace recorder."""
from __future__ import annotations

from time import perf_counter
from typing import Any, Callable, TypeVar

from domain_semantics.catalog import load_vocabulary, semantic_mappings
from model_assets.catalog import AssetCatalog
from model_assets.schema import ModelAsset

from .constraints import build_line_takt_reference, evaluate_asset_constraints
from .ranking import compute_score_breakdown, rank_evaluations
from .retrieval import DereferencedMatchingContext, DereferencedOperation, dereference_input, retrieve_semantic_candidates
from .schema import (
    AgentTraceStep,
    CandidateEvaluation,
    ConstraintCheckItem,
    LLMStrategyRecord,
    OperationMatchingResult,
    ResourceMatchingConfig,
)

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
    "search_assets_by_category": (
        "asset_categories, capability_codes?",
        "按受控资产类别编码检索真实资产库候选，支持 LLM 策略调整后的定向召回",
    ),
    "verify_asset_constraints": (
        "operation, model_asset, partial_capability?",
        "执行能力、物料、状态转换、数值参数（含单位换算）及信息接口的三值（PASS/FAIL/UNKNOWN）约束校验",
    ),
    "verify_llm_strategy_proposal": (
        "operation_result, llm_proposal, catalog",
        "验证 LLM 提出的候选资产、类别偏好与软约束判定，过滤幻觉 ID 并强制复验全部硬约束",
    ),
    "score_and_rank_candidates": (
        "evaluations, scoring_weights, top_k",
        "在严格保持硬约束分层前提下计算综合评分并输出 Top-k 排序",
    ),
    "llm_semantic_disambiguation": (
        "difficult_operations, ontology_vocab, catalog_summary",
        "（LLM 增强模式）针对歧义设备条件、定性约束、多能力拆分及未匹配工序提出检索策略与消歧建议",
    ),
}


class MatchingTrace:
    def __init__(self) -> None:
        self.steps: list[AgentTraceStep] = []

    def call(
        self,
        step: str,
        tool: str,
        fn: Callable[..., T],
        *args,
        summary_fn: Callable[[T], str] | None = None,
        **kwargs,
    ) -> T:
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


def search_assets_by_category(
    catalog: AssetCatalog,
    categories: list[str],
    capability_codes: list[str] | None = None,
) -> list[ModelAsset]:
    """Deterministic lookup of real catalog assets filtered by controlled category codes."""
    known_cats = set(load_vocabulary().get("asset_categories", {}).keys())
    valid_cats = [cat for cat in categories if cat in known_cats]
    if not valid_cats:
        return []
    req_caps = set(capability_codes or [])
    matched: list[ModelAsset] = []
    for asset in catalog.all_assets():
        if asset.identity.category not in valid_cats:
            continue
        if req_caps and not (req_caps & set(asset.capability_codes)):
            continue
        matched.append(asset)
    return matched


def tool_evaluate_candidate(
    op: DereferencedOperation,
    asset: ModelAsset,
    peers: list[ModelAsset],
    context: DereferencedMatchingContext,
    config: ResourceMatchingConfig,
    *,
    partial_capability: str | None = None,
    soft_judgments: dict[str, tuple[bool, str]] | None = None,
    preferred_categories: set[str] | None = None,
) -> CandidateEvaluation:
    hard_verdict, checks, match_reasons, rejection_reasons, missing_info = evaluate_asset_constraints(
        op, asset, context, partial_capability=partial_capability
    )

    # Apply ONLY verified LLM soft-constraint refinements (NEVER override is_hard=True checks!)
    refined_checks: list[ConstraintCheckItem] = []
    for chk in checks:
        if not chk.is_hard and soft_judgments and chk.scope in soft_judgments and chk.verdict == "UNKNOWN":
            satisfied, rationale = soft_judgments[chk.scope]
            next_verdict = "PASS" if satisfied else "UNKNOWN"
            refined_checks.append(
                chk.model_copy(
                    update={
                        "verdict": next_verdict,
                        "rationale": f"[LLM语义消歧复核] {rationale}",
                    }
                )
            )
        else:
            refined_checks.append(chk)

    # Recompute match_reasons / missing_info after soft refinement (hard verdict is immutable!)
    match_reasons = [item.rationale for item in refined_checks if item.verdict == "PASS"]
    missing_info = [item.rationale for item in refined_checks if item.verdict == "UNKNOWN"]
    if preferred_categories and asset.identity.category in preferred_categories and hard_verdict == "PASS":
        match_reasons.append(f"符合语义消歧推荐设备类别：{asset.identity.category}。")

    scores = compute_score_breakdown(
        op, asset, peers, refined_checks, config.weights, partial_capability=partial_capability
    )
    # Mild tie-breaker bonus (+0.02 capped at 1.0) when asset matches verified LLM-preferred category (never overrides hard FAIL/UNKNOWN!)
    if preferred_categories and asset.identity.category in preferred_categories and hard_verdict == "PASS":
        boosted = round(min(1.0, scores.total_score + 0.02), 4)
        scores = scores.model_copy(update={"total_score": boosted})

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
        constraint_checks=refined_checks,
        score_breakdown=scores,
        match_reasons=match_reasons,
        rejection_reasons=rejection_reasons,
        missing_information=missing_info,
        line_takt_reference=build_line_takt_reference(op, asset, context),
    )


def verify_llm_strategy_proposal(
    op: DereferencedOperation,
    current_result: OperationMatchingResult,
    proposal: dict[str, Any],
    context: DereferencedMatchingContext,
    catalog: AssetCatalog,
    config: ResourceMatchingConfig,
    *,
    trigger_reasons: list[str],
    retries_used: int = 0,
) -> tuple[OperationMatchingResult, str | None]:
    """Deterministic gate verifying LLM strategy adjustments; rejects hallucinations and enforces hard constraints."""
    known_cats = set(load_vocabulary().get("asset_categories", {}).keys())
    known_caps = set(load_vocabulary().get("capabilities", {}).keys())

    raw_ids = [str(x).strip() for x in (proposal.get("additional_asset_ids") or []) if isinstance(x, str) and x.strip()]
    raw_cats = [str(x).strip() for x in (proposal.get("preferred_categories") or []) if isinstance(x, str) and x.strip()]
    valid_cats = [c for c in raw_cats if c in known_cats]

    # Parse capability decomposition hints strictly against controlled vocabulary
    decomp_hints: list[list[str]] = []
    for grp in proposal.get("capability_decomposition_hint") or []:
        if isinstance(grp, list):
            clean_grp = [str(c).strip() for c in grp if isinstance(c, str) and str(c).strip() in known_caps]
            if clean_grp:
                decomp_hints.append(clean_grp)

    # Parse soft qualitative judgments keyed by (asset_id, scope)
    soft_by_asset: dict[str, dict[str, tuple[bool, str]]] = {}
    qualitative_notes: list[str] = []
    for item in proposal.get("qualitative_judgments") or []:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("asset_id") or "").strip()
        scope = str(item.get("scope") or "").strip()
        if not aid or scope not in {"equipment", "operation", "capability"}:
            continue
        if catalog.get(aid) is None:
            continue
        satisfied = bool(item.get("satisfied"))
        rationale = str(item.get("rationale") or "语义消歧确认").strip()[:160]
        soft_by_asset.setdefault(aid, {})[scope] = (satisfied, rationale)
        qualitative_notes.append(f"{aid}({scope}): {'满足' if satisfied else '待确认'} — {rationale}")

    # Verify every proposed asset_id exists in the catalog (anti-hallucination gate!)
    hallucinated_ids: list[str] = []
    proposed_real_assets: list[ModelAsset] = []
    for aid in dict.fromkeys(raw_ids):
        found = catalog.get(aid)
        if found is None:
            hallucinated_ids.append(aid)
        else:
            proposed_real_assets.append(found)

    # Expand pool via controlled category lookup (deterministic tool!)
    category_assets = search_assets_by_category(catalog, valid_cats, op.required_capability_codes)

    full_pool, partial_pool = retrieve_semantic_candidates(op, catalog)
    existing_ids = {a.asset_id for a in full_pool}
    combined_full_map: dict[str, ModelAsset] = {a.asset_id: a for a in full_pool}
    for asset in [*proposed_real_assets, *category_assets]:
        combined_full_map.setdefault(asset.asset_id, asset)
    combined_pool = list(combined_full_map.values())

    pref_cat_set = set(valid_cats)
    qualified: list[CandidateEvaluation] = []
    pending: list[CandidateEvaluation] = []
    rejected: list[CandidateEvaluation] = []
    verified_added: list[str] = []
    rejected_hard: list[str] = []

    for asset in combined_pool:
        ev = tool_evaluate_candidate(
            op,
            asset,
            combined_pool,
            context,
            config,
            soft_judgments=soft_by_asset.get(asset.asset_id),
            preferred_categories=pref_cat_set or None,
        )
        if ev.disposition == "qualified":
            qualified.append(ev)
            if asset.asset_id not in existing_ids and asset.asset_id in raw_ids:
                verified_added.append(asset.asset_id)
        elif ev.disposition == "pending_confirmation":
            pending.append(ev)
            if asset.asset_id not in existing_ids and asset.asset_id in raw_ids:
                verified_added.append(asset.asset_id)
        else:
            rejected.append(ev)
            if asset.asset_id in raw_ids:
                rejected_hard.append(asset.asset_id)

    # Also keep partial-pool rejection diagnostics if enabled
    if config.include_rejected_details:
        already_rejected = {c.asset_id for c in rejected}
        for asset in partial_pool:
            if asset.asset_id in combined_full_map or asset.asset_id in already_rejected:
                continue
            ev = tool_evaluate_candidate(op, asset, [*combined_pool, *partial_pool], context, config)
            if ev.disposition == "rejected":
                rejected.append(ev)

    top_k = rank_evaluations(qualified, limit=config.top_k)
    pending_ranked = rank_evaluations(pending)
    rejected_ranked = rank_evaluations(rejected) if config.include_rejected_details else []

    # Re-evaluate match_status faithfully after deterministic verification
    composable_all = bool(current_result.partial_candidates_by_capability) and all(
        bool(g.candidates) for g in current_result.partial_candidates_by_capability
    )
    if top_k:
        next_status = "matched_single"
    elif composable_all:
        next_status = "matched_partial_composable"
    elif pending_ranked or any(g.pending_candidates for g in current_result.partial_candidates_by_capability):
        next_status = "pending_only"
    else:
        next_status = "unmatched"

    extra_diags = list(current_result.diagnostics)
    if hallucinated_ids:
        extra_diags.append(f"确定性工具拦截了 LLM 虚构的资产 ID：{', '.join(hallucinated_ids)}。")
    if rejected_hard:
        extra_diags.append(f"LLM 建议的候选 {', '.join(rejected_hard)} 未通过硬约束校验，已强制淘汰。")

    explanation = proposal.get("explanation")
    clean_explanation = str(explanation).strip() if isinstance(explanation, str) and explanation.strip() else current_result.llm_explanation
    upstream_fb = proposal.get("upstream_feedback")
    clean_fb = str(upstream_fb).strip() if isinstance(upstream_fb, str) and upstream_fb.strip() else None

    strategy_record = LLMStrategyRecord(
        triggered=True,
        trigger_reasons=trigger_reasons,
        proposed_asset_ids=raw_ids,
        verified_added_asset_ids=verified_added,
        rejected_hallucinated_ids=hallucinated_ids,
        rejected_hard_constraint_ids=rejected_hard,
        preferred_categories=valid_cats,
        capability_decomposition_hint=decomp_hints,
        qualitative_refinements=qualitative_notes,
        retries_used=retries_used,
    )

    updated = current_result.model_copy(
        update={
            "match_status": next_status,
            "top_k_candidates": top_k,
            "pending_candidates": pending_ranked,
            "rejected_candidates": rejected_ranked,
            "llm_strategy": strategy_record,
            "llm_explanation": clean_explanation,
            "diagnostics": list(dict.fromkeys(extra_diags)),
        }
    )
    return updated, clean_fb


__all__ = [
    "MATCHING_TOOL_CATALOG",
    "MatchingTrace",
    "dereference_input",
    "list_matching_tools",
    "rank_evaluations",
    "retrieve_semantic_candidates",
    "search_assets_by_category",
    "semantic_mappings",
    "tool_evaluate_candidate",
    "verify_llm_strategy_proposal",
]
