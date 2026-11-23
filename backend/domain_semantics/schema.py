"""The domain contract is independent of extraction, persistence and display."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "2.1.0"
ReviewState = Literal["verified", "pending", "conflict", "invalid", "unprocessed"]
RelationKind = Literal[
    "hasComponent", "hasPredecessor", "consumes", "produces", "requiresCapability",
    "appliesTo", "branchesTo", "mergesInto", "reworksTo", "conditionallyPrecedes",
    "precedes", "dependsOn", "directlyPrecedes", "optionalNext", "hasInputState", "hasOutputState", "stateOf",
]
EvidenceMethod = Literal["legacy", "structured", "rule", "llm", "manual"]
ExtractionStatus = Literal["complete", "partial", "failed"]
Comparator = Literal["<", "<=", "=", ">=", ">", "range"]


class DomainRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Location(DomainRecord):
    sheet: str | None = None
    row: int | None = None
    cell: str | None = None
    page: int | None = None
    line: int | None = None
    paragraph: int | None = None
    message_id: str | None = None
    start: int | None = None
    end: int | None = None


class SourceRef(DomainRecord):
    document_id: str
    name: str
    kind: Literal["file", "message", "demo"]
    content_sha256: str


class Evidence(DomainRecord):
    document_id: str
    fragment_id: str
    quote: str = Field(min_length=1)
    location: Location
    extraction_batch: str
    method: EvidenceMethod = "legacy"
    status: ReviewState = "unprocessed"


class Fact(DomainRecord):
    id: str = Field(min_length=1)
    evidence: list[Evidence] = Field(default_factory=list)
    status: ReviewState = "unprocessed"
    review: Literal["unreviewed", "confirmed"] = "unreviewed"
    review_evidence: list[Evidence] = Field(default_factory=list)


class Entity(Fact):
    name: str = Field(min_length=1)
    standard_term: str | None = None
    mapping_basis: str | None = None


class Product(Entity):
    concept: Literal["Product"] = "Product"
    part_number: str | None = None
    component_ids: list[str] = Field(default_factory=list)
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = None


class InformationFlow(DomainRecord):
    name: str
    direction: Literal["input", "output"]
    evidence: list[Evidence]
    status: ReviewState = "verified"


class Operation(Entity):
    concept: Literal["Operation"] = "Operation"
    number: str | None = None
    operation_type: str | None = None
    precursor_declaration: list[str] | None = None
    predecessor_ids: list[str] = Field(default_factory=list)
    input_material_ids: list[str] = Field(default_factory=list)
    output_material_ids: list[str] = Field(default_factory=list)
    capability_ids: list[str] = Field(default_factory=list)
    constraint_ids: list[str] = Field(default_factory=list)
    input_state_ids: list[str] = Field(default_factory=list)
    output_state_ids: list[str] = Field(default_factory=list)
    equipment_requirement: str | None = None
    equipment_evidence: list[Evidence] = Field(default_factory=list)
    information_flows: list[InformationFlow] = Field(default_factory=list)


class Material(Entity):
    concept: Literal["Material"] = "Material"
    material_type: str | None = None
    consumed_by: list[str] = Field(default_factory=list)
    produced_by: list[str] = Field(default_factory=list)


class MaterialState(Entity):
    concept: Literal["MaterialState"] = "MaterialState"
    material_id: str


class QuantitySpecification(DomainRecord):
    value: float = Field(gt=0)
    unit: str = Field(min_length=1)
    basis: str | None = None


class Capability(Entity):
    concept: Literal["Capability"] = "Capability"
    code: str | None = None
    raw_names: list[str] = Field(default_factory=list)
    mapping: Literal["exact", "phrase", "adjudicated", "unresolved"] = "unresolved"
    candidates: list[str] = Field(default_factory=list)
    operation_ids: list[str] = Field(default_factory=list)
    parameter_constraint_ids: list[str] = Field(default_factory=list)


class Constraint(Entity):
    concept: Literal["Constraint"] = "Constraint"
    target_id: str
    parameter_code: str | None = None
    related_parameter_codes: list[str] = Field(default_factory=list)
    comparator: Comparator | None = None
    value: float | None = None
    lower: float | None = None
    upper: float | None = None
    unit: str | None = None
    basis: str | None = None
    strength: Literal["mandatory", "preferred", "unspecified"] = "unspecified"
    description: str | None = None
    constraint_type: Literal["process_time", "wait_time", "buffer_time", "takt_time", "quantity",
                             "quality", "condition", "numeric", "general"] = "general"


class ProcessRelation(Fact):
    concept: Literal["ProcessRelation"] = "ProcessRelation"
    type: RelationKind
    source_id: str
    target_id: str
    condition: str | None = None
    max_iterations: int | None = Field(default=None, ge=1)
    origin: Literal["document", "model", "user", "manual"]
    quantity: QuantitySpecification | None = None
    asserted_as: Literal["explicit", "inferred"] = "explicit"


class Issue(DomainRecord):
    code: str
    message: str
    path: str
    status: ReviewState


class Validation(DomainRecord):
    status: ReviewState = "unprocessed"
    issues: list[Issue] = Field(default_factory=list)


class GraphNode(DomainRecord):
    id: str
    name: str
    number: str | None = None
    category: Literal["main", "bypass", "merge", "optional", "unspecified"]
    layer: int | None = None
    input_material_ids: list[str]
    output_material_ids: list[str]
    capability_ids: list[str]
    constraint_ids: list[str]
    input_state_ids: list[str]
    output_state_ids: list[str]
    evidence: list[Evidence]


class GraphEdge(DomainRecord):
    id: str
    source: str
    target: str
    kind: Literal["declared_predecessor", "direct_transition", "precedence", "dependency",
                  "branch", "merge", "conditional", "rework", "optional"]
    direct: bool
    condition: str | None = None
    max_iterations: int | None = None
    status: ReviewState
    evidence: list[Evidence]
    relation_ids: list[str]


class ProcessGraph(DomainRecord):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class BatchReport(DomainRecord):
    id: str
    source_ids: list[str]
    scope: str
    status: Literal["complete", "partial", "failed", "unprocessed"]
    input_characters: int
    accepted_rows: int = 0
    reason: str | None = None
    fragments: list[str] = Field(default_factory=list)
    input_digest: str | None = None


class RequirementMetadata(DomainRecord):
    schema_version: str = SCHEMA_VERSION
    ontology_version: str
    session_id: str | None = None
    extraction_status: ExtractionStatus = "complete"
    batches: list[BatchReport] = Field(default_factory=list)
    processed_source_ids: list[str] = Field(default_factory=list)


class Objective(Fact):
    concept: Literal["Objective"] = "Objective"
    kind: str = Field(min_length=1)
    description: str


class RequirementSemanticModel(DomainRecord):
    schema_version: Literal["1.0.0", "2.0.0", "2.1.0"] = SCHEMA_VERSION
    ontology_version: str
    vocabulary_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_refs: list[SourceRef] = Field(default_factory=list)
    input_digest: str
    content_digest: str = ""
    run_id: str
    parent_version_id: str | None = None
    metadata: RequirementMetadata | None = None
    material_states: list[MaterialState] = Field(default_factory=list)
    modeling_intents: list[Objective] = Field(default_factory=list)
    process_graph: ProcessGraph | None = None
    evidences: dict[str, Evidence] = Field(default_factory=dict)
    products: list[Product] = Field(default_factory=list)
    operations: list[Operation] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    relations: list[ProcessRelation] = Field(default_factory=list)
    unresolved_items: list[Issue] = Field(default_factory=list)
    validation: Validation = Field(default_factory=Validation)


# Existing API and template callers retain the same field names and can load v1 snapshots.
SemanticModel = RequirementSemanticModel
