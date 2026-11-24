"""Resource Matching Agent: deterministic + LLM-assisted semantic disambiguation, strategy refinement & A/B/C methods."""
from __future__ import annotations

import json
from time import perf_counter
import uuid

from agents.config import ResolvedAgentRuntime
from agents.llm_client import ExtractorConfig, UnifiedLLMClient
from agents.runtime import AgentRunMetadata, RunBudget
from domain_semantics.catalog import load_vocabulary, semantic_mappings
from domain_semantics.handoff import ResourceMatchingInput
from model_assets.catalog import AssetCatalog, get_default_catalog
from model_assets.validation import compute_completeness

from .constraints import build_line_takt_reference, evaluate_asset_constraints
from .prompts import MATCHING_PROMPT_VERSION, compose_matching_system_prompt
from .ranking import rank_evaluations
from .retrieval import (
    DereferencedMatchingContext,
    DereferencedOperation,
    dereference_input,
    retrieve_keyword_candidates,
    retrieve_semantic_candidates,
)
from .schema import (
    CandidateEvaluation,
    CapabilityPartialCandidates,
    LineConstraintReference,
    MatchingDiagnostic,
    MatchingSummary,
    OperationMatchingResult,
    ResourceMatchingConfig,
    ResourceMatchingOutput,
    ScoreBreakdown,
    StructuredFeedbackItem,
    UnmetRequirement,
)
from .tools import MatchingTrace, tool_evaluate_candidate, verify_llm_strategy_proposal


class MatchingExecutionError(RuntimeError):
    """Raised when fail_fast fallback_policy forbids silent degradation on LLM errors."""

    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        super().__init__(message)


def _build_line_refs(context: DereferencedMatchingContext) -> list[LineConstraintReference]:
    return [
        LineConstraintReference(
            constraint_id=c.id,
            name=c.name,
            parameter_code=c.parameter_code,
            comparator=c.comparator,
            value=c.value,
            lower=c.lower,
            upper=c.upper,
            unit=c.unit,
            basis=c.basis,
            strength=c.strength,
            description=c.description,
        )
        for c in context.line_constraints
    ]


def _cap_label(code: str) -> str:
    aliases = load_vocabulary().get("capabilities", {}).get(code, [])
    return aliases[0] if aliases else code


def _match_operation_method_c(
    op: DereferencedOperation,
    context: DereferencedMatchingContext,
    catalog: AssetCatalog,
    config: ResourceMatchingConfig,
) -> OperationMatchingResult:
    base_fields = dict(
        operation_id=op.operation_id,
        operation_number=op.operation_number,
        operation_name=op.operation_name,
        operation_type=op.operation_type,
        required_capability_codes=list(op.required_capability_codes),
        input_materials=[m.raw_name for m in op.input_materials],
        output_materials=[m.raw_name for m in op.output_materials],
        input_states=[s.raw_name for s in op.input_states],
        output_states=[s.raw_name for s in op.output_states],
        information_flows=[f"{f.direction}:{f.name}" for f in op.information_flows],
        equipment_requirement=op.equipment_requirement,
        operation_constraint_ids=[c.id for c in op.operation_constraints],
        capability_constraint_ids=[c.id for _, c in op.capability_constraints],
    )

    if op.has_blocking_issues or not op.required_capability_codes:
        return OperationMatchingResult(
            **base_fields,
            match_status="blocked",
            unmet_requirements=[
                UnmetRequirement(
                    code="operation_handoff_blocked",
                    scope="operation",
                    message="工序在需求交接校验中存在阻塞问题或缺少已核验能力编码，暂不进入匹配。",
                    capability_codes=list(op.required_capability_codes),
                )
            ],
            diagnostics=["工序存在上游交接阻塞项，需先在需求解析阶段修复或人工确认。"],
        )

    full_pool, partial_pool = retrieve_semantic_candidates(op, catalog)
    all_retrieved = [*full_pool, *partial_pool]

    full_qualified: list[CandidateEvaluation] = []
    pending_full: list[CandidateEvaluation] = []
    rejected_all: list[CandidateEvaluation] = []

    for asset in full_pool:
        ev = tool_evaluate_candidate(op, asset, full_pool, context, config)
        if ev.disposition == "qualified":
            full_qualified.append(ev)
        elif ev.disposition == "pending_confirmation":
            pending_full.append(ev)
        else:
            rejected_all.append(ev)

    if config.include_rejected_details:
        for asset in partial_pool:
            ev = tool_evaluate_candidate(op, asset, all_retrieved, context, config)
            if ev.disposition == "rejected":
                rejected_all.append(ev)

    top_k = rank_evaluations(full_qualified, limit=config.top_k)
    pending_ranked = rank_evaluations(pending_full)
    rejected_ranked = rank_evaluations(rejected_all) if config.include_rejected_details else []

    partial_groups: list[CapabilityPartialCandidates] = []
    should_build_partial = len(op.required_capability_codes) > 1 or not top_k
    if should_build_partial:
        for cap_code in op.required_capability_codes:
            cap_assets = [a for a in all_retrieved if cap_code in a.capability_codes]
            cap_qualified: list[CandidateEvaluation] = []
            cap_pending: list[CandidateEvaluation] = []
            for asset in cap_assets:
                ev = tool_evaluate_candidate(
                    op, asset, cap_assets, context, config, partial_capability=cap_code
                )
                if ev.disposition == "partial_qualified":
                    cap_qualified.append(ev)
                elif ev.disposition == "pending_confirmation":
                    cap_pending.append(ev)
            ranked_cap = rank_evaluations(cap_qualified, limit=config.partial_top_k)
            ranked_cap_pending = rank_evaluations(cap_pending, limit=config.partial_top_k)
            partial_groups.append(
                CapabilityPartialCandidates(
                    capability_code=cap_code,
                    capability_name=_cap_label(cap_code),
                    candidates=ranked_cap,
                    pending_candidates=ranked_cap_pending,
                    unmet=not ranked_cap and not ranked_cap_pending,
                )
            )

    unmet: list[UnmetRequirement] = []
    diagnostics: list[str] = []
    composable_all = bool(partial_groups) and all(bool(group.candidates) for group in partial_groups)
    multi_asset_recommended = False

    if top_k:
        match_status = "matched_single"
        if len(op.required_capability_codes) > 1 and composable_all:
            diagnostics.append("该多能力工序同时存在单设备一体化候选与按能力拆分的多模型协同候选，供下游关联建模权衡。")
    elif composable_all:
        match_status = "matched_partial_composable"
        multi_asset_recommended = True
        diagnostics.append("未找到同时满足全部能力与约束的单台模型资产，但按能力拆分后各子能力均存在合格候选，建议多模型协同。")
    elif pending_ranked or any(group.pending_candidates for group in partial_groups):
        match_status = "pending_only"
        unmet.append(
            UnmetRequirement(
                code="pending_confirmation_only",
                scope="operation",
                message="当前仅存在信息待补全的候选模型资产，缺少必要参数或状态定义，无法直接进入合格候选集。",
                capability_codes=list(op.required_capability_codes),
            )
        )
    else:
        match_status = "unmatched"
        missing_cap_codes = [
            group.capability_code for group in partial_groups if not group.candidates and not group.pending_candidates
        ] or list(op.required_capability_codes)
        unmet.append(
            UnmetRequirement(
                code="no_qualified_resource",
                scope="capability" if missing_cap_codes else "operation",
                message="资产库中未检索到同时满足能力、物料、状态及硬约束要求的合格模型资产。",
                capability_codes=missing_cap_codes,
            )
        )

    for group in partial_groups:
        if group.unmet:
            unmet.append(
                UnmetRequirement(
                    code="capability_unmet",
                    scope="capability",
                    message=f"制造能力 {group.capability_code}（{group.capability_name}）缺少合格或待确认候选资产。",
                    capability_codes=[group.capability_code],
                )
            )

    return OperationMatchingResult(
        **base_fields,
        match_status=match_status,
        multi_asset_recommended=multi_asset_recommended,
        top_k_candidates=top_k,
        partial_candidates_by_capability=partial_groups,
        pending_candidates=pending_ranked,
        rejected_candidates=rejected_ranked,
        unmet_requirements=unmet,
        diagnostics=diagnostics,
    )


