"""Semantic and structural validation for ModelAsset definitions against domain_semantics."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from domain_semantics.catalog import load_vocabulary
from domain_semantics.units import canonical_unit

from .schema import ModelAsset


class AssetValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["error", "warning"]
    path: str
    message: str


class AssetValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str | None = None
    valid: bool
    completeness_score: float = Field(ge=0.0, le=1.0)
    missing_fields: list[str] = Field(default_factory=list)
    issues: list[AssetValidationIssue] = Field(default_factory=list)


def compute_completeness(asset: ModelAsset) -> tuple[float, list[str]]:
    """Compute modeling information completeness score in [0, 1] and list missing modeling slots."""
    checks: list[tuple[str, float, bool]] = [
        ("capabilities", 0.15, bool(asset.capabilities)),
        ("material_compatibility.supported_material_types", 0.15, bool(asset.material_compatibility.supported_material_types)),
        (
            "material_compatibility.states_or_transitions",
            0.12,
            bool(
                asset.material_compatibility.state_transitions
                or (asset.material_compatibility.input_states and asset.material_compatibility.output_states)
            ),
        ),
        ("parameters", 0.18, bool(asset.parameters)),
        ("interfaces", 0.12, bool(asset.interfaces)),
        ("behavior.cycle_time_s", 0.12, bool(asset.behavior and asset.behavior.cycle_time_s is not None)),
        ("geometry.dimensions_mm", 0.08, bool(asset.geometry and asset.geometry.dimensions_mm is not None)),
        ("geometry.connection_points", 0.04, bool(asset.geometry and asset.geometry.connection_points)),
        ("model_binding.bound", 0.04, asset.model_binding.binding_status == "bound"),
    ]
    score = sum(weight for _, weight, present in checks if present)
    missing = [slot for slot, _, present in checks if not present]
    return round(min(1.0, score), 4), missing


def validate_asset(asset: ModelAsset) -> AssetValidationResult:
    """Validate a ModelAsset against shared domain_semantics controlled vocabularies."""
    vocab = load_vocabulary()
    known_caps = set(vocab.get("capabilities", {}).keys())
    known_params = vocab.get("parameters", {})
    known_qual = set(vocab.get("quality_parameters", {}).keys())
    known_cats = set(vocab.get("asset_categories", {}).keys())
    known_mats = set(vocab.get("material_types", {}).keys())
    known_states = set(vocab.get("material_state_codes", {}).keys())
    known_ifaces = set(vocab.get("interface_types", {}).keys())

    issues: list[AssetValidationIssue] = []

    def add(code: str, severity: Literal["error", "warning"], path: str, message: str) -> None:
        issues.append(AssetValidationIssue(code=code, severity=severity, path=path, message=message))

    if asset.identity.category not in known_cats:
        add("unknown_asset_category", "error", "identity.category", f"未知资产类别编码：{asset.identity.category}")

    for idx, cap in enumerate(asset.capabilities):
        if cap.code not in known_caps:
            add("unknown_capability_code", "error", f"capabilities[{idx}].code", f"制造能力编码不在共享词表内：{cap.code}")
        for feat in cap.qualitative_features:
            if feat not in known_qual:
                add("unknown_quality_parameter", "warning", f"capabilities[{idx}].qualitative_features",
                    f"定性特征编码不在共享词表内：{feat}")

    mc = asset.material_compatibility
    if not mc.supported_material_types:
        add("missing_material_types", "warning", "material_compatibility.supported_material_types", "未声明适配的标准物料类型")
    for field_name in ("supported_material_types", "input_material_types", "output_material_types"):
        for mat in getattr(mc, field_name):
            if mat not in known_mats:
                add("unknown_material_type", "error", f"material_compatibility.{field_name}", f"物料类型不在受控词表内：{mat}")
    for spec_idx, spec in enumerate(mc.specifications):
        if spec.material_type not in known_mats:
            add("unknown_material_type", "error", f"material_compatibility.specifications[{spec_idx}].material_type",
                f"物料规格引用的类型不在受控词表内：{spec.material_type}")

    for field_name in ("input_states", "output_states"):
        for st in getattr(mc, field_name):
            if st not in known_states:
                add("unknown_material_state", "error", f"material_compatibility.{field_name}", f"物料状态不在受控词表内：{st}")
    for idx, tr in enumerate(mc.state_transitions):
        if tr.from_state not in known_states:
            add("unknown_material_state", "error", f"material_compatibility.state_transitions[{idx}].from_state",
                f"状态转换起点不在受控词表内：{tr.from_state}")
        if tr.to_state not in known_states:
            add("unknown_material_state", "error", f"material_compatibility.state_transitions[{idx}].to_state",
                f"状态转换终点不在受控词表内：{tr.to_state}")
        if tr.material_type and tr.material_type not in known_mats:
            add("unknown_material_type", "error", f"material_compatibility.state_transitions[{idx}].material_type",
                f"状态转换物料类型不在受控词表内：{tr.material_type}")

    for idx, param in enumerate(asset.parameters):
        spec = known_params.get(param.parameter_code)
        if spec is None:
            add("unknown_parameter_code", "error", f"parameters[{idx}].parameter_code",
                f"技术参数编码不在共享词表内：{param.parameter_code}")
        else:
            canon = canonical_unit(param.unit)
            if canon not in spec.get("units", []):
                add("invalid_parameter_unit", "error", f"parameters[{idx}].unit",
                    f"单位 {param.unit} 不适用于参数 {param.parameter_code}（允许：{', '.join(spec.get('units', []))}）")

    for idx, iface in enumerate(asset.interfaces):
        if iface.interface_type not in known_ifaces:
            add("unknown_interface_type", "error", f"interfaces[{idx}].interface_type",
                f"接口类型不在受控词表内：{iface.interface_type}")

    if asset.behavior is None or asset.behavior.cycle_time_s is None:
        add("missing_cycle_time", "warning", "behavior.cycle_time_s", "缺少标称加工时间，节拍相关校验将标记为待确认")
    if asset.geometry is None or asset.geometry.dimensions_mm is None:
        add("missing_geometry_dimensions", "warning", "geometry.dimensions_mm", "缺少几何包络尺寸（占位状态，允许后续补充）")
    if asset.model_binding.binding_status != "bound":
        add("model_file_unbound", "warning", "model_binding", "三维模型文件尚未绑定，已预留稳定 asset_id 绑定键")

    completeness, missing = compute_completeness(asset)
    has_error = any(item.severity == "error" for item in issues)
    return AssetValidationResult(
        asset_id=asset.asset_id,
        valid=not has_error,
        completeness_score=completeness,
        missing_fields=missing,
        issues=issues,
    )


def validate_raw_asset(payload: dict) -> tuple[ModelAsset | None, AssetValidationResult]:
    """Validate raw JSON dict into ModelAsset plus semantic checks."""
    try:
        asset = ModelAsset.model_validate(payload)
    except ValidationError as exc:
        issues = [
            AssetValidationIssue(
                code="schema_validation_error",
                severity="error",
                path=".".join(str(p) for p in err.get("loc", ())) or "$",
                message=err.get("msg", "Schema 校验失败"),
            )
            for err in exc.errors()
        ]
        raw_id = payload.get("identity", {}).get("asset_id") if isinstance(payload, dict) and isinstance(payload.get("identity"), dict) else None
        return None, AssetValidationResult(asset_id=raw_id, valid=False, completeness_score=0.0, issues=issues)
    return asset, validate_asset(asset)
