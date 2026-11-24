"""Stage 1 & 2: Requirement dereferencing, normalization, and ontology-guided resource retrieval."""
from __future__ import annotations

from dataclasses import dataclass, field

from domain_semantics.catalog import (
    load_vocabulary,
    normalize,
    normalize_material_state,
    normalize_material_type,
)
from domain_semantics.handoff import OperationResourceRequirement, ResourceMatchingInput
from domain_semantics.schema import Capability, Constraint, InformationFlow
from model_assets.catalog import AssetCatalog
from model_assets.schema import ModelAsset


@dataclass
class NormalizedMaterialRef:
    material_id: str
    raw_name: str
    canonical_type: str | None


@dataclass
class NormalizedStateRef:
    state_id: str
    raw_name: str
    material_id: str
    material_type: str | None
    canonical_state: str | None


@dataclass
class DereferencedOperation:
    raw: OperationResourceRequirement
    operation_id: str
    operation_number: str | None
    operation_name: str
    operation_type: str | None
    required_capability_codes: list[str]
    capabilities: list[Capability]
    operation_constraints: list[Constraint]
    capability_constraints: list[tuple[str, Constraint]]  # (capability_code, constraint)
    input_materials: list[NormalizedMaterialRef]
    output_materials: list[NormalizedMaterialRef]
    input_states: list[NormalizedStateRef]
    output_states: list[NormalizedStateRef]
    information_flows: list[InformationFlow]
    equipment_requirement: str | None
    has_blocking_issues: bool


@dataclass
class DereferencedMatchingContext:
    handoff_status: str
    operations: list[DereferencedOperation]
    line_constraints: list[Constraint]
    component_multipliers: dict[str, float] = field(default_factory=dict)  # material_type -> qty per product


def dereference_input(rmi: ResourceMatchingInput) -> DereferencedMatchingContext:
    """Dereference all IDs inside ResourceMatchingInput and normalize materials/states via domain_semantics."""
    line_constraints = [
        rmi.constraints[cid]
        for cid in rmi.line_constraint_ids
        if cid in rmi.constraints
    ]

    # Extract BOM component quantities (e.g., 8 prismatic_cell per module) for advisory line-takt notes.
    component_multipliers: dict[str, float] = {}
    for rel in rmi.relations:
        if rel.type == "hasComponent" and rel.status == "verified" and rel.quantity:
            mat = rmi.materials.get(rel.target_id)
            if mat:
                mat_type = normalize_material_type(mat.name)
                if mat_type:
                    component_multipliers[mat_type] = float(rel.quantity.value)

    operations: list[DereferencedOperation] = []
    for op in rmi.operation_requirements:
        caps = [rmi.capabilities[cid] for cid in op.capability_ids if cid in rmi.capabilities]
        op_constraints = [
            rmi.constraints[cid]
            for cid in op.constraint_ids
            if cid in rmi.constraints
        ]
        cap_constraints: list[tuple[str, Constraint]] = []
        seen_cap_cids: set[str] = set()
        for cap in caps:
            cap_code = cap.code or ""
            for cid in cap.parameter_constraint_ids:
                if cid in rmi.constraints and cid not in seen_cap_cids:
                    seen_cap_cids.add(cid)
                    cap_constraints.append((cap_code, rmi.constraints[cid]))

        in_mats: list[NormalizedMaterialRef] = []
        for mid in op.input_material_ids:
            mat = rmi.materials.get(mid)
            if mat:
                in_mats.append(NormalizedMaterialRef(mid, mat.name, normalize_material_type(mat.material_type or mat.name)))

        out_mats: list[NormalizedMaterialRef] = []
        for mid in op.output_material_ids:
            mat = rmi.materials.get(mid)
            if mat:
                out_mats.append(NormalizedMaterialRef(mid, mat.name, normalize_material_type(mat.material_type or mat.name)))

        in_states: list[NormalizedStateRef] = []
        for sid in op.input_state_ids:
            st = rmi.material_states.get(sid)
            if st:
                base_mat = rmi.materials.get(st.material_id)
                base_type = normalize_material_type(base_mat.name) if base_mat else normalize_material_type(st.name)
                in_states.append(NormalizedStateRef(sid, st.name, st.material_id, base_type, normalize_material_state(st.name)))

        out_states: list[NormalizedStateRef] = []
        for sid in op.output_state_ids:
            st = rmi.material_states.get(sid)
            if st:
                base_mat = rmi.materials.get(st.material_id)
                base_type = normalize_material_type(base_mat.name) if base_mat else normalize_material_type(st.name)
                out_states.append(NormalizedStateRef(sid, st.name, st.material_id, base_type, normalize_material_state(st.name)))

        operations.append(
            DereferencedOperation(
                raw=op,
                operation_id=op.operation_id,
                operation_number=op.operation_number,
                operation_name=op.operation_name,
                operation_type=op.operation_type,
                required_capability_codes=list(op.required_capability_codes),
                capabilities=caps,
                operation_constraints=op_constraints,
                capability_constraints=cap_constraints,
                input_materials=in_mats,
                output_materials=out_mats,
                input_states=in_states,
                output_states=out_states,
                information_flows=list(op.information_flows),
                equipment_requirement=op.equipment_requirement,
                has_blocking_issues=op.has_blocking_issues,
            )
        )

    return DereferencedMatchingContext(
        handoff_status=rmi.handoff_validation.status,
        operations=operations,
        line_constraints=line_constraints,
        component_multipliers=component_multipliers,
    )