def _match_operation_method_a(
    op: DereferencedOperation,
    context: DereferencedMatchingContext,
    catalog: AssetCatalog,
    config: ResourceMatchingConfig,
) -> OperationMatchingResult:
    """Method A baseline: Keyword-only matching (audited by deterministic checks)."""
    base_fields = dict(
        operation_id=op.operation_id,
        operation_number=op.operation_number,
        operation_name=op.operation_name,
        operation_type=op.operation_type,
        required_capability_codes=list(op.required_capability_codes),
        input_materials=[m.raw_name for m in op.input_materials],
        output_materials=[m.raw_name for m in op.output_materials],
        input_states=[s.raw_name for s in op.input_states],
        output_states=[s.raw_name for s in op.output_states],
        information_flows=[f"{f.direction}:{f.name}" for f in op.information_flows],
        equipment_requirement=op.equipment_requirement,
        operation_constraint_ids=[c.id for c in op.operation_constraints],
        capability_constraint_ids=[c.id for _, c in op.capability_constraints],
    )
    if op.has_blocking_issues:
        return OperationMatchingResult(**base_fields, match_status="blocked")

    hits = retrieve_keyword_candidates(op, catalog)[: config.top_k]
    candidates_out: list[CandidateEvaluation] = []
    for idx, (asset, kw_score, matched_tokens) in enumerate(hits, start=1):
        hard_verdict, checks, _, rej, miss = evaluate_asset_constraints(op, asset, context)
        req = list(op.required_capability_codes)
        provided = set(asset.capability_codes)
        covered = [c for c in req if c in provided]
        missing = [c for c in req if c not in provided]
        cov = round(len(covered) / max(1, len(req)), 4) if req else 0.0
        comp, _ = compute_completeness(asset)
        score = ScoreBreakdown(
            functional_fit=round(kw_score, 4),
            material_fit=round(kw_score, 4),
            technical_performance=0.5,
            modeling_completeness=comp,
            total_score=round(kw_score, 4),
        )
        disposition = "qualified" if hard_verdict == "PASS" else ("pending_confirmation" if hard_verdict == "UNKNOWN" else "rejected")
        candidates_out.append(
            CandidateEvaluation(
                asset_id=asset.asset_id,
                asset_name=asset.identity.name,
                asset_category=asset.identity.category,
                data_source=asset.data_source,
                binding_status=asset.model_binding.binding_status,
                disposition=disposition,
                rank=idx,
                covered_capabilities=covered,
                missing_capabilities=missing,
                capability_coverage=cov,
                hard_constraint_verdict=hard_verdict,
                constraint_checks=checks,
                score_breakdown=score,
                match_reasons=[f"关键词命中（{', '.join(matched_tokens[:6])}），词面相似度 {kw_score:.2f}"],
                rejection_reasons=rej,
                missing_information=miss,
                line_takt_reference=build_line_takt_reference(op, asset, context),
            )
        )

    qualified = [c for c in candidates_out if c.disposition == "qualified"]
    pending = [c for c in candidates_out if c.disposition == "pending_confirmation"]
    rejected = [c for c in candidates_out if c.disposition == "rejected"]
    status = "matched_single" if qualified else ("pending_only" if pending else "unmatched")
    return OperationMatchingResult(
        **base_fields,
        match_status=status,
        top_k_candidates=qualified,
        pending_candidates=pending,
        rejected_candidates=rejected,
        diagnostics=["方法A（关键词基线）按词面相似度召回 Top-k，随后标注实际硬约束核验结果。"],
    )


