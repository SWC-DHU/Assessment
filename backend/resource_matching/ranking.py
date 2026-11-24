"""Stage 4 & 5: Configurable multi-criteria scoring and strict hard-constraint-preserving ranking."""
from __future__ import annotations

from domain_semantics.units import convert_unit
from model_assets.schema import ModelAsset
from model_assets.validation import compute_completeness

from .constraints import _asset_all_materials, _find_asset_parameter
from .retrieval import DereferencedOperation
from .schema import CandidateEvaluation, ConstraintCheckItem, ScoreBreakdown, ScoringWeights


def _functional_score(
    op: DereferencedOperation,
    asset: ModelAsset,
    checks: list[ConstraintCheckItem],
    *,
    partial_capability: str | None = None,
) -> float:
    req = {partial_capability} if partial_capability else set(op.required_capability_codes)
    provided = set(asset.capability_codes)
    coverage = len(req & provided) / max(1, len(req))

    # Primary capability bonus
    primary_codes = {c.code for c in asset.capabilities if c.is_primary}
    primary_ratio = len(req & primary_codes) / max(1, len(req))

    # Specificity: slight preference for focused assets over Swiss-army-knife over-matching
    extra_caps = len(provided - req)
    specificity = 1.0 / (1.0 + 0.12 * extra_caps)

    # Qualitative alignment
    qual_checks = [c for c in checks if not c.is_hard and c.scope in {"operation", "capability"}]
    if qual_checks:
        qual_ratio = sum(1.0 if c.verdict == "PASS" else 0.5 if c.verdict == "UNKNOWN" else 0.0 for c in qual_checks) / len(qual_checks)
    else:
        qual_ratio = 0.8

    # Equipment requirement fit
    equip_checks = [c for c in checks if c.scope == "equipment"]
    equip_score = 1.0 if not equip_checks else (1.0 if equip_checks[0].verdict == "PASS" else 0.5)

    score = 0.45 * coverage + 0.15 * primary_ratio + 0.10 * specificity + 0.22 * qual_ratio + 0.08 * equip_score
    return round(min(1.0, max(0.0, score)), 4)


def _material_score(
    op: DereferencedOperation,
    asset: ModelAsset,
    checks: list[ConstraintCheckItem],
) -> float:
    mat_check = next((c for c in checks if c.scope == "material"), None)
    state_check = next((c for c in checks if c.scope == "state"), None)
    iface_check = next((c for c in checks if c.scope == "interface"), None)

    mat_val = 1.0 if (not mat_check or mat_check.verdict == "PASS") else 0.45 if mat_check.verdict == "UNKNOWN" else 0.0
    state_val = 1.0 if (not state_check or state_check.verdict == "PASS") else 0.45 if state_check.verdict == "UNKNOWN" else 0.0
    iface_val = 1.0 if (not iface_check or iface_check.verdict == "PASS") else 0.45 if iface_check.verdict == "UNKNOWN" else 0.0

    # Dedicated material specificity bonus (e.g., dedicated mid_plate feeder vs generic 3-material feeder)
    req_mats = {m.canonical_type for m in op.input_materials + op.output_materials if m.canonical_type}
    asset_mats = _asset_all_materials(asset)
    extra_mats = max(0, len(asset_mats - req_mats))
    specificity = 1.0 / (1.0 + 0.10 * extra_mats)

    score = 0.42 * mat_val + 0.30 * state_val + 0.16 * iface_val + 0.12 * specificity
    return round(min(1.0, max(0.0, score)), 4)


def _extract_cycle_time_s(asset: ModelAsset) -> float | None:
    param = _find_asset_parameter(asset, "process_time")
    if param is not None and param.value is not None:
        return convert_unit(float(param.value), param.unit, "s")
    if asset.behavior and asset.behavior.cycle_time_s is not None:
        return float(asset.behavior.cycle_time_s)
    return None


def _extract_precision_mm(asset: ModelAsset) -> float | None:
    for code in ("positioning_accuracy", "dispense_accuracy", "tolerance"):
        param = _find_asset_parameter(asset, code)
        if param is not None and param.value is not None:
            mm = convert_unit(float(param.value), param.unit, "mm")
            if mm is not None:
                return mm
    return None


