"""Candidate exchange format. The model can propose facts and source pointers only."""
from pydantic import ConfigDict, Field

from .schema import Comparator, DomainRecord, QuantitySpecification, RelationKind


class CandidateRecord(DomainRecord):
    model_config = ConfigDict(strict=True)


class Citation(CandidateRecord):
    quote: str = Field(min_length=1)
    document_id: str | None = None
    fragment_id: str | None = None


class NamedCandidate(CandidateRecord):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    evidence: list[Citation] = Field(min_length=1)


class ProductCandidate(NamedCandidate):
    part_number: str | None = None
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = None


class OperationCandidate(NamedCandidate):
    number: str | None = None
    operation_type: str | None = None
    equipment_requirement: str | None = None
    equipment_evidence: list[Citation] = Field(default_factory=list)


class MaterialCandidate(NamedCandidate):
    material_type: str | None = None


class MaterialStateCandidate(NamedCandidate):
    material_id: str


class ConstraintCandidate(NamedCandidate):
    target_id: str
    comparator: Comparator | None = None
    value: float | None = None
    lower: float | None = None
    upper: float | None = None
    unit: str | None = None
    basis: str | None = None
    description: str | None = None


class CandidateQuantity(QuantitySpecification):
    model_config = ConfigDict(strict=True)


class RelationCandidate(CandidateRecord):
    type: RelationKind
    source_id: str
    target_id: str
    condition: str | None = None
    max_iterations: int | None = Field(default=None, ge=1)
    quantity: CandidateQuantity | None = None
    inferred: bool = False
    evidence: list[Citation] = Field(min_length=1)


class ExtractionCandidates(CandidateRecord):
    products: list[ProductCandidate] = Field(default_factory=list)
    operations: list[OperationCandidate] = Field(default_factory=list)
    materials: list[MaterialCandidate] = Field(default_factory=list)
    material_states: list[MaterialStateCandidate] = Field(default_factory=list)
    capabilities: list[NamedCandidate] = Field(default_factory=list)
    constraints: list[ConstraintCandidate] = Field(default_factory=list)
    objectives: list[NamedCandidate] = Field(default_factory=list)
    relations: list[RelationCandidate] = Field(default_factory=list)