def _needs_llm_strategy(op: DereferencedOperation, res: OperationMatchingResult) -> list[str]:
    """Identify why an operation benefits from LLM semantic disambiguation or retrieval strategy refinement."""
    reasons: list[str] = []
    if res.match_status in {"unmatched", "pending_only", "matched_partial_composable"}:
        reasons.append(f"初始匹配状态为 {res.match_status}")
    if op.equipment_requirement:
        has_unknown_eq = any(
            any(chk.scope == "equipment" and chk.verdict == "UNKNOWN" for chk in c.constraint_checks)
            for c in [*res.top_k_candidates, *res.pending_candidates, *res.rejected_candidates[:3]]
        )
        if has_unknown_eq or not res.top_k_candidates:
            reasons.append("存在待消歧的设备文本条件")
    has_unknown_qual = any(
        any(not chk.is_hard and chk.scope in {"operation", "capability"} and chk.verdict == "UNKNOWN" for chk in c.constraint_checks)
        for c in [*res.top_k_candidates, *res.pending_candidates]
    )
    if has_unknown_qual:
        reasons.append("存在待核验的定性工艺约束")
    if len(op.required_capability_codes) > 1 and res.partial_candidates_by_capability:
        reasons.append("多能力工序需权衡单设备与多模型协同拆分")
    return reasons


def _compact_catalog_summary(catalog: AssetCatalog) -> list[dict]:
    return [
        {
            "asset_id": a.asset_id,
            "name": a.identity.name,
            "category": a.identity.category,
            "capabilities": a.capability_codes,
            "materials": a.material_compatibility.supported_material_types,
            "description": a.identity.description[:90],
        }
        for a in catalog.all_assets()
    ]


