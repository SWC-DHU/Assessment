"""Resource Matching Agent: LLM + deterministic tool collaboration & A/B/C experiment methods."""
from __future__ import annotations

from dataclasses import replace
import json
from time import perf_counter
import uuid

from app.services.llm_extractor import ExtractorConfig, run_extraction
from domain_semantics.catalog import load_vocabulary, semantic_mappings
from domain_semantics.handoff import ResourceMatchingInput
from model_assets.catalog import AssetCatalog, get_default_catalog
from model_assets.schema import ModelAsset
from model_assets.validation import compute_completeness

from .constraints import build_line_takt_reference, evaluate_asset_constraints
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
    UnmetRequirement,
)
from .tools import MatchingTrace, tool_evaluate_candidate

LLM_EXPLAIN_SYSTEM_PROMPT = """你是一位数字孪生制造产线资源匹配专家。
后端确定性引擎已经完成了标准制造能力编码检索、单位换算、三值硬约束校验（PASS/FAIL/UNKNOWN）和综合评分排序。
你的任务仅限于：
1. 针对给定工序的复杂定性工艺要求或设备描述，给出简明、专业的工程匹配解释（1~2 句话）；
2. 不得推翻硬约束 FAIL 的淘汰结论，也不得编造输入中不存在的资产 ID 或数值。
请严格输出 JSON 对象：{"explanations": {"<operation_id>": "<简洁工程匹配说明>"}}。"""

LLM_DIRECT_METHOD_B_PROMPT = """你是一位数字孪生制造产线设备资源匹配助手。
请根据给定的工序需求和候选模型资产摘要，直接为每道工序推荐最合适的 Top-k 模型资产 ID，并给出简要理由。
只能从提供的模型资产 ID 中选择，不得编造不存在的 ID。
严格输出 JSON 对象：
{
  "matches": {
    "<operation_id>": [
      {"asset_id": "<资产ID>", "reason": "<简要理由>", "score": 0.85}
    ]
  }
}"""


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

    # 1. Evaluate full-coverage single-asset candidates
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

    # Also evaluate partial_pool against full-op requirement if we want clear diagnostics on capability-insufficient distractors
    if config.include_rejected_details:
        for asset in partial_pool:
            ev = tool_evaluate_candidate(op, asset, all_retrieved, context, config)
            if ev.disposition == "rejected":
                rejected_all.append(ev)

    top_k = rank_evaluations(full_qualified, limit=config.top_k)
    pending_ranked = rank_evaluations(pending_full)
    rejected_ranked = rank_evaluations(rejected_all) if config.include_rejected_details else []

    # 2. Per-capability partial candidates (always built for multi-capability ops, or when single-asset unmatched)
    partial_groups: list[CapabilityPartialCandidates] = []
    should_build_partial = len(op.required_capability_codes) > 1 or not top_k
    if should_build_partial:
        for cap_code in op.required_capability_codes:
            cap_assets = [a for a in all_retrieved if cap_code in a.capability_codes]
            # Prioritize dedicated/partial assets alongside full assets that cover this capability
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

    # 3. Determine operation match_status, multi_asset_recommended, and unmet_requirements
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
    """Method A baseline: Keyword-only matching (no ontology capability enforcement, no 3-value constraint filter)."""
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
        # Compute real constraint checks for transparent experiment auditing,
        # but Method A does NOT filter by hard constraints (it ranks purely by keyword score)!
        # To respect CandidateEvaluation's invariant while allowing Method A experiment scoring,
        # we record the actual constraint checks and keep disposition="qualified" ONLY when we mark
        # an audit field — wait: CandidateEvaluation enforces qualified => PASS!
        # So for Method A/B baselines, we store baseline picks in top_k_candidates when PASS, OR
        # let's evaluate and record real checks cleanly: wait — Method A/B can pick assets that violate
        # hard constraints, which is literally how "硬约束违反率" (hard constraint violation rate) is measured!
        # Let's handle that cleanly by computing real checks on the side or allowing raw baseline picks
        # in the experiment evaluator, while in ResourceMatchingOutput keeping the invariant intact OR
        # providing a dedicated `run_baseline_picks` helper for Method A/B!
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


async def _run_llm_explanations(
    llm_config: ExtractorConfig,
    results: list[OperationMatchingResult],
) -> tuple[dict[str, str], int, str | None]:
    payload = [
        {
            "operation_id": r.operation_id,
            "operation_name": r.operation_name,
            "required_capabilities": r.required_capability_codes,
            "match_status": r.match_status,
            "top_candidates": [
                {
                    "asset_id": c.asset_id,
                    "asset_name": c.asset_name,
                    "score": c.score_breakdown.total_score,
                    "reasons": c.match_reasons[:3],
                }
                for c in r.top_k_candidates[:3]
            ],
            "pending_count": len(r.pending_candidates),
            "rejected_count": len(r.rejected_candidates),
        }
        for r in results
    ]
    configured = replace(
        llm_config,
        domain_instruction=LLM_EXPLAIN_SYSTEM_PROMPT,
        extraction_schema={},
        require_source_quote=False,
        request_budget=1,
    )
    res = await run_extraction(configured, json.dumps(payload, ensure_ascii=False), [])
    calls = int(res.debug_info.get("request_count") or 0)
    model_used = res.debug_info.get("model_used")
    if res.status == "success" and isinstance(res.extracted_json, dict):
        raw_map = res.extracted_json.get("explanations")
        if isinstance(raw_map, dict):
            return {str(k): str(v) for k, v in raw_map.items() if isinstance(v, str)}, calls, model_used
    return {}, calls, model_used


