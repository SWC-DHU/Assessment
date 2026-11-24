"""Stage 3: Deterministic 3-value (PASS / FAIL / UNKNOWN) multi-constraint verification engine."""
from __future__ import annotations

import math

from domain_semantics.catalog import load_vocabulary, normalize
from domain_semantics.schema import Constraint
from domain_semantics.units import canonical_unit, convert_unit
from model_assets.schema import ModelAsset, TechnicalParameter

from .retrieval import DereferencedMatchingContext, DereferencedOperation
from .schema import CheckVerdict, ConstraintCheckItem

# Consumable material types that may only belong to a specific capability in a multi-capability operation
CONSUMABLE_MATERIAL_TYPES = {"adhesive", "adhesive_tape", "steel_strap"}


def _asset_input_materials(asset: ModelAsset) -> set[str]:
    mc = asset.material_compatibility
    return set(mc.input_material_types or mc.supported_material_types)


def _asset_output_materials(asset: ModelAsset) -> set[str]:
    mc = asset.material_compatibility
    return set(mc.output_material_types or mc.supported_material_types)


def _asset_all_materials(asset: ModelAsset) -> set[str]:
    mc = asset.material_compatibility
    return set(mc.supported_material_types) | set(mc.input_material_types) | set(mc.output_material_types)


def _find_asset_parameter(
    asset: ModelAsset,
    parameter_code: str,
    capability_code: str | None = None,
) -> TechnicalParameter | None:
    candidates = [p for p in asset.parameters if p.parameter_code == parameter_code]
    if capability_code:
        scoped = [p for p in candidates if p.capability_code == capability_code]
        if scoped:
            return scoped[0]
    if candidates:
        return candidates[0]
    if parameter_code == "process_time" and asset.behavior and asset.behavior.cycle_time_s is not None:
        return TechnicalParameter(
            parameter_code="process_time",
            name="标称加工时间",
            value=asset.behavior.cycle_time_s,
            unit="s",
            basis=asset.behavior.cycle_time_basis,
        )
    return None