async def _run_llm_collaborative_stage(
    client: UnifiedLLMClient,
    budget: RunBudget,
    context: DereferencedMatchingContext,
    op_results: list[OperationMatchingResult],
    catalog: AssetCatalog,
    config: ResourceMatchingConfig,
    trace: MatchingTrace,
    *,
    custom_system_prompt: str = "",
    custom_business_rules: str = "",
) -> tuple[list[OperationMatchingResult], list[StructuredFeedbackItem], str | None, bool, str | None]:
    """Execute LLM semantic disambiguation + retrieval strategy adjustment with deterministic tool verification."""
    op_by_id = {op.operation_id: op for op in context.operations}
    feedbacks: list[StructuredFeedbackItem] = []
    model_used: str | None = None
    fallback_triggered = False
    fallback_reason: str | None = None

    targets: list[tuple[DereferencedOperation, OperationMatchingResult, list[str]]] = []
    for res in op_results:
        if res.match_status == "blocked":
            continue
        op = op_by_id.get(res.operation_id)
        if op is None:
            continue
        reasons = _needs_llm_strategy(op, res)
        if reasons:
            targets.append((op, res, reasons))

    vocab = load_vocabulary()
    catalog_summary = _compact_catalog_summary(catalog)
    result_map = {r.operation_id: r for r in op_results}

    # Strategy disambiguation & retrieval adjustment round (+ bounded retry on still-unmatched ops)
    if targets:
        attempt = 0
        pending_targets = list(targets)
        while pending_targets and (attempt == 0 or budget.can_retry()):
            if attempt > 0:
                budget.consume_retry()
            started = perf_counter()
            system_prompt = compose_matching_system_prompt(
                "strategy_disambiguation",
                custom_system_prompt=custom_system_prompt,
                custom_business_rules=custom_business_rules,
            )
            payload = {
                "attempt": attempt + 1,
                "is_retry_for_unmatched": attempt > 0,
                "controlled_categories": list(vocab.get("asset_categories", {}).keys()),
                "controlled_capabilities": list(vocab.get("capabilities", {}).keys()),
                "operations": [
                    {
                        "operation_id": op.operation_id,
                        "operation_name": op.operation_name,
                        "required_capabilities": op.required_capability_codes,
                        "input_materials": [m.raw_name for m in op.input_materials],
                        "output_materials": [m.raw_name for m in op.output_materials],
                        "equipment_requirement": op.equipment_requirement,
                        "qualitative_constraints": [c.description or c.name for c in op.operation_constraints if c.comparator is None],
                        "current_status": result_map[op.operation_id].match_status,
                        "trigger_reasons": reasons,
                        "current_top_candidates": [
                            {"asset_id": c.asset_id, "name": c.asset_name, "score": c.score_breakdown.total_score}
                            for c in result_map[op.operation_id].top_k_candidates[:3]
                        ],
                        "current_pending_ids": [c.asset_id for c in result_map[op.operation_id].pending_candidates[:4]],
                    }
                    for op, _, reasons in pending_targets
                ],
                "catalog_summary": catalog_summary,
            }
            call_res = await client.complete_json(
                system_prompt=system_prompt,
                user_content=json.dumps(payload, ensure_ascii=False),
                budget=budget,
            )
            elapsed_ms = (perf_counter() - started) * 1000
            if call_res.model_used:
                model_used = call_res.model_used

            if not call_res.ok or not isinstance(call_res.data, dict):
                fallback_triggered = True
                fallback_reason = call_res.error_message or "LLM 策略消歧调用失败"
                trace.record(
                    f"llm_strategy_round_{attempt + 1}",
                    "llm_semantic_disambiguation",
                    "warning",
                    elapsed_ms,
                    f"LLM 策略调用失败（{call_res.error_code}）：{fallback_reason}",
                )
                if config.fallback_policy == "fail_fast":
                    raise MatchingExecutionError(call_res.error_code or "llm_call_failed", fallback_reason)
                break

            strategies = call_res.data.get("strategies")
            if not isinstance(strategies, list):
                strategies = []
            by_op = {
                str(item.get("operation_id")): item
                for item in strategies
                if isinstance(item, dict) and item.get("operation_id")
            }

            verified_count = 0
            hallucination_count = 0
            still_unmatched: list[tuple[DereferencedOperation, OperationMatchingResult, list[str]]] = []
            verify_start = perf_counter()
            for op, _, reasons in pending_targets:
                proposal = by_op.get(op.operation_id)
                if not proposal:
                    continue
                updated_res, upstream_fb = verify_llm_strategy_proposal(
                    op,
                    result_map[op.operation_id],
                    proposal,
                    context,
                    catalog,
                    config,
                    trigger_reasons=reasons,
                    retries_used=attempt,
                )
                result_map[op.operation_id] = updated_res
                verified_count += 1
                if updated_res.llm_strategy:
                    hallucination_count += len(updated_res.llm_strategy.rejected_hallucinated_ids)
                if upstream_fb:
                    feedbacks.append(
                        StructuredFeedbackItem(
                            feedback_id=f"fb:llm:{op.operation_id}:{attempt + 1}",
                            issue_source="constraint_ambiguity",
                            severity="info",
                            operation_ids=[op.operation_id],
                            message=upstream_fb,
                            suggested_action="请在需求解析工作台或资产库中人工核对后生成新版本（本反馈不修改上游需求）。",
                        )
                    )
                if updated_res.match_status == "unmatched":
                    still_unmatched.append((op, updated_res, [*reasons, "首轮策略调整后仍未匹配"]))

            trace.record(
                f"llm_strategy_round_{attempt + 1}",
                "llm_semantic_disambiguation",
                "success",
                elapsed_ms,
                f"LLM 第 {attempt + 1} 轮协同分析 {len(pending_targets)} 道工序，返回 {len(by_op)} 条策略建议",
            )
            trace.record(
                f"verify_llm_strategy_round_{attempt + 1}",
                "verify_llm_strategy_proposal",
                "warning" if hallucination_count else "success",
                (perf_counter() - verify_start) * 1000,
                f"确定性工具复验 {verified_count} 道工序的 LLM 策略（拦截幻觉 ID {hallucination_count} 个）",
            )

            pending_targets = still_unmatched
            attempt += 1

    # Optional concise explanation pass for remaining operations lacking explanation if budget remains
    unexplained = [r for r in result_map.values() if r.match_status != "blocked" and not r.llm_explanation]
    if unexplained and not fallback_triggered and budget.can_call():
        started = perf_counter()
        system_prompt = compose_matching_system_prompt(
            "explain_results",
            custom_system_prompt=custom_system_prompt,
            custom_business_rules=custom_business_rules,
        )
        exp_payload = [
            {
                "operation_id": r.operation_id,
                "operation_name": r.operation_name,
                "required_capabilities": r.required_capability_codes,
                "match_status": r.match_status,
                "top_candidates": [
                    {"asset_id": c.asset_id, "asset_name": c.asset_name, "score": c.score_breakdown.total_score}
                    for c in r.top_k_candidates[:3]
                ],
            }
            for r in unexplained
        ]
        call_res = await client.complete_json(
            system_prompt=system_prompt,
            user_content=json.dumps(exp_payload, ensure_ascii=False),
            budget=budget,
        )
        if call_res.model_used:
            model_used = call_res.model_used
        if call_res.ok and isinstance(call_res.data, dict) and isinstance(call_res.data.get("explanations"), dict):
            count = 0
            for op_id, text in call_res.data["explanations"].items():
                if op_id in result_map and isinstance(text, str) and text.strip():
                    result_map[op_id] = result_map[op_id].model_copy(update={"llm_explanation": text.strip()})
                    count += 1
            trace.record(
                "llm_result_explanations",
                "llm_semantic_disambiguation",
                "success",
                (perf_counter() - started) * 1000,
                f"补充生成 {count} 道工序的工程匹配说明",
            )

    ordered = [result_map[r.operation_id] for r in op_results]
    return ordered, feedbacks, model_used, fallback_triggered, fallback_reason