async def _run_method_b_llm(
    context: DereferencedMatchingContext,
    catalog: AssetCatalog,
    config: ResourceMatchingConfig,
    llm_config: ExtractorConfig | None,
) -> tuple[list[OperationMatchingResult], dict[str, list[dict]], int, str | None, list[MatchingDiagnostic]]:
    """Method B baseline: direct LLM matching without ontology/constraint engine."""
    diagnostics: list[MatchingDiagnostic] = []
    raw_picks: dict[str, list[dict]] = {}
    if llm_config is None or not llm_config.api_key.strip() or llm_config.base_url.strip().startswith("mock://"):
        diagnostics.append(
            MatchingDiagnostic(
                code="method_b_llm_unavailable",
                severity="warning",
                message="方法B（直接LLM匹配）需要已配置的在线 LLM Provider；当前为离线/未配置状态，返回空匹配结果。",
            )
        )
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
    assets_summary = [
        {
            "asset_id": a.asset_id,
            "name": a.identity.name,
            "category": a.identity.category,
            "capabilities": a.capability_codes,
            "materials": a.material_compatibility.supported_material_types,
            "description": a.identity.description[:80],
        }
        for a in catalog.all_assets()
    ]
    prompt_body = json.dumps(
        {"top_k": config.top_k, "operations": ops_summary, "assets": assets_summary},
        ensure_ascii=False,
    )
    configured = replace(
        llm_config,
        domain_instruction=LLM_DIRECT_METHOD_B_PROMPT,
        extraction_schema={},
        require_source_quote=False,
        request_budget=1,
    )
    res = await run_extraction(configured, prompt_body, [])
    calls = int(res.debug_info.get("request_count") or 0)
    model_used = res.debug_info.get("model_used")
    matches_map = (res.extracted_json or {}).get("matches", {}) if isinstance(res.extracted_json, dict) else {}
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
    return results, raw_picks, calls, model_used, diagnostics


async def run_resource_matching(
    rmi: ResourceMatchingInput,
    *,
    catalog: AssetCatalog | None = None,
    config: ResourceMatchingConfig | None = None,
    llm_config: ExtractorConfig | None = None,
    run_id: str | None = None,
) -> ResourceMatchingOutput:
    """Execute the complete Resource Matching Agent pipeline on a ResourceMatchingInput."""
    started = perf_counter()
    active_catalog = catalog or get_default_catalog()
    active_config = config or ResourceMatchingConfig()
    effective_run_id = run_id or str(uuid.uuid4())
    trace = MatchingTrace()
    diagnostics: list[MatchingDiagnostic] = []
    llm_calls = 0
    llm_model_used: str | None = None

    # Step 1: Dereference and check handoff status
    context = trace.call(
        "requirement_dereferencing",
        "dereference_requirements",
        dereference_input,
        rmi,
        summary_fn=lambda ctx: f"解引用 {len(ctx.operations)} 道工序与 {len(ctx.line_constraints)} 条整线约束（交接状态：{ctx.handoff_status}）",
    )

    # Step 2: Query ontology demand↔resource mappings
    mappings = trace.call(
        "ontology_mapping_lookup",
        "query_ontology_mappings",
        semantic_mappings,
        summary_fn=lambda items: f"加载 {len(items)} 条共享本体需求↔资源语义映射规则",
    )
    _ = mappings

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

    # Execute selected method (C / A / B)
    op_results: list[OperationMatchingResult] = []
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
        # Optional LLM explanation/disambiguation step
        if active_config.use_llm and llm_config and llm_config.api_key.strip() and not llm_config.base_url.startswith("mock://"):
            llm_start = perf_counter()
            explanations, llm_calls, llm_model_used = await _run_llm_explanations(llm_config, op_results)
            for item in op_results:
                if item.operation_id in explanations:
                    item.llm_explanation = explanations[item.operation_id]
            trace.record(
                "llm_disambiguation_and_explanation",
                "llm_semantic_disambiguation",
                "success" if explanations else "warning",
                (perf_counter() - llm_start) * 1000,
                f"LLM 生成 {len(explanations)} 道工序的工程匹配解释（调用 {llm_calls} 次）",
            )
        else:
            trace.record(
                "llm_disambiguation_and_explanation",
                "llm_semantic_disambiguation",
                "skipped",
                0.0,
                "当前使用纯确定性语义引擎运行（未启用或未配置在线 LLM）",
            )
    elif active_config.method == "A":
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
        match_start = perf_counter()
        op_results, _, llm_calls, llm_model_used, method_b_diags = await _run_method_b_llm(
            context, active_catalog, active_config, llm_config
        )
        diagnostics.extend(method_b_diags)
        trace.record(
            "llm_direct_matching",
            "llm_semantic_disambiguation",
            "success" if llm_calls > 0 else "warning",
            (perf_counter() - match_start) * 1000,
            f"方法B直接LLM匹配完成（调用 {llm_calls} 次）",
        )

    # Summarize results
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
    elif diagnostics or matched_partial > 0:
        overall_status = "completed_with_warnings"
    else:
        overall_status = "completed"

    elapsed_ms = round((perf_counter() - started) * 1000, 2)
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
            llm_model_used=llm_model_used,
        ),
        diagnostics=diagnostics,
        tool_trace=trace.steps,
    )