def _evaluate_numeric_constraint(
    constraint: Constraint,
    asset: ModelAsset,
    scope: str,
    capability_code: str | None,
    context: DereferencedMatchingContext,
) -> ConstraintCheckItem:
    is_hard = constraint.strength != "preferred"
    pcode = constraint.parameter_code or "unknown"
    req_unit = canonical_unit(constraint.unit)
    if constraint.comparator == "range":
        req_desc = f"{pcode} in [{constraint.lower}, {constraint.upper}] {req_unit or ''}".strip()
    else:
        req_desc = f"{pcode} {constraint.comparator} {constraint.value} {req_unit or ''}".strip()
    if constraint.basis:
        req_desc += f" / {constraint.basis}"

    if constraint.status != "verified":
        return ConstraintCheckItem(
            check_id=f"constraint:{constraint.id}",
            scope=scope,  # type: ignore[arg-type]
            constraint_id=constraint.id,
            parameter_code=pcode,
            is_hard=is_hard,
            verdict="UNKNOWN",
            required_summary=req_desc,
            asset_summary="来源约束尚未核验",
            rationale="需求侧约束状态非 verified，保留待确认。",
        )

    param = _find_asset_parameter(asset, pcode, capability_code)
    if param is None:
        return ConstraintCheckItem(
            check_id=f"constraint:{constraint.id}",
            scope=scope,  # type: ignore[arg-type]
            constraint_id=constraint.id,
            parameter_code=pcode,
            is_hard=is_hard,
            verdict="UNKNOWN",
            required_summary=req_desc,
            asset_summary=f"资产未声明参数 {pcode}",
            rationale=f"模型资产缺少 {pcode} 技术参数，无法确认是否满足要求，需人工补充确认。",
        )

    asset_unit = canonical_unit(param.unit)
    if param.lower is not None or param.upper is not None:
        raw_lo = param.lower if param.lower is not None else param.value
        raw_hi = param.upper if param.upper is not None else param.value
        asset_desc = f"[{raw_lo}, {raw_hi}] {asset_unit}"
    else:
        raw_lo = raw_hi = param.value
        asset_desc = f"{param.value} {asset_unit}"
    if param.basis:
        asset_desc += f" / {param.basis}"

    # Convert asset numbers to constraint unit
    lo = convert_unit(raw_lo, asset_unit, req_unit) if raw_lo is not None else None
    hi = convert_unit(raw_hi, asset_unit, req_unit) if raw_hi is not None else None
    if lo is None or hi is None:
        return ConstraintCheckItem(
            check_id=f"constraint:{constraint.id}",
            scope=scope,  # type: ignore[arg-type]
            constraint_id=constraint.id,
            parameter_code=pcode,
            is_hard=is_hard,
            verdict="UNKNOWN",
            required_summary=req_desc,
            asset_summary=asset_desc,
            rationale=f"单位 {asset_unit} 与需求单位 {req_unit} 量纲不兼容或不可换算。",
        )

    # Check measurement basis compatibility
    if constraint.basis and param.basis and normalize(constraint.basis) != normalize(param.basis):
        # Allow exact BOM conversion when constraint basis is 模组 and asset basis is 电芯/单体组件
        cell_qty = context.component_multipliers.get("prismatic_cell")
        if cell_qty and constraint.basis in {"模组", "电池模组"} and param.basis in {"电芯", "方形电芯", "单体组件"}:
            lo *= cell_qty
            hi *= cell_qty
            asset_desc += f" (按 {int(cell_qty)} {param.basis}/模组折算为 [{lo:.2f}, {hi:.2f}] {req_unit})"
        else:
            return ConstraintCheckItem(
                check_id=f"constraint:{constraint.id}",
                scope=scope,  # type: ignore[arg-type]
                constraint_id=constraint.id,
                parameter_code=pcode,
                is_hard=is_hard,
                verdict="UNKNOWN",
                required_summary=req_desc,
                asset_summary=asset_desc,
                rationale=f"计量基准不一致（需求基准：{constraint.basis}，资产基准：{param.basis}），无法直接比较。",
            )

    comp = constraint.comparator
    passed = False
    if comp == "<":
        passed = lo < float(constraint.value)
    elif comp == "<=":
        passed = lo <= float(constraint.value) + 1e-9
    elif comp == ">":
        passed = hi > float(constraint.value)
    elif comp == ">=":
        passed = hi >= float(constraint.value) - 1e-9
    elif comp == "=":
        target = float(constraint.value)
        passed = lo - 1e-9 <= target <= hi + 1e-9
    elif comp == "range":
        c_lo = float(constraint.lower)
        c_hi = float(constraint.upper)
        passed = max(lo, c_lo) <= min(hi, c_hi) + 1e-9

    verdict: CheckVerdict = "PASS" if passed else "FAIL"
    rationale = (
        f"经单位统一换算为 {req_unit} 后，资产参数 {asset_desc} 满足要求 {req_desc}。"
        if passed
        else f"经单位统一换算为 {req_unit} 后，资产参数 {asset_desc} 违反约束 {req_desc}。"
    )
    return ConstraintCheckItem(
        check_id=f"constraint:{constraint.id}",
        scope=scope,  # type: ignore[arg-type]
        constraint_id=constraint.id,
        parameter_code=pcode,
        is_hard=is_hard,
        verdict=verdict,
        required_summary=req_desc,
        asset_summary=asset_desc,
        rationale=rationale,
    )


def _evaluate_qualitative_constraint(
    constraint: Constraint,
    asset: ModelAsset,
    scope: str,
) -> ConstraintCheckItem:
    codes = list(
        dict.fromkeys(
            ([constraint.parameter_code] if constraint.parameter_code else [])
            + list(constraint.related_parameter_codes)
        )
    )
    asset_features = {feat for cap in asset.capabilities for feat in cap.qualitative_features}
    matched = [code for code in codes if code in asset_features]
    req_text = constraint.description or constraint.name
    if not codes:
        return ConstraintCheckItem(
            check_id=f"constraint:{constraint.id}",
            scope=scope,  # type: ignore[arg-type]
            constraint_id=constraint.id,
            parameter_code=None,
            is_hard=False,
            verdict="UNKNOWN",
            required_summary=req_text,
            asset_summary="定性约束无映射编码，需人工或LLM辅助复核",
            rationale="定性要求未关联标准质量参数编码，作为软约束提示。",
        )
    if len(matched) == len(codes):
        verdict: CheckVerdict = "PASS"
        rationale = f"资产能力声明覆盖全部定性质量维度：{', '.join(matched)}。"
    elif matched:
        verdict = "UNKNOWN"
        missing = [c for c in codes if c not in asset_features]
        rationale = f"资产覆盖部分定性维度（{', '.join(matched)}），待确认：{', '.join(missing)}。"
    else:
        verdict = "UNKNOWN"
        rationale = f"资产未显式声明定性维度 {', '.join(codes)}，需人工或LLM进一步核验。"
    return ConstraintCheckItem(
        check_id=f"constraint:{constraint.id}",
        scope=scope,  # type: ignore[arg-type]
        constraint_id=constraint.id,
        parameter_code=codes[0] if len(codes) == 1 else None,
        is_hard=False,
        verdict=verdict,
        required_summary=req_text,
        asset_summary=f"声明定性特性：{', '.join(sorted(asset_features)) or '无'}",
        rationale=rationale,
    )