async def _run_method_b_llm(
    context: DereferencedMatchingContext,
    catalog: AssetCatalog,
    config: ResourceMatchingConfig,
    llm_config: ExtractorConfig | None,
) -> tuple[list[OperationMatchingResult], dict[str, list[dict]], int, str | None, list[MatchingDiagnostic]]:
    """Method B baseline: direct LLM matching without ontology/constraint engine."""
    diagnostics: list[MatchingDiagnostic] = []
    raw_picks: dict[str, list[dict]] = {}
    client = UnifiedLLMClient(llm_config) if llm_config else None
    if client is None or not client.is_configured:
        msg = "方法B（直接LLM匹配）需要已配置的在线 resource_matching LLM Provider；当前为离线/未配置状态。"
        if config.fallback_policy == "fail_fast":
            raise MatchingExecutionError("unconfigured", msg)
        diagnostics.append(MatchingDiagnostic(code="method_b_llm_unavailable", severity="warning", message=msg))
        empty = [
            OperationMatchingResult(
                operation_id=op.operation_id,
                operation_number=op.operation_number,
                operation_name=op.operation_name,
                operation_type=op.operation_type,
                required_capability_codes=list(op.required_capability_codes),
                input_materials=[m.raw_name for m in op.input_materials],
                output_materials=[m.raw_name for m in op.output_materials],
                input_states=[s.raw_name for s in op.input_states],
                output_states=[s.raw_name for s in op.output_states],
                match_status="unmatched",
                diagnostics=["方法B需要在线 LLM Provider"],
            )
            for op in context.operations
        ]
        return empty, raw_picks, 0, None, diagnostics

    ops_summary = [
        {
            "operation_id": op.operation_id,
            "name": op.operation_name,
            "capabilities": op.required_capability_codes,
            "inputs": [m.raw_name for m in op.input_materials],
            "outputs": [m.raw_name for m in op.output_materials],
            "constraints": [c.description or c.name for c in op.operation_constraints],
        }
        for op in context.operations
    ]
    prompt_body = json.dumps(
        {"top_k": config.top_k, "operations": ops_summary, "assets": _compact_catalog_summary(catalog)},
        ensure_ascii=False,
    )
    sys_prompt = compose_matching_system_prompt("direct_baseline")
    call_res = await client.complete_json(system_prompt=sys_prompt, user_content=prompt_body)
    matches_map = (call_res.data or {}).get("matches", {}) if call_res.ok and isinstance(call_res.data, dict) else {}
    if not isinstance(matches_map, dict):
        matches_map = {}

    results: list[OperationMatchingResult] = []
    for op in context.operations:
        picks = matches_map.get(op.operation_id, [])
        if not isinstance(picks, list):
            picks = []
        raw_picks[op.operation_id] = [p for p in picks if isinstance(p, dict)][: config.top_k]
        qualified: list[CandidateEvaluation] = []
        pending: list[CandidateEvaluation] = []
        rejected: list[CandidateEvaluation] = []
        for idx, pick in enumerate(raw_picks[op.operation_id], start=1):
            aid = str(pick.get("asset_id") or "")
            asset = catalog.get(aid)
            if asset is None:
                continue
            hard_verdict, checks, _, rej, miss = evaluate_asset_constraints(op, asset, context)
            req = list(op.required_capability_codes)
            provided = set(asset.capability_codes)
            covered = [c for c in req if c in provided]
            missing = [c for c in req if c not in provided]
            cov = round(len(covered) / max(1, len(req)), 4) if req else 0.0
            comp, _ = compute_completeness(asset)
            raw_score = float(pick.get("score", 0.75)) if isinstance(pick.get("score"), (int, float)) else 0.75
            raw_score = max(0.0, min(1.0, raw_score))
            ev = CandidateEvaluation(
                asset_id=asset.asset_id,
                asset_name=asset.identity.name,
                asset_category=asset.identity.category,
                data_source=asset.data_source,
                binding_status=asset.model_binding.binding_status,
                disposition="qualified" if hard_verdict == "PASS" else ("pending_confirmation" if hard_verdict == "UNKNOWN" else "rejected"),
                rank=idx,
                covered_capabilities=covered,
                missing_capabilities=missing,
                capability_coverage=cov,
                hard_constraint_verdict=hard_verdict,
                constraint_checks=checks,
                score_breakdown=ScoreBreakdown(
                    functional_fit=raw_score,
                    material_fit=raw_score,
                    technical_performance=raw_score,
                    modeling_completeness=comp,
                    total_score=raw_score,
                ),
                match_reasons=[str(pick.get("reason") or "LLM 直接推荐")],
                rejection_reasons=rej,
                missing_information=miss,
                line_takt_reference=build_line_takt_reference(op, asset, context),
            )
            if ev.disposition == "qualified":
                qualified.append(ev)
            elif ev.disposition == "pending_confirmation":
                pending.append(ev)
            else:
                rejected.append(ev)

        status = "matched_single" if qualified else ("pending_only" if pending else "unmatched")
        results.append(
            OperationMatchingResult(
                operation_id=op.operation_id,
                operation_number=op.operation_number,
                operation_name=op.operation_name,
                operation_type=op.operation_type,
                required_capability_codes=list(op.required_capability_codes),
                input_materials=[m.raw_name for m in op.input_materials],
                output_materials=[m.raw_name for m in op.output_materials],
                input_states=[s.raw_name for s in op.input_states],
                output_states=[s.raw_name for s in op.output_states],
                match_status=status,
                top_k_candidates=qualified,
                pending_candidates=pending,
                rejected_candidates=rejected,
                diagnostics=["方法B（直接LLM匹配）结果，已附加确定性硬约束审计。"],
            )
        )
    return results, raw_picks, call_res.request_count, call_res.model_used, diagnostics


