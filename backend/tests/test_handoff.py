from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.parser import parse_file_bytes, parse_file_path
from domain_semantics.handoff import ResourceMatchingInput, build_resource_matching_input
from domain_semantics.pipeline import project
from domain_semantics.review import ConfirmCapability, apply_review
from domain_semantics.schema import RequirementSemanticModel
from domain_semantics.sources import file_document, message_document

ROOT = Path(__file__).resolve().parents[2]


def source_table(body: str):
    parsed = parse_file_bytes(body.encode(), "process.csv")
    return file_document("workbook", "process.csv", parsed.parsed_text, parsed.parsed_preview)


def task_source(extra=""):
    return message_document("user", "构建装配产线数字孪生模型。" + extra)


@pytest.fixture
def complete_model():
    table = source_table(
        "工序编号,工序名称,制造能力,输入物料,输出物料,工序类型,前置工序,关键工艺约束\n"
        "A1,清洗,清洗,来料外壳,清洗后外壳,主线工序,无,不得损伤表面\n"
        "Z1,端盖上料,上料,端盖,已供给端盖,旁路工序,无,\n"
        "B2,装配,汇合外壳；装配,外壳；端盖,成品,汇合工序,A1；Z1,完成定位\n"
    )
    return project([table, task_source("要求整线节拍不超过60秒/件。")], "complete")


def blockers(value):
    return {item.code for item in value.handoff_validation.issues if item.severity == "blocker"}


def test_complete_projection_is_self_contained_and_round_trips(complete_model):
    before = complete_model.model_dump_json()
    view = build_resource_matching_input(complete_model)
    assert view.handoff_validation.status == "ready"
    assert view.source_model.content_digest == complete_model.content_digest
    assert view.source_model.schema_version == "2.2.0"
    [line_takt] = [view.constraints[key] for key in view.line_constraint_ids]
    assert (line_takt.comparator, line_takt.value, line_takt.unit, line_takt.basis) == ("<=", 60, "s", "件")
    wash, feed, assemble = view.operation_requirements
    assert wash.required_capability_codes == ["CLEAN"]
    assert feed.required_capability_codes == ["FEED"]
    assert assemble.required_capability_codes == ["ASSEMBLE"]
    assert assemble.predecessor_ids == [wash.operation_id, feed.operation_id]
    assert [view.materials[key].name for key in assemble.input_material_ids] == ["外壳", "端盖"]
    assert [view.materials[key].name for key in assemble.output_material_ids] == ["成品"]
    assert view.constraints[assemble.constraint_ids[0]].description == "完成定位"
    assert view.material_states[wash.output_state_ids[0]].name == "清洗后外壳"
    assert view.process_graph.nodes[1].category == "bypass"
    assert {edge.kind for edge in view.process_graph.edges} == {"merge"}
    assert all(edge.relation_ids and all(cite.location.cell for cite in edge.evidence) for edge in view.process_graph.edges)
    assert complete_model.model_dump_json() == before
    assert view == ResourceMatchingInput.model_validate_json(view.model_dump_json())
    assert view == build_resource_matching_input(complete_model)
    assert ResourceMatchingInput.model_json_schema()["properties"]["operation_requirements"]["type"] == "array"


def test_optional_missing_parameters_and_partial_coverage_are_nonblocking(complete_model):
    complete_model.metadata.extraction_status = "partial"
    view = build_resource_matching_input(complete_model)
    assert complete_model.validation.status == "verified"
    assert view.handoff_validation.status == "ready_with_warnings"
    assert not blockers(view)
    assert {item.code for item in view.handoff_validation.issues} == {"handoff_extraction_coverage"}
    assert all(not item.equipment_requirement for item in view.operation_requirements)
    assert not any(item.parameter_code == "process_time" for item in view.constraints.values())