def retrieve_semantic_candidates(
    op: DereferencedOperation,
    catalog: AssetCatalog,
) -> tuple[list[ModelAsset], list[ModelAsset]]:
    """Retrieve candidate assets via shared ontology capability codes.

    Returns (full_coverage_pool, partial_coverage_pool):
    - full_coverage_pool: assets whose capability codes are a superset of op.required_capability_codes
    - partial_coverage_pool: assets that cover a non-empty strict subset of op.required_capability_codes
    """
    required = set(op.required_capability_codes)
    if not required:
        return [], []
    full_pool: list[ModelAsset] = []
    partial_pool: list[ModelAsset] = []
    for asset in catalog.all_assets():
        provided = set(asset.capability_codes)
        overlap = required & provided
        if not overlap:
            continue
        if required <= provided:
            full_pool.append(asset)
        else:
            partial_pool.append(asset)
    return full_pool, partial_pool


def keyword_tokenize(text: str) -> set[str]:
    """Simple character/word tokenizer for Method A keyword baseline."""
    cleaned = normalize(text)
    if not cleaned:
        return set()
    tokens: set[str] = set()
    for part in cleaned.replace("、", " ").replace("；", " ").replace("，", " ").replace("。", " ").replace("（", " ").replace("）", " ").split():
        if len(part) >= 2:
            tokens.add(part)
        # Add Chinese bigrams for substring-sensitive keyword matching
        cjk = "".join(ch for ch in part if "\u4e00" <= ch <= "\u9fff")
        for idx in range(max(0, len(cjk) - 1)):
            tokens.add(cjk[idx : idx + 2])
    return tokens


def retrieve_keyword_candidates(op: DereferencedOperation, catalog: AssetCatalog) -> list[tuple[ModelAsset, float, list[str]]]:
    """Method A baseline: pure lexical/keyword overlap retrieval without ontology codes or constraint engine."""
    vocab = load_vocabulary().get("capabilities", {})
    cap_words = [
        word
        for code in op.required_capability_codes
        for word in [code, *vocab.get(code, [])[:2]]
    ]
    query_parts = [
        op.operation_name,
        " ".join(cap_words),
        " ".join(m.raw_name for m in op.input_materials + op.output_materials),
        " ".join(s.raw_name for s in op.input_states + op.output_states),
        " ".join(c.description or c.name for c in op.operation_constraints),
        op.equipment_requirement or "",
    ]
    query_tokens = keyword_tokenize(" ".join(query_parts))
    if not query_tokens:
        return []

    scored: list[tuple[ModelAsset, float, list[str]]] = []
    for asset in catalog.all_assets():
        doc_parts = [
            asset.identity.name,
            asset.identity.description,
            asset.identity.subcategory or "",
            " ".join(asset.identity.tags),
            " ".join(c.name + " " + c.description for c in asset.capabilities),
        ]
        doc_tokens = keyword_tokenize(" ".join(doc_parts))
        overlap = sorted(query_tokens & doc_tokens)
        if not overlap:
            continue
        score = len(overlap) / max(1.0, (len(query_tokens) * len(doc_tokens)) ** 0.5)
        scored.append((asset, min(1.0, score), overlap[:12]))
    scored.sort(key=lambda item: (-item[1], item[0].asset_id))
    return scored
