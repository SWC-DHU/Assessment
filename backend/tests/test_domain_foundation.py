import pytest
from pydantic import ValidationError

from domain_semantics import SemanticModel, ontology_metadata
from domain_semantics.catalog import candidates
from domain_semantics.schema import Constraint, Operation


def test_versioned_catalog_and_public_schema():
    metadata = ontology_metadata()
    assert metadata["ontology_version"] == "3.0.0"
    assert set(metadata["core_concepts"]) == {"Product", "Material", "Operation", "CapabilityRequirement", "Constraint", "Objective"}
    assert "ProcessRelation" in ontology_metadata("2.0.0")["concepts"]
    assert "reworksTo" in metadata["relations"]
    assert candidates("capabilities", " 精确定位 ") == ["POSITION"]
    assert candidates("parameters", "单件工时") == ["process_time"]
    assert candidates("parameters", "生产节拍") == ["takt_time"]
    assert not candidates("capabilities", "未知能力")
    assert "relations" in SemanticModel.model_json_schema()["properties"]
    assert Operation(id="op:01", name="清洗", number="01").equipment_requirement is None


def test_domain_rejects_invalid_numeric_values_and_extra_properties():
    with pytest.raises(ValidationError):
        Constraint(id="c:1", name="工时", target_id="op:01", value=float("nan"))
    with pytest.raises(ValidationError):
        Operation(id="op:01", name="清洗", asset_id="invented")
