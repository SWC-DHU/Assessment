"""Unit tests for ModelAsset Schema, vocabulary validation, and AssetCatalog."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain_semantics.catalog import load_vocabulary
from model_assets import (
    AssetCatalog,
    ModelAsset,
    ModelBinding,
    get_default_catalog,
    validate_asset,
    validate_raw_asset,
)


def _sample_asset_dict(asset_id: str = "MA-TEST-01") -> dict:
    return {
        "schema_version": "1.0.0",
        "data_source": "synthetic",
        "identity": {
            "asset_id": asset_id,
            "name": "测试用方形电芯清洗机",
            "category": "cleaning",
            "description": "合成测试资产，用于验证 Schema 与校验规则。",
            "version": "1.0.0",
            "tags": ["测试", "清洗"],
        },
        "capabilities": [
            {
                "code": "LASER_CLEAN",
                "name": "激光清洗",
                "description": "脉冲激光清洗电芯表面",
                "qualitative_features": ["surface_condition"],
            }
        ],
        "material_compatibility": {
            "supported_material_types": ["prismatic_cell"],
            "input_states": ["positioned"],
            "output_states": ["cleaned"],
            "state_transitions": [
                {"material_type": "prismatic_cell", "from_state": "positioned", "to_state": "cleaned"}
            ],
        },
        "parameters": [
            {"parameter_code": "process_time", "name": "加工时间", "value": 9.0, "unit": "s", "basis": "电芯"},
            {"parameter_code": "laser_power", "name": "激光功率", "value": 200.0, "unit": "W", "capability_code": "LASER_CLEAN"},
        ],
        "interfaces": [
            {
                "interface_id": "mat_in",
                "name": "进料口",
                "interface_type": "material_flow",
                "direction": "input",
                "compatible_conditions": ["方形电芯托盘"],
                "protocol": "Pallet-Conveyor",
            }
        ],
        "geometry": None,
        "behavior": {"cycle_time_s": 9.0, "cycle_time_basis": "电芯", "batch_size": 1},
        "model_binding": {"binding_status": "unbound"},
        "provenance": {
            "author": "pytest",
            "created_at": "2026-11-24T00:00:00Z",
            "synthetic_disclaimer": "Synthetic unit test asset.",
        },
    }


def test_default_synthetic_catalog_completeness_and_integrity():
    catalog = get_default_catalog()
    assets = catalog.all_assets()
    assert 40 <= len(assets) <= 65
    categories = {a.identity.category for a in assets}
    expected_cats = set(load_vocabulary()["asset_categories"].keys())
    assert categories == expected_cats
    for asset in assets:
        assert asset.data_source == "synthetic"
        assert asset.provenance.synthetic_disclaimer
        assert asset.instantiation_policy.reusable is True
        report = validate_asset(asset)
        assert report.valid, f"{asset.asset_id} failed validation: {report.issues}"
        # Ensure no operation_id hardcoding in asset definitions
        raw = asset.model_dump_json()
        assert "operation:" not in raw


def test_schema_rejects_extra_fields_nan_and_invalid_binding():
    raw = _sample_asset_dict()
    raw["operation_id"] = "operation:01"
    with pytest.raises(ValidationError):
        ModelAsset.model_validate(raw)

    raw_nan = _sample_asset_dict()
    raw_nan["parameters"][0]["value"] = float("nan")
    with pytest.raises(ValidationError):
        ModelAsset.model_validate(raw_nan)

    raw_no_disclaimer = _sample_asset_dict()
    raw_no_disclaimer["provenance"]["synthetic_disclaimer"] = ""
    with pytest.raises(ValidationError):
        ModelAsset.model_validate(raw_no_disclaimer)

    with pytest.raises(ValidationError):
        ModelBinding(binding_status="bound", model_uri=None, file_format=None)


def test_semantic_validation_detects_unknown_codes_and_unit_mismatch():
    raw = _sample_asset_dict()
    raw["capabilities"][0]["code"] = "INVENTED_CAP"
    raw["parameters"][1]["capability_code"] = "INVENTED_CAP"
    raw["parameters"][0]["unit"] = "kg"  # process_time cannot use kg
    raw["material_compatibility"]["supported_material_types"] = ["unknown_material"]
    asset, report = validate_raw_asset(raw)
    assert asset is not None
    assert not report.valid
    codes = {i.code for i in report.issues if i.severity == "error"}
    assert {"unknown_capability_code", "invalid_parameter_unit", "unknown_material_type"} <= codes


def test_catalog_crud_import_export_and_binding_in_isolated_dir(tmp_path):
    catalog = AssetCatalog(data_dir=tmp_path)
    assert catalog.all_assets() == []

    asset, report = catalog.upsert(_sample_asset_dict("MA-ISO-01"), persist=True)
    assert report.valid and asset.asset_id == "MA-ISO-01"
    assert (tmp_path / "MA-ISO-01.json").exists()

    # Reload from disk
    reloaded = AssetCatalog(data_dir=tmp_path)
    assert reloaded.get("MA-ISO-01") is not None

    # Update binding to bound
    bound_asset, bound_report = reloaded.update_binding(
        "MA-ISO-01",
        ModelBinding(binding_status="bound", model_uri="geometry/MA-ISO-01.glb", file_format="glb"),
    )
    assert bound_report.valid
    assert bound_asset.model_binding.binding_status == "bound"
    assert bound_report.completeness_score > report.completeness_score

    # Batch import skip duplicate vs overwrite
    res_skip = reloaded.import_batch([_sample_asset_dict("MA-ISO-01"), _sample_asset_dict("MA-ISO-02")], overwrite=False)
    assert res_skip["imported_count"] == 1
    assert len(res_skip["skipped"]) == 1
    assert reloaded.export_payload()["count"] == 2
