"""Candidate exchange format. The model can propose facts and source pointers only."""
from pydantic import Field

from .schema import Comparator, DomainRecord, RelationKind


class Citation(DomainRecord):
    quote: str = Field(min_length=1)
    document_id: str | None = None
    fragment_id: str | None = None


class NamedCandidate(DomainRecord):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    evidence: list[Citation] = Field(min_length=1)


class ProductCandidate(NamedCandidate):
    part_number: str | None = None
    quantity: float | None = None
    unit: str | None = None


class OperationCandidate(NamedCandidate):
    number: str | None = None
    operation_type: str | None = None
    equipment_requirement: str | None = None
    equipment_evidence: list[Citation] = Field(default_factory=list)


class MaterialCandidate(NamedCandidate):
    material_type: str | None = None


class ConstraintCandidate(NamedCandidate):
    target_id: str
    comparator: Comparator | None = None
    value: float | None = None
    lower: float | None = None
    upper: float | None = None
    unit: str | None = None
    basis: str | None = None
    description: str | None = None


class RelationCandidate(DomainRecord):
    type: RelationKind
    source_id: str
    target_id: str
    condition: str | None = None
    max_iterations: int | None = None
    evidence: list[Citation] = Field(min_length=1)


class ExtractionCandidates(DomainRecord):
    products: list[ProductCandidate] = Field(default_factory=list)
    operations: list[OperationCandidate] = Field(default_factory=list)
    materials: list[MaterialCandidate] = Field(default_factory=list)
    capabilities: list[NamedCandidate] = Field(default_factory=list)
    constraints: list[ConstraintCandidate] = Field(default_factory=list)
    relations: list[RelationCandidate] = Field(default_factory=list)