def _check_material_compatibility(
    op: DereferencedOperation,
    asset: ModelAsset,
    *,
    partial_capability: str | None = None,
) -> ConstraintCheckItem:
    req_in = [m for m in op.input_materials]
    req_out = [m for m in op.output_materials]
    req_all = req_in + req_out
    if not req_all:
        return ConstraintCheckItem(
            check_id="material_compatibility",
            scope="material",
            is_hard=True,
            verdict="PASS",
            required_summary="工序未限定输入/输出物料",
            asset_summary=", ".join(asset.material_compatibility.supported_material_types) or "未声明",
            rationale="工序无显式物料要求。",
        )

    asset_all = _asset_all_materials(asset)
    asset_in = _asset_input_materials(asset)
    asset_out = _asset_output_materials(asset)
    if not asset_all:
        return ConstraintCheckItem(
            check_id="material_compatibility",
            scope="material",
            is_hard=True,
            verdict="UNKNOWN",
            required_summary=f"输入: {', '.join(m.raw_name for m in req_in) or '无'}；输出: {', '.join(m.raw_name for m in req_out) or '无'}",
            asset_summary="资产未声明适配物料类型",
            rationale="模型资产缺少 material_compatibility.supported_material_types，无法验证物料适配性。",
        )

    in_codes = {m.canonical_type for m in req_in if m.canonical_type}
    out_codes = {m.canonical_type for m in req_out if m.canonical_type}
    unmapped = [m.raw_name for m in req_all if not m.canonical_type]
    req_summary = (
        f"输入: {', '.join(m.canonical_type or m.raw_name for m in req_in) or '无'} → "
        f"输出: {', '.join(m.canonical_type or m.raw_name for m in req_out) or '无'}"
    )
    asset_summary = f"支持输入: {', '.join(sorted(asset_in))}；支持输出: {', '.join(sorted(asset_out))}"

    if partial_capability is not None:
        # For a partial-capability asset, it must handle at least one primary workpiece material of the op
        # (not an incompatible family like pouch_cell / metal_housing).
        primary_req = (in_codes | out_codes) - CONSUMABLE_MATERIAL_TYPES
        target_pool = primary_req or (in_codes | out_codes)
        if not target_pool and unmapped:
            return ConstraintCheckItem(
                check_id="material_compatibility",
                scope="material",
                is_hard=True,
                verdict="UNKNOWN",
                required_summary=req_summary,
                asset_summary=asset_summary,
                rationale=f"工序物料未映射至受控编码：{', '.join(unmapped)}。",
            )
        overlap = target_pool & asset_all
        if overlap:
            return ConstraintCheckItem(
                check_id="material_compatibility",
                scope="material",
                is_hard=True,
                verdict="PASS",
                required_summary=req_summary,
                asset_summary=asset_summary,
                rationale=f"作为 {partial_capability} 部分能力候选，适配工序核心物料：{', '.join(sorted(overlap))}。",
            )
        return ConstraintCheckItem(
            check_id="material_compatibility",
            scope="material",
            is_hard=True,
            verdict="FAIL",
            required_summary=req_summary,
            asset_summary=asset_summary,
            rationale=f"资产适配物料（{', '.join(sorted(asset_all))}）与工序物料（{', '.join(sorted(target_pool))}）不匹配。",
        )

    # Full single-asset check: all mapped input and output materials must be compatible
    missing_in = sorted(in_codes - asset_in)
    missing_out = sorted(out_codes - asset_out)
    if missing_in or missing_out:
        reasons = []
        if missing_in:
            reasons.append(f"不适配输入物料 {', '.join(missing_in)}")
        if missing_out:
            reasons.append(f"不适配输出物料 {', '.join(missing_out)}")
        return ConstraintCheckItem(
            check_id="material_compatibility",
            scope="material",
            is_hard=True,
            verdict="FAIL",
            required_summary=req_summary,
            asset_summary=asset_summary,
            rationale="；".join(reasons) + "。",
        )
    if unmapped:
        return ConstraintCheckItem(
            check_id="material_compatibility",
            scope="material",
            is_hard=True,
            verdict="UNKNOWN",
            required_summary=req_summary,
            asset_summary=asset_summary,
            rationale=f"已映射物料匹配，但存在未归入受控词表的物料：{', '.join(unmapped)}，需确认。",
        )
    return ConstraintCheckItem(
        check_id="material_compatibility",
        scope="material",
        is_hard=True,
        verdict="PASS",
        required_summary=req_summary,
        asset_summary=asset_summary,
        rationale="全部输入与输出标准物料类型均在模型资产适配范围内。",
    )


