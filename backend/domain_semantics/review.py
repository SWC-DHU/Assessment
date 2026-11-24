"""Typed human corrections applied to a new snapshot of a requirement model."""
from typing import Annotated, Literal

from pydantic import Field

from .catalog import load_vocabulary
from .modeling_task import TASK_KIND
from .pipeline import Builder, record_id
from .schema import (Capability, DomainRecord, ModelingScope, ModelingTask, Objective, Operation, ProcessRelation,
                     Product, QuantitySpecification)
from .sources import message_document


class ReviewAction(DomainRecord):
    note: str = Field(min_length=1, max_length=1200)


class RenameOperation(ReviewAction):
    action: Literal["rename_operation"]
    operation_id: str
    name: str = Field(min_length=1, max_length=150)


class SetComponentQuantity(ReviewAction):
    action: Literal["set_component_quantity"]
    relation_id: str
    quantity: QuantitySpecification


class ConfirmCapability(ReviewAction):
    action: Literal["confirm_capability"]
    capability_id: str
    code: str


class AddTransition(ReviewAction):
    action: Literal["add_transition"]
    source_id: str
    target_id: str
    type: Literal["directlyPrecedes", "precedes", "dependsOn", "branchesTo", "mergesInto",
                  "conditionallyPrecedes", "optionalNext", "reworksTo"]
    condition: str | None = Field(default=None, max_length=300)
    max_iterations: int | None = Field(default=None, ge=1)


class ConfirmFact(ReviewAction):
    action: Literal["confirm_fact"]
    fact_id: str


class SetModelingTask(ReviewAction):
    action: Literal["set_modeling_task"]
    target_scope: ModelingScope | None = None
    target_product_id: str | None = None


ReviewRequest = Annotated[
    RenameOperation | SetComponentQuantity | ConfirmCapability | AddTransition | ConfirmFact | SetModelingTask,
    Field(discriminator="action"),
]


def apply_review(base, run_id, message_id, review: ReviewRequest):
    if not review.note.strip():
        raise ValueError("请填写人工核对依据")
    fields = review.model_dump(exclude={"note"}, exclude_none=True)
    description = "\n".join(f"{key} = {value}" for key, value in fields.items())
    statement = f"人工核对\n{description}\n依据：{review.note}"
    document = message_document(message_id, statement)
    builder = Builder([document], run_id, base)
    if base.metadata:
        builder.model.metadata.session_id = base.metadata.session_id
        builder.model.metadata.batches = base.metadata.batches
        builder.model.metadata.extraction_status = base.metadata.extraction_status
        builder.model.metadata.processed_source_ids = base.metadata.processed_source_ids
    cite = document.fragments[0].evidence(run_id, method="manual")

    def require(key, kind):
        item = builder.by_id.get(key)
        if not isinstance(item, kind):
            raise ValueError("审核对象不存在或类型不符")
        return item

    if isinstance(review, RenameOperation):
        fact = require(review.operation_id, Operation)
        if not review.name.strip():
            raise ValueError("工序名称不能为空")
        fact.name = review.name.strip()
    elif isinstance(review, SetComponentQuantity):
        fact = require(review.relation_id, ProcessRelation)
        if fact.type != "hasComponent":
            raise ValueError("数量修订仅适用于产品组成关系")
        fact.quantity = review.quantity
    elif isinstance(review, ConfirmCapability):
        fact = require(review.capability_id, Capability)
        if review.code not in load_vocabulary()["capabilities"]:
            raise ValueError("能力编码不在当前受控词表")
        fact.code = review.code
        fact.candidates = sorted(set([*fact.candidates, review.code]))
        fact.standard_term = review.code
        fact.mapping_basis = "人工确认"
    elif isinstance(review, AddTransition):
        require(review.source_id, Operation)
        require(review.target_id, Operation)
        if review.source_id == review.target_id:
            raise ValueError("工艺关系的起点和终点必须不同")
        if review.type == "conditionallyPrecedes" and not (review.condition or "").strip():
            raise ValueError("条件转序需要填写条件")
        if review.max_iterations is not None and review.type != "reworksTo":
            raise ValueError("返工次数仅适用于返工路径")
        fact = builder.relation(review.type, review.source_id, review.target_id, [cite],
                                origin="manual", condition=review.condition, max_iterations=review.max_iterations)
        fact.asserted_as = "explicit"
    elif isinstance(review, SetModelingTask):
        if review.target_product_id is not None:
            product = require(review.target_product_id, Product)
            if product.status != "verified":
                raise ValueError("请选择已核验的目标产品")
        fact = next((item for item in builder.model.modeling_intents if item.kind == TASK_KIND), None)
        if fact is None:
            fact = builder.add("modeling_intents", Objective(
                id=record_id("intent", TASK_KIND), kind=TASK_KIND, description=review.note.strip(), evidence=[cite],
            ))
        history = base.modeling_task.review_evidence if base.modeling_task else []
        builder.model.modeling_task = ModelingTask(
            task_type=TASK_KIND, target_scope=review.target_scope, target_product_id=review.target_product_id,
            objective_ids=[fact.id], required_outputs=["digital_twin_model"], review_evidence=[*history, cite],
        )
    else:
        fact = builder.by_id.get(review.fact_id)
        if fact is None:
            raise ValueError("确认对象不存在")
        if isinstance(fact, ProcessRelation) and fact.asserted_as == "inferred":
            fact.asserted_as = "explicit"
            fact.evidence.append(cite)
    fact.review = "confirmed"
    fact.review_evidence.append(cite)
    builder.mapping_queue()
    return builder.finish(), statement