def _performance_score(
    asset: ModelAsset,
    peers: list[ModelAsset],
    checks: list[ConstraintCheckItem],
) -> float:
    # 1. Numeric constraint pass ratio
    numeric_checks = [c for c in checks if c.constraint_id and c.is_hard]
    if numeric_checks:
        num_score = sum(1.0 if c.verdict == "PASS" else 0.4 if c.verdict == "UNKNOWN" else 0.0 for c in numeric_checks) / len(numeric_checks)
    else:
        num_score = 0.8

    # 2. Cycle time relative efficiency (lower per-unit effective time = higher score)
    ct = _extract_cycle_time_s(asset)
    peer_cts = [val for p in peers if (val := _extract_cycle_time_s(p)) is not None]
    if ct is not None and peer_cts:
        batch = asset.behavior.batch_size if (asset.behavior and asset.behavior.batch_size) else 1
        eff_ct = ct / max(1, batch)
        peer_eff = [
            val / max(1, (p.behavior.batch_size if p.behavior and p.behavior.batch_size else 1))
            for p in peers
            if (val := _extract_cycle_time_s(p)) is not None
        ]
        min_ct, max_ct = min(peer_eff), max(peer_eff)
        if max_ct - min_ct > 1e-6:
            ct_score = 0.35 + 0.65 * (max_ct - eff_ct) / (max_ct - min_ct)
        else:
            ct_score = 0.85
    elif ct is not None:
        ct_score = 0.8
    else:
        ct_score = 0.35

    # 3. Precision relative score (tighter mm = higher score; neutral default when precision is N/A)
    prec = _extract_precision_mm(asset)
    peer_precs = [val for p in peers if (val := _extract_precision_mm(p)) is not None]
    if prec is not None and peer_precs:
        min_p, max_p = min(peer_precs), max(peer_precs)
        if max_p - min_p > 1e-6:
            prec_score = 0.55 + 0.45 * (max_p - prec) / (max_p - min_p)
        else:
            prec_score = 0.80
    elif prec is not None:
        prec_score = 0.80
    else:
        prec_score = 0.72

    # 4. Availability
    avail = asset.behavior.availability if (asset.behavior and asset.behavior.availability is not None) else 0.90
    avail_score = max(0.0, min(1.0, (avail - 0.80) / 0.20))

    score = 0.25 * num_score + 0.40 * ct_score + 0.20 * prec_score + 0.15 * avail_score
    return round(min(1.0, max(0.0, score)), 4)


def compute_score_breakdown(
    op: DereferencedOperation,
    asset: ModelAsset,
    peers: list[ModelAsset],
    checks: list[ConstraintCheckItem],
    weights: ScoringWeights,
    *,
    partial_capability: str | None = None,
) -> ScoreBreakdown:
    w_func, w_mat, w_perf, w_comp = weights.normalized()
    f_score = _functional_score(op, asset, checks, partial_capability=partial_capability)
    m_score = _material_score(op, asset, checks)
    p_score = _performance_score(asset, peers, checks)
    c_score, _ = compute_completeness(asset)
    total = round(w_func * f_score + w_mat * m_score + w_perf * p_score + w_comp * c_score, 4)
    return ScoreBreakdown(
        functional_fit=f_score,
        material_fit=m_score,
        technical_performance=p_score,
        modeling_completeness=c_score,
        total_score=min(1.0, max(0.0, total)),
    )


def rank_evaluations(items: list[CandidateEvaluation], limit: int | None = None) -> list[CandidateEvaluation]:
    """Deterministic ranking by total_score desc, capability_coverage desc, asset_id asc."""
    ordered = sorted(
        items,
        key=lambda item: (-item.score_breakdown.total_score, -item.capability_coverage, item.asset_id),
    )
    if limit is not None:
        ordered = ordered[:limit]
    return [item.model_copy(update={"rank": idx}) for idx, item in enumerate(ordered, start=1)]