def test_missing_required_capability_and_invalid_operation_reference_are_blocked():
    table = source_table("工序编号,工序名称,制造能力,前置工序\nA1,清洗,,Z0\nB2,装配,装配,A1")
    formal = project([table, task_source()], "with-errors")
    view = build_resource_matching_input(formal)
    assert view.handoff_validation.status == "blocked"
    assert {"handoff_capability_missing", "handoff_transition_unverified",
            "handoff_predecessor_unresolved"} <= blockers(view)
    issue = next(item for item in view.handoff_validation.issues if item.code == "handoff_transition_unverified")
    assert issue.operation_ids == ["operation:A1"]
    assert view.operation_requirements[0].required_capability_codes == []
    assert view.operation_requirements[0].has_blocking_issues
    assert view.operation_requirements[1].required_capability_codes == ["ASSEMBLE"]
    assert formal.metadata.extraction_status == "complete"


def test_unverified_capability_is_excluded_until_human_confirmation():
    table = source_table("工序编号,工序名称,制造能力\nA1,工件处理,表面处理甲")
    base = project([table, task_source()], "base")
    preview = build_resource_matching_input(base)
    capability, = base.capabilities
    assert preview.operation_requirements[0].capability_ids == [capability.id]
    assert preview.operation_requirements[0].required_capability_codes == []
    assert "handoff_required_capability_unverified" in blockers(preview)
    review = ConfirmCapability(action="confirm_capability", capability_id=capability.id, code="CLEAN",
                               note="工艺负责人确认此项指清洗")
    child, _ = apply_review(base, "child", "review", review)
    verified = build_resource_matching_input(child)
    assert verified.handoff_validation.status == "ready"
    assert verified.operation_requirements[0].required_capability_codes == ["CLEAN"]
    assert next(iter(verified.capabilities.values())).review_evidence[0].method == "manual"
    assert base.capabilities[0].code is None


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0", "2.1.0"])
def test_historical_null_task_is_diagnostic_and_read_only(complete_model, version):
    stored = complete_model.model_dump(mode="json", exclude={"metadata", "modeling_task", "process_graph"})
    stored.update(schema_version=version, modeling_intents=[])
    history = RequirementSemanticModel.model_validate(stored)
    view = build_resource_matching_input(history)
    assert view.source_model.schema_version == version
    assert view.handoff_validation.status == "blocked"
    assert "handoff_task_missing" in blockers(view)
    assert len(view.process_graph.edges) == 2
    assert history.modeling_task is history.process_graph is None


def test_conditional_route_and_explicit_rework_keep_their_conditions():
    table = source_table(
        "工序编号,工序名称,制造能力,下一工序,转序条件\nA1,清洗,清洗,B2,清洗完毕\nB2,检测,检测,,"
    )
    message = task_source("不合格时检测返工至清洗，最多2次。")
    payload = {"relations": [{
        "type": "reworksTo", "source_id": "B2", "target_id": "A1", "condition": "不合格",
        "max_iterations": 2, "evidence": [{"quote": "不合格时检测返工至清洗，最多2次"}],
    }]}
    model = project([table, message], "bounded", payload)
    view = build_resource_matching_input(model)
    assert view.handoff_validation.status == "ready"
    assert [(item.kind, item.condition, item.max_iterations) for item in view.process_graph.edges] == [
        ("conditional", "清洗完毕", None), ("rework", "不合格", 2),
    ]
    del payload["relations"][0]["condition"]
    model = project([table, message], "missing-condition", payload)
    view = build_resource_matching_input(model)
    assert {"handoff_transition_unverified"} <= blockers(view)
    assert "rework_condition_pending" in {issue.code for issue in model.validation.issues}


def test_invalid_mandatory_constraint_blocks_but_invalid_preference_warns():
    for wording, expected in (("必须", "blocked"), ("建议", "ready_with_warnings")):
        table = source_table(f"工序编号,工序名称,制造能力,关键工艺约束\nA1,清洗,清洗,{wording}工时不超过30mm")
        model = project([table, task_source()], "constraint")
        [constraint] = model.constraints
        assert constraint.strength == ("mandatory" if wording == "必须" else "preferred")
        view = build_resource_matching_input(model)
        assert view.handoff_validation.status == expected
        assert view.constraints[constraint.id].status == "invalid"