def _check_state_transitions(
    op: DereferencedOperation,
    asset: ModelAsset,
    *,
    partial_capability: str | None = None,
) -> ConstraintCheckItem:
    in_states = [s for s in op.input_states if s.canonical_state]
    out_states = [s for s in op.output_states if s.canonical_state]
    raw_in = [s.raw_name for s in op.input_states]
    raw_out = [s.raw_name for s in op.output_states]
    if not op.input_states and not op.output_states:
        return ConstraintCheckItem(
            check_id="state_transition",
            scope="state",
            is_hard=True,
            verdict="PASS",
            required_summary="工序未限定显式物料状态转换",
            asset_summary="无需校验",
            rationale="工序未声明显式输入/输出物料状态要求。",
        )

    mc = asset.material_compatibility
    req_summary = f"输入状态: {', '.join(raw_in) or '不限'} → 输出状态: {', '.join(raw_out) or '不限'}"
    has_asset_states = bool(mc.input_states or mc.output_states or mc.state_transitions)
    if not has_asset_states:
        return ConstraintCheckItem(
            check_id="state_transition",
            scope="state",
            is_hard=True,
            verdict="UNKNOWN",
            required_summary=req_summary,
            asset_summary="资产未声明输入/输出物料状态及状态转换",
            rationale="模型资产缺少物料状态与状态转换定义，需人工确认是否支持该工艺状态转换。",
        )

    asset_in_set = set(mc.input_states) | {t.from_state for t in mc.state_transitions}
    asset_out_set = set(mc.output_states) | {t.to_state for t in mc.state_transitions}
    transitions = {(t.from_state, t.to_state) for t in mc.state_transitions}
    asset_summary = (
        f"输入状态: {', '.join(sorted(asset_in_set)) or '无'}；"
        f"输出状态: {', '.join(sorted(asset_out_set)) or '无'}"
    )

    if partial_capability is not None:
        # Partial assets handle an intermediate step in a multi-capability op;
        # pass if either input state or output state aligns, or asset supports a related intermediate transition.
        in_codes = {s.canonical_state for s in in_states if s.canonical_state}
        out_codes = {s.canonical_state for s in out_states if s.canonical_state}
        if (in_codes & asset_in_set) or (out_codes & asset_out_set):
            return ConstraintCheckItem(
                check_id="state_transition",
                scope="state",
                is_hard=True,
                verdict="PASS",
                required_summary=req_summary,
                asset_summary=asset_summary,
                rationale="部分能力资产支持该工序的起始或目标物料状态。",
            )
        # Check whether an intermediate state bridge is plausible (not contradictory NG/unqualified)
        if "unqualified" in asset_in_set and "unqualified" not in in_codes:
            return ConstraintCheckItem(
                check_id="state_transition",
                scope="state",
                is_hard=True,
                verdict="FAIL",
                required_summary=req_summary,
                asset_summary=asset_summary,
                rationale="资产仅接收不合格品状态，与工序正常输入状态冲突。",
            )
        return ConstraintCheckItem(
            check_id="state_transition",
            scope="state",
            is_hard=True,
            verdict="PASS",
            required_summary=req_summary,
            asset_summary=asset_summary,
            rationale="部分能力资产作为多设备协同中间环节，状态衔接留待下游关联建模确认。",
        )

    # Full single-asset check
    missing_in = [s.canonical_state for s in in_states if s.canonical_state not in asset_in_set]
    missing_out = [s.canonical_state for s in out_states if s.canonical_state not in asset_out_set]
    if missing_in or missing_out:
        reasons = []
        if missing_in:
            reasons.append(f"不支持输入状态 {', '.join(str(x) for x in missing_in)}")
        if missing_out:
            reasons.append(f"不支持输出状态 {', '.join(str(x) for x in missing_out)}")
        return ConstraintCheckItem(
            check_id="state_transition",
            scope="state",
            is_hard=True,
            verdict="FAIL",
            required_summary=req_summary,
            asset_summary=asset_summary,
            rationale="；".join(reasons) + "。",
        )

    # If both input and output states exist on the same material and asset defines transitions, verify pair
    if in_states and out_states and transitions:
        pair_found = any(
            (ins.canonical_state, outs.canonical_state) in transitions
            for ins in in_states
            for outs in out_states
        )
        if not pair_found:
            return ConstraintCheckItem(
                check_id="state_transition",
                scope="state",
                is_hard=True,
                verdict="FAIL",
                required_summary=req_summary,
                asset_summary=asset_summary,
                rationale="资产声明的状态转换规则未覆盖工序所需的输入→输出状态转换。",
            )

    if any(s.canonical_state is None for s in op.input_states + op.output_states):
        return ConstraintCheckItem(
            check_id="state_transition",
            scope="state",
            is_hard=True,
            verdict="UNKNOWN",
            required_summary=req_summary,
            asset_summary=asset_summary,
            rationale="存在未映射至受控词表的物料状态术语，需人工确认。",
        )

    return ConstraintCheckItem(
        check_id="state_transition",
        scope="state",
        is_hard=True,
        verdict="PASS",
        required_summary=req_summary,
        asset_summary=asset_summary,
        rationale="模型资产支持工序声明的物料输入/输出状态及状态转换。",
    )