def _build_structured_feedback(
    rmi: ResourceMatchingInput,
    op_results: list[OperationMatchingResult],
    extra_feedback: list[StructuredFeedbackItem],
    fallback_triggered: bool,
    fallback_reason: str | None,
) -> list[StructuredFeedbackItem]:
    items: list[StructuredFeedbackItem] = []

    for idx, issue in enumerate(rmi.handoff_validation.issues):
        items.append(
            StructuredFeedbackItem(
                feedback_id=f"fb:upstream:{idx + 1}",
                issue_source="upstream_requirement",
                severity="blocker" if issue.severity == "blocker" else "warning",
                operation_ids=list(issue.operation_ids),
                message=f"上游需求交接问题（{issue.code}）：{issue.message}",
                suggested_action="请在「需求解析」工作台补充来源或通过人工审核生成新的语义版本（资源匹配不修改上游模型）。",
            )
        )

    for res in op_results:
        if res.match_status == "unmatched":
            items.append(
                StructuredFeedbackItem(
                    feedback_id=f"fb:catalog:{res.operation_id}",
                    issue_source="asset_catalog_gap",
                    severity="warning",
                    operation_ids=[res.operation_id],
                    message=f"工序「{res.operation_name}」（需求能力：{', '.join(res.required_capability_codes)}）在资产库中无合格候选。",
                    suggested_action="建议在「模型资产库」补充覆盖对应能力与物料的设备资产，或复核该工序的约束边界。",
                )
            )
        elif res.match_status == "pending_only":
            items.append(
                StructuredFeedbackItem(
                    feedback_id=f"fb:pending:{res.operation_id}",
                    issue_source="asset_catalog_gap",
                    severity="info",
                    operation_ids=[res.operation_id],
                    message=f"工序「{res.operation_name}」仅存在信息待补全的候选资产（{len(res.pending_candidates)} 项）。",
                    suggested_action="建议在「模型资产库」补全候选资产的技术参数、加工时间或状态转换后重新执行匹配。",
                )
            )

    if fallback_triggered and fallback_reason:
        items.append(
            StructuredFeedbackItem(
                feedback_id="fb:runtime:fallback",
                issue_source="llm_runtime",
                severity="warning",
                operation_ids=[],
                message=f"LLM 增强模式已触发安全降级：{fallback_reason}",
                suggested_action="已自动降级为纯确定性语义+多约束匹配并保留完整结果；如需启用 LLM 协同，请在「资源匹配智能体」配置有效的在线 LLM。",
            )
        )

    items.extend(extra_feedback)
    return items