def test_ready_status_cannot_be_relabelled(complete_model):
    output = build_resource_matching_input(complete_model).model_dump(mode="json")
    output["handoff_validation"]["status"] = "blocked"
    with pytest.raises(ValidationError):
        ResourceMatchingInput.model_validate(output)


def test_original_workbook_and_narrative_handoff():
    workbook = ROOT / "test case/工艺需求文件.xlsx"
    parsed = parse_file_path(workbook)
    document = file_document("workbook", workbook.name, parsed.parsed_text, parsed.parsed_preview)
    narrative = ROOT / "test case/自然语言建模需求.md"
    model = project([document, message_document("narrative", narrative.read_text(encoding="utf-8"))], "real")
    view = build_resource_matching_input(model)
    assert view.handoff_validation.status == "ready_with_warnings"
    assert len(view.operation_requirements) == 16 and len(view.process_graph.edges) == 15
    assert sum(node.category == "bypass" for node in view.process_graph.nodes) == 3
    assert sum(edge.kind == "merge" for edge in view.process_graph.edges) == 5
    assert all(item.required_capability_codes for item in view.operation_requirements)
    assert view.modeling_task.target_product_id is None
    assert {item.code for item in view.handoff_validation.issues} == {
        "handoff_target_product_unconfirmed", "conditional_route_missing",
    }
    [takt] = [view.constraints[key] for key in view.line_constraint_ids]
    assert (takt.value, takt.comparator, takt.unit, takt.basis) == (120, "<=", "s", "模组")
    [component] = [edge for edge in view.relations if edge.type == "hasComponent" and edge.quantity]
    assert component.quantity.value == 8
    assert view.materials[component.target_id].name == "方形电芯"
    assert not any(item.equipment_requirement for item in view.operation_requirements)
    assert not any(item.parameter_code == "process_time" for item in view.constraints.values())


def test_adjudicated_candidate_stays_separate_from_verified_capability():
    from domain_semantics.pipeline import Builder, table_rows

    table = source_table("工序编号,工序名称,制造能力,前置工序\nA1,清洗,清洗,无\nB2,处理,未知处理,A1")
    builder = Builder([table, task_source()], "disambiguation")
    builder.table(list(table_rows([table])))
    builder.explicit_requirements(task_source())
    builder.mapping_queue()
    tentative = next(item for item in builder.model.capabilities if item.code is None)
    tentative.candidates = ["CLEAN", "MACHINE"]
    builder.accept_mapping({tentative.id: "CLEAN"})
    model = builder.finish()
    view = build_resource_matching_input(model)
    assert view.operation_requirements[0].required_capability_codes == ["CLEAN"]
    assert view.operation_requirements[1].required_capability_codes == []
    assert view.capabilities[tentative.id].status == "pending"
    assert "handoff_required_capability_unverified" in blockers(view)


def test_incompatible_preference_does_not_invalidate_required_bound():
    table = source_table("工序编号,工序名称,制造能力,关键工艺约束\nA1,清洗,清洗,必须保证工时不超过30秒；建议工时至少60秒")
    model = project([table, task_source()], "preference")
    assert [(item.strength, item.status) for item in model.constraints] == [
        ("mandatory", "verified"), ("preferred", "conflict"),
    ]
    assert build_resource_matching_input(model).handoff_validation.status == "ready_with_warnings"


def test_executable_import_rejects_dangling_reference(complete_model):
    output = build_resource_matching_input(complete_model).model_dump(mode="json")
    output["operation_requirements"][0]["input_material_ids"] = ["missing-material"]
    with pytest.raises(ValidationError, match="无法解析"):
        ResourceMatchingInput.model_validate(output)