def _check_information_interfaces(
    op: DereferencedOperation,
    asset: ModelAsset,
    *,
    partial_capability: str | None = None,
) -> ConstraintCheckItem | None:
    verified_flows = [flow for flow in op.information_flows if flow.status == "verified"]
    if not verified_flows:
        return None
    # For partial capability assets, only check information interface on info-producing capabilities
    if partial_capability is not None and partial_capability not in {"CODE_READ", "CODE_MARK", "INSPECT"}:
        return None

    req_desc = ", ".join(f"{f.direction}:{f.name}" for f in verified_flows)
    if not asset.interfaces:
        return ConstraintCheckItem(
            check_id="information_interface",
            scope="interface",
            is_hard=True,
            verdict="UNKNOWN",
            required_summary=req_desc,
            asset_summary="资产未声明接口定义",
            rationale="模型资产缺少 interfaces 定义，无法核验是否支持所需信息流输出。",
        )

    info_ifaces = [iface for iface in asset.interfaces if iface.interface_type == "information"]
    if not info_ifaces:
        return ConstraintCheckItem(
            check_id="information_interface",
            scope="interface",
            is_hard=True,
            verdict="FAIL",
            required_summary=req_desc,
            asset_summary="资产不含信息流接口 (information)",
            rationale="工序要求输出/交换信息流，但模型资产未配置信息流接口。",
        )

    missing_flows = []
    for flow in verified_flows:
        ok = False
        flow_norm = normalize(flow.name)
        for iface in info_ifaces:
            if iface.direction not in {flow.direction, "bidirectional"}:
                continue
            conds = [normalize(c) for c in iface.compatible_conditions]
            if not conds or any(flow_norm in c or c in flow_norm or "追溯" in c for c in conds):
                ok = True
                break
        if not ok:
            missing_flows.append(flow.name)

    iface_desc = "; ".join(
        f"{i.interface_id}({i.direction}: {', '.join(i.compatible_conditions) or '通用'})"
        for i in info_ifaces
    )
    if missing_flows:
        return ConstraintCheckItem(
            check_id="information_interface",
            scope="interface",
            is_hard=True,
            verdict="FAIL",
            required_summary=req_desc,
            asset_summary=iface_desc,
            rationale=f"信息接口未兼容以下信息流：{', '.join(missing_flows)}。",
        )
    return ConstraintCheckItem(
        check_id="information_interface",
        scope="interface",
        is_hard=True,
        verdict="PASS",
        required_summary=req_desc,
        asset_summary=iface_desc,
        rationale="资产信息流接口方向与兼容载荷满足工序信息流要求。",
    )