async def run_resource_matching(
    rmi: ResourceMatchingInput,
    *,
    catalog: AssetCatalog | None = None,
    config: ResourceMatchingConfig | None = None,
    llm_config: ExtractorConfig | None = None,
    runtime: ResolvedAgentRuntime | None = None,
    run_id: str | None = None,
) -> ResourceMatchingOutput:
    """Execute the Resource Matching Agent pipeline with strict role isolation & deterministic verification."""
    started = perf_counter()
    active_catalog = catalog or get_default_catalog()
    active_config = config or ResourceMatchingConfig()
    effective_run_id = run_id or str(uuid.uuid4())
    effective_llm_cfg = llm_config or (runtime.llm_config if runtime else None)
    trace = MatchingTrace()
    diagnostics: list[MatchingDiagnostic] = []
    extra_feedback: list[StructuredFeedbackItem] = []

    budget = RunBudget(
        max_calls=active_config.max_llm_calls,
        max_retries=active_config.max_strategy_retries,
    )
    llm_calls = 0
    llm_retries = 0
    llm_model_used: str | None = None
    fallback_triggered = False
    fallback_reason: str | None = None

    def make_metadata(mode: str, elapsed: float) -> AgentRunMetadata:
        snapshot = (
            runtime.non_sensitive_snapshot(
                prompt_version=MATCHING_PROMPT_VERSION,
                extra={
                    "method": active_config.method,
                    "llm_mode": active_config.llm_mode,
                    "top_k": active_config.top_k,
                    "fallback_policy": active_config.fallback_policy,
                    "max_llm_calls": active_config.max_llm_calls,
                    "max_strategy_retries": active_config.max_strategy_retries,
                },
            )
            if runtime is not None
            else {
                "agent_type": "resource_matching",
                "method": active_config.method,
                "llm_mode": active_config.llm_mode,
                "top_k": active_config.top_k,
                "fallback_policy": active_config.fallback_policy,
                "prompt_version": MATCHING_PROMPT_VERSION,
                "effective_model": effective_llm_cfg.model if effective_llm_cfg else None,
            }
        )
        return AgentRunMetadata(
            agent_type="resource_matching",
            agent_id=runtime.agent_id if runtime else None,
            agent_name=runtime.agent_name if runtime else None,
            model_requested=effective_llm_cfg.model if effective_llm_cfg else None,
            model_used=llm_model_used,
            prompt_version=MATCHING_PROMPT_VERSION,
            input_version=rmi.source_model.run_id,
            execution_mode=mode,
            config_snapshot=snapshot,
            tool_calls_count=len(trace.steps),
            llm_calls=llm_calls,
            llm_retries=llm_retries,
            fallback_triggered=fallback_triggered,
            fallback_reason=fallback_reason,
            elapsed_ms=elapsed,
        )

    # Step 1: Dereference and check handoff status
    context = trace.call(
        "requirement_dereferencing",
        "dereference_requirements",
        dereference_input,
        rmi,
        summary_fn=lambda ctx: f"解引用 {len(ctx.operations)} 道工序与 {len(ctx.line_constraints)} 条整线约束（交接状态：{ctx.handoff_status}）",
    )

    # Step 2: Query ontology demand↔resource mappings
    trace.call(
        "ontology_mapping_lookup",
        "query_ontology_mappings",
        semantic_mappings,
        summary_fn=lambda items: f"加载 {len(items)} 条共享本体需求↔资源语义映射规则",
    )

    line_refs = _build_line_refs(context)

    if rmi.handoff_validation.status == "blocked" and not active_config.force_blocked_diagnostic:
        for issue in rmi.handoff_validation.issues:
            if issue.severity == "blocker":
                diagnostics.append(
                    MatchingDiagnostic(
                        code=issue.code,
                        severity="blocker",
                        operation_id=issue.operation_ids[0] if issue.operation_ids else None,
                        message=f"上游交接阻塞：{issue.message}",
                    )
                )
        blocked_ops = [
            OperationMatchingResult(
                operation_id=op.operation_id,
                operation_number=op.operation_number,
                operation_name=op.operation_name,
                operation_type=op.operation_type,
                required_capability_codes=list(op.required_capability_codes),
                input_materials=[m.raw_name for m in op.input_materials],
                output_materials=[m.raw_name for m in op.output_materials],
                input_states=[s.raw_name for s in op.input_states],
                output_states=[s.raw_name for s in op.output_states],
                match_status="blocked",
                diagnostics=["上游 ResourceMatchingInput 交接状态为 blocked，已停止资源匹配并返回诊断信息。"],
            )
            for op in context.operations
        ]
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        feedback_items = _build_structured_feedback(rmi, blocked_ops, [], False, None)
        return ResourceMatchingOutput(
            run_id=effective_run_id,
            source_requirement_version=rmi.source_model.run_id,
            source_content_digest=rmi.source_model.content_digest,
            source_handoff_status=rmi.handoff_validation.status,
            catalog_version=active_catalog.catalog_version,
            catalog_digest=active_catalog.catalog_digest,
            ontology_version=rmi.source_model.ontology_version,
            vocabulary_version=rmi.source_model.vocabulary_version,
            config=active_config,
            run_metadata=make_metadata("blocked", elapsed_ms),
            modeling_task=rmi.modeling_task,
            target_product_ids=list(rmi.target_product_ids),
            line_constraints=line_refs,
            process_graph=rmi.process_graph,
            relations=list(rmi.relations),
            operation_matches=blocked_ops,
            matching_status="blocked",
            summary=MatchingSummary(
                total_operations=len(blocked_ops),
                blocked_count=len(blocked_ops),
                total_duration_ms=elapsed_ms,
            ),
            diagnostics=diagnostics,
            feedback_items=feedback_items,
            tool_trace=trace.steps,
        )

    if rmi.handoff_validation.status == "ready_with_warnings":
        for issue in rmi.handoff_validation.issues:
            diagnostics.append(
                MatchingDiagnostic(
                    code=issue.code,
                    severity="warning",
                    operation_id=issue.operation_ids[0] if issue.operation_ids else None,
                    message=f"继承上游交接警告：{issue.message}",
                )
            )

    op_results: list[OperationMatchingResult] = []
    exec_mode = "deterministic"

    if active_config.method == "C":
        match_start = perf_counter()
        for op in context.operations:
            op_results.append(_match_operation_method_c(op, context, active_catalog, active_config))
        trace.record(
            "semantic_retrieval_and_constraints",
            "verify_asset_constraints",
            "success",
            (perf_counter() - match_start) * 1000,
            f"完成 {len(op_results)} 道工序的语义检索、三值硬约束校验与 Top-{active_config.top_k} 排序",
        )

        if active_config.llm_mode == "enhanced":
            exec_mode = "llm_enhanced"
            client = UnifiedLLMClient(effective_llm_cfg) if effective_llm_cfg else None
            if client is None or not client.is_configured:
                fallback_reason = (
                    "当前资源匹配智能体配置为离线 mock:// 模式，未启用在线 LLM。"
                    if (client and client.is_mock)
                    else "当前资源匹配智能体尚未配置可用 API Key，无法执行在线 LLM 协同消歧。"
                )
                if active_config.fallback_policy == "fail_fast":
                    raise MatchingExecutionError("unconfigured", fallback_reason)
                fallback_triggered = True
                exec_mode = "llm_enhanced_degraded"
                diagnostics.append(
                    MatchingDiagnostic(
                        code="llm_degraded_to_deterministic",
                        severity="warning",
                        message=f"{fallback_reason} 已按降级策略返回纯确定性语义+多约束匹配结果。",
                    )
                )
                trace.record(
                    "llm_disambiguation_and_strategy",
                    "llm_semantic_disambiguation",
                    "warning",
                    0.0,
                    f"已触发安全降级：{fallback_reason}",
                )
            else:
                op_results, extra_feedback, llm_model_used, fallback_triggered, fallback_reason = (
                    await _run_llm_collaborative_stage(
                        client,
                        budget,
                        context,
                        op_results,
                        active_catalog,
                        active_config,
                        trace,
                        custom_system_prompt=effective_llm_cfg.system_prompt if effective_llm_cfg else "",
                        custom_business_rules=effective_llm_cfg.business_rules if effective_llm_cfg else "",
                    )
                )
                llm_calls = budget.calls_used
                llm_retries = budget.retries_used
                if fallback_triggered:
                    exec_mode = "llm_enhanced_degraded"
                    diagnostics.append(
                        MatchingDiagnostic(
                            code="llm_degraded_to_deterministic",
                            severity="warning",
                            message=f"LLM 协同阶段异常并已降级为确定性结果：{fallback_reason}",
                        )
                    )
        else:
            trace.record(
                "llm_disambiguation_and_strategy",
                "llm_semantic_disambiguation",
                "skipped",
                0.0,
                "当前为纯确定性语义匹配模式（llm_mode=disabled），未发起 LLM 调用",
            )
    elif active_config.method == "A":
        exec_mode = "method_a_keyword"
        match_start = perf_counter()
        for op in context.operations:
            op_results.append(_match_operation_method_a(op, context, active_catalog, active_config))
        trace.record(
            "keyword_baseline_matching",
            "search_assets_by_capability",
            "success",
            (perf_counter() - match_start) * 1000,
            f"方法A关键词基线完成 {len(op_results)} 道工序匹配",
        )
    else:
        exec_mode = "method_b_direct_llm"
        match_start = perf_counter()
        op_results, _, llm_calls, llm_model_used, method_b_diags = await _run_method_b_llm(
            context, active_catalog, active_config, effective_llm_cfg
        )
        diagnostics.extend(method_b_diags)
        if llm_calls == 0:
            fallback_triggered = True
            fallback_reason = method_b_diags[0].message if method_b_diags else "方法B未配置在线 LLM"
        trace.record(
            "llm_direct_matching",
            "llm_semantic_disambiguation",
            "success" if llm_calls > 0 else "warning",
            (perf_counter() - match_start) * 1000,
            f"方法B直接LLM匹配完成（调用 {llm_calls} 次）",
        )

    matched_single = sum(r.match_status == "matched_single" for r in op_results)
    matched_partial = sum(r.match_status == "matched_partial_composable" for r in op_results)
    pending_only = sum(r.match_status == "pending_only" for r in op_results)
    unmatched = sum(r.match_status == "unmatched" for r in op_results)
    blocked = sum(r.match_status == "blocked" for r in op_results)

    coverages = []
    for r in op_results:
        if r.top_k_candidates:
            coverages.append(r.top_k_candidates[0].capability_coverage)
        elif r.partial_candidates_by_capability:
            covered_caps = sum(1 for g in r.partial_candidates_by_capability if g.candidates)
            coverages.append(covered_caps / max(1, len(r.required_capability_codes)))
        else:
            coverages.append(0.0)
    avg_cov = round(sum(coverages) / max(1, len(coverages)), 4)

    if blocked == len(op_results) and op_results:
        overall_status = "blocked"
    elif unmatched > 0 or pending_only > 0 or blocked > 0:
        overall_status = "partial"
    elif diagnostics or matched_partial > 0 or fallback_triggered:
        overall_status = "completed_with_warnings"
    else:
        overall_status = "completed"

    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    feedback_items = _build_structured_feedback(rmi, op_results, extra_feedback, fallback_triggered, fallback_reason)
    metadata = make_metadata(exec_mode, elapsed_ms)

    return ResourceMatchingOutput(
        run_id=effective_run_id,
        source_requirement_version=rmi.source_model.run_id,
        source_content_digest=rmi.source_model.content_digest,
        source_handoff_status=rmi.handoff_validation.status,
        catalog_version=active_catalog.catalog_version,
        catalog_digest=active_catalog.catalog_digest,
        ontology_version=rmi.source_model.ontology_version,
        vocabulary_version=rmi.source_model.vocabulary_version,
        config=active_config,
        run_metadata=metadata,
        modeling_task=rmi.modeling_task,
        target_product_ids=list(rmi.target_product_ids),
        line_constraints=line_refs,
        process_graph=rmi.process_graph,
        relations=list(rmi.relations),
        operation_matches=op_results,
        matching_status=overall_status,
        summary=MatchingSummary(
            total_operations=len(op_results),
            matched_single_count=matched_single,
            matched_partial_count=matched_partial,
            pending_only_count=pending_only,
            unmatched_count=unmatched,
            blocked_count=blocked,
            average_top1_capability_coverage=avg_cov,
            total_duration_ms=elapsed_ms,
            llm_calls=llm_calls,
            llm_retries=llm_retries,
            llm_model_used=llm_model_used,
            fallback_triggered=fallback_triggered,
            fallback_reason=fallback_reason,
        ),
        diagnostics=diagnostics,
        feedback_items=feedback_items,
        tool_trace=trace.steps,
    )
