"""The standalone, deterministic projection consumed by resource matching."""
from typing import Literal

from pydantic import Field, model_validator

from .modeling_task import build_modeling_task
from .process_graph import build_process_graph
from .schema import (
    Capability, Constraint, DomainRecord, Evidence, ExtractionStatus, HandoffValidation, InformationFlow, Issue, Material,
    MaterialState,
    ModelingTask, Objective, ProcessGraph, ProcessRelation, Product, RequirementSemanticModel, ReviewState, SchemaVersion,
    SourceRef,
)
from .sources import digest
from .validation import check_handoff


class SourceModel(DomainRecord):
    run_id: str
    schema_version: SchemaVersion
    ontology_version: str
    vocabulary_version: str
    content_digest: str = Field(min_length=1)
    extraction_status: ExtractionStatus | None
    semantic_validation_status: ReviewState


class OperationResourceRequirement(DomainRecord):
    operation_id: str = Field(min_length=1)
    operation_name: str = Field(min_length=1)
    operation_number: str | None
    operation_type: str | None
    standard_term: str | None
    required_capability_codes: list[str] = Field(description="通过词表、来源和明确工序关联校验的能力编码")
    capability_ids: list[str] = Field(description="原模型的工序能力引用，可能包含待确认项")
    constraint_ids: list[str] = Field(description="直接作用于工序的约束；能力级约束通过能力实例读取")
    input_material_ids: list[str]
    output_material_ids: list[str]
    input_state_ids: list[str]
    output_state_ids: list[str]
    predecessor_ids: list[str] = Field(description="明确声明的直接前驱；完整关系见工艺图和 relations")
    information_flows: list[InformationFlow]
    equipment_requirement: str | None
    equipment_evidence: list[Evidence]
    source_status: ReviewState
    review: Literal["unreviewed", "confirmed"]
    evidence: list[Evidence]
    review_evidence: list[Evidence]
    has_blocking_issues: bool


class ResourceMatchingInput(DomainRecord):
    contract_version: Literal["1.0.0"] = "1.0.0"
    source_model: SourceModel
    modeling_task: ModelingTask | None
    target_product_ids: list[str]
    line_constraint_ids: list[str]
    operation_requirements: list[OperationResourceRequirement]
    process_graph: ProcessGraph
    products: dict[str, Product]
    materials: dict[str, Material]
    material_states: dict[str, MaterialState]
    capabilities: dict[str, Capability]
    constraints: dict[str, Constraint]
    objectives: dict[str, Objective]
    relations: list[ProcessRelation]
    source_refs: list[SourceRef]
    unresolved_items: list[Issue]
    handoff_validation: HandoffValidation

    @model_validator(mode="after")
    def check_executable_shape(self):
        if self.handoff_validation.status == "blocked":
            return self
        task = self.modeling_task
        if task is None or not self.operation_requirements:
            raise ValueError("可交接输入必须有建模任务、工序及已核验的工序能力")
        operations = {op.operation_id for op in self.operation_requirements}
        if len(operations) != len(self.operation_requirements) or operations != {node.id for node in self.process_graph.nodes}:
            raise ValueError("工序标识与工艺图节点必须一致")
        if not (set(task.objective_ids) <= self.objectives.keys() and set(task.constraint_ids) <= self.constraints.keys()
                and set(self.line_constraint_ids) <= self.constraints.keys()
                and set(self.target_product_ids) <= self.products.keys()
                and self.target_product_ids == ([task.target_product_id] if task.target_product_id else [])):
            raise ValueError("建模任务、整线约束或目标产品的引用无法从交接文件解析")
        for op in self.operation_requirements:
            if op.has_blocking_issues or not op.required_capability_codes:
                raise ValueError("可交接工序必须有已核验的能力要求")
            references = (
                (op.capability_ids, self.capabilities.keys()), (op.constraint_ids, self.constraints.keys()),
                (op.input_material_ids + op.output_material_ids, self.materials.keys()),
                (op.input_state_ids + op.output_state_ids, self.material_states.keys()),
                (op.predecessor_ids, operations),
            )
            if any(not set(keys) <= available for keys, available in references):
                raise ValueError(f"工序 {op.operation_id} 存在无法解析的交接引用")
        return self


def build_resource_matching_input(model: RequirementSemanticModel) -> ResourceMatchingInput:
    task = model.modeling_task
    if task is None and model.schema_version != "2.2.0":
        task = build_modeling_task(model)
    graph = model.process_graph or build_process_graph(model)
    report, approved_codes = check_handoff(model, task, graph)
    blocked = {key for issue in report.issues if issue.severity == "blocker" for key in issue.operation_ids}
    requirements = [
        OperationResourceRequirement(
            operation_id=op.id, operation_name=op.name, operation_number=op.number,
            operation_type=op.operation_type, standard_term=op.standard_term,
            required_capability_codes=approved_codes[op.id], capability_ids=op.capability_ids,
            constraint_ids=op.constraint_ids, input_material_ids=op.input_material_ids,
            output_material_ids=op.output_material_ids, input_state_ids=op.input_state_ids,
            output_state_ids=op.output_state_ids, predecessor_ids=op.predecessor_ids,
            information_flows=op.information_flows, equipment_requirement=op.equipment_requirement,
            equipment_evidence=op.equipment_evidence, source_status=op.status, review=op.review,
            evidence=op.evidence, review_evidence=op.review_evidence, has_blocking_issues=op.id in blocked,
        )
        for op in model.operations
    ]
    return ResourceMatchingInput(
        source_model=SourceModel(
            run_id=model.run_id, schema_version=model.schema_version, ontology_version=model.ontology_version,
            vocabulary_version=model.vocabulary_version,
            content_digest=model.content_digest or digest(model.model_dump_json(exclude={"created_at", "content_digest"})),
            extraction_status=model.metadata.extraction_status if model.metadata else None,
            semantic_validation_status=model.validation.status,
        ),
        modeling_task=task,
        target_product_ids=[task.target_product_id] if task and task.target_product_id else [],
        line_constraint_ids=[item.id for item in model.constraints if item.target_id == "line"],
        operation_requirements=requirements,
        process_graph=graph,
        products={item.id: item for item in model.products},
        materials={item.id: item for item in model.materials},
        material_states={item.id: item for item in model.material_states},
        capabilities={item.id: item for item in model.capabilities},
        constraints={item.id: item for item in model.constraints},
        objectives={item.id: item for item in model.modeling_intents},
        relations=model.relations,
        source_refs=model.source_refs,
        unresolved_items=model.unresolved_items,
        handoff_validation=report,
    )