def _check_equipment_requirement(op: DereferencedOperation, asset: ModelAsset) -> ConstraintCheckItem | None:
    if not op.equipment_requirement:
        return None
    req_norm = normalize(op.equipment_requirement)
    vocab = load_vocabulary().get("asset_categories", {})
    cat_entry = vocab.get(asset.identity.category, {})
    cat_terms = [asset.identity.category, cat_entry.get("label", ""), *cat_entry.get("aliases", [])]
    asset_text = normalize(
        " ".join([
            asset.identity.name,
            asset.identity.subcategory or "",
            asset.identity.description,
            " ".join(asset.identity.tags),
            " ".join(cat_terms),
        ])
    )
    # Check if explicit equipment requirement matches category or asset description keywords
    matched_cat = any(normalize(term) in req_norm for term in cat_terms if term)
    direct_overlap = req_norm in asset_text or any(
        tok in asset_text for tok in req_norm.split() if len(tok) >= 2
    )
    if matched_cat or direct_overlap:
        return ConstraintCheckItem(
            check_id="equipment_requirement",
            scope="equipment",
            is_hard=False,
            verdict="PASS",
            required_summary=op.equipment_requirement,
            asset_summary=f"{asset.identity.category} · {asset.identity.name}",
            rationale="模型资产类别及功能描述与工序显式设备要求相符。",
        )
    return ConstraintCheckItem(
        check_id="equipment_requirement",
        scope="equipment",
        is_hard=False,
        verdict="UNKNOWN",
        required_summary=op.equipment_requirement,
        asset_summary=f"{asset.identity.category} · {asset.identity.name}",
        rationale="设备要求文本与资产类别未直接命中，建议由LLM或人工结合工艺说明进一步核对。",
    )


def build_line_takt_reference(
    op: DereferencedOperation,
    asset: ModelAsset,
    context: DereferencedMatchingContext,
) -> str | None:
    """Compute an informational (non-blocking) reference comparing asset cycle time to line takt."""
    takt_constraints = [
        c for c in context.line_constraints
        if c.parameter_code == "takt_time" and c.status == "verified" and c.value is not None
    ]
    if not takt_constraints:
        return None
    takt = takt_constraints[0]
    takt_s = convert_unit(float(takt.value), takt.unit, "s")
    if takt_s is None or takt_s <= 0:
        return None

    param = _find_asset_parameter(asset, "process_time")
    if param is None or param.value is None:
        return f"整线目标节拍 {takt.comparator or '<='} {takt.value}{takt.unit}/{takt.basis or '件'}；当前资产缺少工时数据，待下游产线平衡核验。"
    ct_s = convert_unit(float(param.value), param.unit, "s")
    if ct_s is None:
        return None

    basis = param.basis or (asset.behavior.cycle_time_basis if asset.behavior else None)
    batch = (asset.behavior.batch_size if asset.behavior and asset.behavior.batch_size else 1)
    effective_module_s = ct_s
    detail = f"单机标称工时 {ct_s:.1f}s"
    if takt.basis in {"模组", "电池模组"} and basis in {"电芯", "方形电芯", "单体组件"}:
        qty = context.component_multipliers.get("prismatic_cell", 8.0)
        effective_module_s = ct_s * (qty / max(1, batch))
        detail = f"单次 {ct_s:.1f}s/{basis}（批处理 {batch}，按 {int(qty)}/模组折算单机有效耗时约 {effective_module_s:.1f}s/模组）"
    elif basis:
        detail = f"单机标称工时 {ct_s:.1f}s/{basis}"

    if effective_module_s <= takt_s:
        return f"整线节拍参考（非单机硬限制）：{detail} ≤ 整线目标 {takt_s:.0f}s/{takt.basis or '模组'}，单台实例化即可满足节拍。"
    parallel = math.ceil(effective_module_s / takt_s)
    return (
        f"整线节拍参考（非单机硬限制）：{detail} > 整线目标 {takt_s:.0f}s/{takt.basis or '模组'}，"
        f"后续关联建模阶段可考虑配置 {parallel} 台并行实例（{asset.asset_id}#inst-1..{parallel}）。"
    )


