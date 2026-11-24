import pytest
from pydantic import ValidationError

from domain_semantics.candidates import ExtractionCandidates
from domain_semantics.pipeline import project
from domain_semantics.review import SetModelingTask, apply_review
from domain_semantics.schema import RequirementSemanticModel
from domain_semantics.sources import message_document


def test_task_references_objectives_product_and_line_constraints():
    message = message_document("source", "构建泵体装配产线数字孪生模型。每个泵体由2件外壳组成。"
                                        "要求整线节拍不超过30秒/件。基于模型库完成设备匹配。")
    model = project([message], "run")
    task = model.modeling_task
    assert model.schema_version == model.metadata.schema_version == "2.2.0"
    assert task.task_type == model.modeling_intents[0].kind == "digital_twin_modeling"
    assert task.target_scope == "production_line"
    assert task.target_product_id == model.products[0].id
    assert task.required_outputs == ["digital_twin_model"]
    assert task.objective_ids == [item.id for item in model.modeling_intents]
    assert task.constraint_ids == [model.constraints[0].id]
    assert model.constraints[0].strength == "mandatory"
    assert model.modeling_intents[0].evidence[0] in model.evidences.values()
    assert RequirementSemanticModel.model_validate_json(model.model_dump_json()) == model
    serialized = model.model_dump(mode="json")
    serialized["modeling_task"]["target_scope"] = "invented"
    with pytest.raises(ValidationError):
        RequirementSemanticModel.model_validate(serialized)


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0", "2.1.0"])
def test_historical_snapshot_without_task_remains_readable(version):
    model = project([message_document("source", "工序为检测，编号A1。")], "parent")
    data = model.model_dump(mode="json", exclude={"modeling_task", "metadata"})
    data["schema_version"] = version
    restored = RequirementSemanticModel.model_validate(data)
    assert restored.schema_version == version and restored.modeling_task is None
    assert restored.operations[0].number == "A1"


def test_model_reference_or_negated_request_does_not_create_task():
    doc = message_document("source", "已有数字孪生仿真模型。不要构建装配工作站数字孪生模型。")
    assert project([doc], "run").modeling_task is None


def test_human_target_resolves_conflict_in_a_child_version():
    base = project([message_document("source", "构建装配产线数字孪生模型。另需构建检测工位数字孪生模型。")], "parent")
    assert base.modeling_task.target_scope is None
    assert any(item.code == "task_scope_conflict" for item in base.validation.issues)
    review = SetModelingTask(action="set_modeling_task", target_scope="workstation", note="本轮范围由用户确认为检测工位")
    child, statement = apply_review(base, "child", "review", review)
    assert base.modeling_task.target_scope is None
    assert child.parent_version_id == base.run_id
    assert child.modeling_task.target_scope == "workstation"
    assert child.modeling_task.review_evidence[0].quote == statement
    assert not any(item.code == "task_scope_conflict" for item in child.validation.issues)


def test_candidate_numbers_are_strict_and_extra_fields_are_rejected():
    for fields in ({"value": "120"}, {"value": True}, {"unknown": "asset"}):
        with pytest.raises(ValidationError):
            ExtractionCandidates.model_validate({"constraints": [
                {"id": "c", "name": "节拍", "target_id": "line", "evidence": [{"quote": "节拍"}], **fields},
            ]})


def test_free_text_revision_preserves_manually_confirmed_constraint():
    from domain_semantics.pipeline import Builder
    from domain_semantics.review import ConfirmFact

    source = message_document("source", "工序为检测，编号A1。")
    builder = Builder([source], "base")
    builder.explicit_requirements(source)
    revision = message_document("first", "将工序A1的工时改为10秒。")
    builder = Builder([revision], "first", builder.finish())
    assert builder.revise(revision)
    base = builder.finish()
    [original] = base.constraints
    reviewed, _ = apply_review(base, "reviewed", "review", ConfirmFact(
        action="confirm_fact", fact_id=original.id, note="已核对产线实际工时",
    ))
    update = message_document("update", "将工序A1的工时改为11秒。")
    builder = Builder([update], "child", reviewed)
    assert builder.revise(update)
    child = builder.finish()
    assert child.constraints == reviewed.constraints
    assert any(item.code == "revision_review_required" for item in child.validation.issues)
