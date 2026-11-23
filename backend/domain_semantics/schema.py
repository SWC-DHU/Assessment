"""The domain contract is independent of extraction, persistence and display."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0.0"
ReviewState = Literal["verified", "pending", "conflict", "invalid", "unprocessed"]
RelationKind = Literal[
    "hasComponent", "hasPredecessor", "consumes", "produces", "requiresCapability",
    "appliesTo", "branchesTo", "mergesInto", "reworksTo", "conditionallyPrecedes",
]
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
    status: ReviewState = "unprocessed"


class Fact(DomainRecord):
    id: str = Field(min_length=1)
    evidence: list[Evidence] = Field(default_factory=list)
    status: ReviewState = "unprocessed"


class Entity(Fact):
    name: str = Field(min_length=1)


class Product(Entity):
    concept: Literal["Product"] = "Product"
    part_number: str | None = None
    component_ids: list[str] = Field(default_factory=list)
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = None


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
    equipment_requirement: str | None = None
    equipment_evidence: list[Evidence] = Field(default_factory=list)


class Material(Entity):
    concept: Literal["Material"] = "Material"
    material_type: str | None = None
    consumed_by: list[str] = Field(default_factory=list)
    produced_by: list[str] = Field(default_factory=list)


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
    comparator: Comparator | None = None
    value: float | None = None
    lower: float | None = None
    upper: float | None = None
    unit: str | None = None
    basis: str | None = None
    strength: Literal["mandatory", "preferred", "unspecified"] = "unspecified"
    description: str | None = None


class ProcessRelation(Fact):
    concept: Literal["ProcessRelation"] = "ProcessRelation"
    type: RelationKind
    source_id: str
    target_id: str
    condition: str | None = None
    max_iterations: int | None = Field(default=None, ge=1)
    origin: Literal["document", "model", "user"]


class Issue(DomainRecord):
    code: str
    message: str
    path: str
    status: ReviewState


class Validation(DomainRecord):
    status: ReviewState = "unprocessed"
    issues: list[Issue] = Field(default_factory=list)


class SemanticModel(DomainRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    ontology_version: str
    vocabulary_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_refs: list[SourceRef] = Field(default_factory=list)
    input_digest: str
    content_digest: str = ""
    run_id: str
    parent_version_id: str | None = None
    products: list[Product] = Field(default_factory=list)
    operations: list[Operation] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    relations: list[ProcessRelation] = Field(default_factory=list)
    unresolved_items: list[Issue] = Field(default_factory=list)
    validation: Validation = Field(default_factory=Validation)