def evaluate_asset_constraints(
    op: DereferencedOperation,
    asset: ModelAsset,
    context: DereferencedMatchingContext,
    *,
    partial_capability: str | None = None,
) -> tuple[CheckVerdict, list[ConstraintCheckItem], list[str], list[str], list[str]]:
    """Run all deterministic 3-value checks for one (operation, asset) pair.

    Returns:
      (hard_verdict, checks, match_reasons, rejection_reasons, missing_information)
    """
    checks: list[ConstraintCheckItem] = []

    # 1. Capability coverage check
    req_caps = [partial_capability] if partial_capability is not None else list(op.required_capability_codes)
    provided = set(asset.capability_codes)
    covered = [c for c in req_caps if c in provided]
    missing_caps = [c for c in req_caps if c not in provided]
    cap_verdict: CheckVerdict = "PASS" if not missing_caps else "FAIL"
    checks.append(
        ConstraintCheckItem(
            check_id="capability_coverage",
            scope="capability",
            is_hard=True,
            verdict=cap_verdict,
            required_summary=", ".join(req_caps) or "无",
            asset_summary=", ".join(asset.capability_codes),
            rationale=(
                f"资产完整覆盖所需能力编码：{', '.join(covered)}。"
                if cap_verdict == "PASS"
                else f"资产缺少必需能力编码：{', '.join(missing_caps)}（仅覆盖 {', '.join(covered) or '无'}）。"
            ),
        )
    )

    # 2. Material compatibility check
    checks.append(_check_material_compatibility(op, asset, partial_capability=partial_capability))

    # 3. Material state & state transition check
    checks.append(_check_state_transitions(op, asset, partial_capability=partial_capability))

    # 4. Essential parameter / behavior completeness check (missing core specs -> UNKNOWN / pending confirmation)
    has_cycle = bool(asset.behavior and asset.behavior.cycle_time_s is not None)
    if not asset.parameters and not has_cycle:
        checks.append(
            ConstraintCheckItem(
                check_id="essential_parameters",
                scope="operation",
                is_hard=True,
                verdict="UNKNOWN",
                required_summary="基础技术参数与加工时间信息",
                asset_summary="资产未声明任何技术参数及标称加工时间",
                rationale="模型资产缺少基础技术参数和加工时间定义，无法确认工程适用性，需人工补全确认。",
            )
        )

    # 5. Operation-level constraints (quantitative & qualitative; NEVER line-level takt!)
    for constraint in op.operation_constraints:
        if constraint.comparator is not None and constraint.parameter_code is not None:
            checks.append(_evaluate_numeric_constraint(constraint, asset, "operation", None, context))
        else:
            checks.append(_evaluate_qualitative_constraint(constraint, asset, "operation"))

    # 5. Capability-level constraints
    for cap_code, constraint in op.capability_constraints:
        if partial_capability is not None and cap_code != partial_capability:
            continue
        if constraint.comparator is not None and constraint.parameter_code is not None:
            checks.append(_evaluate_numeric_constraint(constraint, asset, "capability", cap_code or None, context))
        else:
            checks.append(_evaluate_qualitative_constraint(constraint, asset, "capability"))

    # 6. Information flow & interface check
    info_check = _check_information_interfaces(op, asset, partial_capability=partial_capability)
    if info_check is not None:
        checks.append(info_check)

    # 7. Explicit equipment requirement check
    equip_check = _check_equipment_requirement(op, asset)
    if equip_check is not None:
        checks.append(equip_check)

    # Aggregate 3-value hard constraint verdict: FAIL > UNKNOWN > PASS
    hard_checks = [item for item in checks if item.is_hard]
    if any(item.verdict == "FAIL" for item in hard_checks):
        hard_verdict: CheckVerdict = "FAIL"
    elif any(item.verdict == "UNKNOWN" for item in hard_checks):
        hard_verdict = "UNKNOWN"
    else:
        hard_verdict = "PASS"

    match_reasons = [item.rationale for item in checks if item.verdict == "PASS"]
    rejection_reasons = [item.rationale for item in hard_checks if item.verdict == "FAIL"]
    missing_info = [item.rationale for item in checks if item.verdict == "UNKNOWN"]
    return hard_verdict, checks, match_reasons, rejection_reasons, missing_info
