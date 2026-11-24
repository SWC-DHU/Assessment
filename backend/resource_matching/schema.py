"""Formal ResourceMatchingOutput contract and configuration schemas."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain_semantics.schema import ModelingTask, ProcessGraph, ProcessRelation

RESOURCE_MATCHING_OUTPUT_VERSION = "1.0.0"

CheckVerdict = Literal["PASS", "FAIL", "UNKNOWN"]
MatchingMethod = Literal["A", "B", "C"]
CandidateDisposition = Literal["qualified", "partial_qualified", "pending_confirmation", "rejected"]
OperationMatchStatus = Literal["matched_single", "matched_partial_composable", "pending_only", "unmatched", "blocked"]
OverallMatchingStatus = Literal["completed", "completed_with_warnings", "partial", "blocked"]


class MatchingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ScoringWeights(MatchingRecord):
    functional_fit: float = Field(default=0.35, ge=0.0, le=1.0, description="功能适配权重")
    material_fit: float = Field(default=0.30, ge=0.0, le=1.0, description="物料与状态适配权重")
    technical_performance: float = Field(default=0.20, ge=0.0, le=1.0, description="技术性能权重")
    modeling_completeness: float = Field(default=0.15, ge=0.0, le=1.0, description="建模信息完整性权重")

    @model_validator(mode="after")
    def check_positive_sum(self):
        total = self.functional_fit + self.material_fit + self.technical_performance + self.modeling_completeness
        if total <= 0:
            raise ValueError("评分权重总和必须大于 0")
        return self

    def normalized(self) -> tuple[float, float, float, float]:
        total = self.functional_fit + self.material_fit + self.technical_performance + self.modeling_completeness
        return (
            self.functional_fit / total,
            self.material_fit / total,
            self.technical_performance / total,
            self.modeling_completeness / total,
        )


class ResourceMatchingConfig(MatchingRecord):
    top_k: int = Field(default=3, ge=1, le=20, description="每道工序输出的 Top-k 完整覆盖单资产候选数")
    partial_top_k: int = Field(default=3, ge=1, le=10, description="按能力划分的部分资产候选 Top-k 数量")
    method: MatchingMethod = Field(default="C", description="匹配方法：A=关键词基线，B=直接LLM基线，C=本体语义增强+多约束推理")
    use_llm: bool = Field(default=False, description="方法C是否启用LLM辅助定性语义消歧与匹配解释")
    allow_warnings_input: bool = Field(default=True, description="是否允许消费 handoff_validation=ready_with_warnings 的输入")
    force_blocked_diagnostic: bool = Field(default=False, description="当输入为 blocked 时是否仍输出逐工序诊断而不中断")
    include_rejected_details: bool = Field(default=True, description="是否在输出中保留淘汰候选及淘汰原因")
    weights: ScoringWeights = Field(default_factory=ScoringWeights)


class ConstraintCheckItem(MatchingRecord):
    check_id: str
    scope: Literal["operation", "capability", "line", "material", "state", "interface", "equipment"]
    constraint_id: str | None = None
    parameter_code: str | None = None
    is_hard: bool
    verdict: CheckVerdict
    required_summary: str
    asset_summary: str
    rationale: str


class ScoreBreakdown(MatchingRecord):
    functional_fit: float = Field(ge=0.0, le=1.0)
    material_fit: float = Field(ge=0.0, le=1.0)
    technical_performance: float = Field(ge=0.0, le=1.0)
    modeling_completeness: float = Field(ge=0.0, le=1.0)
    total_score: float = Field(ge=0.0, le=1.0)


class CandidateEvaluation(MatchingRecord):
    asset_id: str
    asset_name: str
    asset_category: str
    data_source: str
    binding_status: str
    disposition: CandidateDisposition
    rank: int | None = Field(default=None, ge=1)
    covered_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    capability_coverage: float = Field(ge=0.0, le=1.0)
    hard_constraint_verdict: CheckVerdict
    constraint_checks: list[ConstraintCheckItem] = Field(default_factory=list)
    score_breakdown: ScoreBreakdown
    match_reasons: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    line_takt_reference: str | None = Field(default=None, description="整线节拍参考提示（不作为单机硬淘汰条件）")

    @model_validator(mode="after")
    def check_hard_constraint_invariant(self):
        if self.disposition in {"qualified", "partial_qualified"} and self.hard_constraint_verdict != "PASS":
            raise ValueError("违反硬约束或信息缺失的候选资产不得进入合格候选集")
        if self.disposition == "pending_confirmation" and self.hard_constraint_verdict == "FAIL":
            raise ValueError("明确违反硬约束的资产必须进入淘汰集，不得进入待确认集")
        return self


class CapabilityPartialCandidates(MatchingRecord):
    capability_code: str
    capability_name: str | None = None
    candidates: list[CandidateEvaluation] = Field(default_factory=list)
    pending_candidates: list[CandidateEvaluation] = Field(default_factory=list)
    unmet: bool = False


class UnmetRequirement(MatchingRecord):
    code: str
    scope: Literal["operation", "capability", "material", "constraint", "interface"]
    message: str
    capability_codes: list[str] = Field(default_factory=list)


class OperationMatchingResult(MatchingRecord):
    operation_id: str
    operation_number: str | None = None
    operation_name: str
    operation_type: str | None = None
    required_capability_codes: list[str] = Field(default_factory=list)
    input_materials: list[str] = Field(default_factory=list)
    output_materials: list[str] = Field(default_factory=list)
    input_states: list[str] = Field(default_factory=list)
    output_states: list[str] = Field(default_factory=list)
    information_flows: list[str] = Field(default_factory=list)
    equipment_requirement: str | None = None
    operation_constraint_ids: list[str] = Field(default_factory=list)
    capability_constraint_ids: list[str] = Field(default_factory=list)
    match_status: OperationMatchStatus
    multi_asset_recommended: bool = False
    top_k_candidates: list[CandidateEvaluation] = Field(default_factory=list)
    partial_candidates_by_capability: list[CapabilityPartialCandidates] = Field(default_factory=list)
    pending_candidates: list[CandidateEvaluation] = Field(default_factory=list)
    rejected_candidates: list[CandidateEvaluation] = Field(default_factory=list)
    unmet_requirements: list[UnmetRequirement] = Field(default_factory=list)
    llm_explanation: str | None = None
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_top_k_invariants(self):
        for item in self.top_k_candidates:
            if item.hard_constraint_verdict != "PASS" or item.disposition != "qualified":
                raise ValueError(f"工序 {self.operation_id} 的 top_k_candidates 包含不合格候选 {item.asset_id}")
        return self


class LineConstraintReference(MatchingRecord):
    constraint_id: str
    name: str
    parameter_code: str | None = None
    comparator: str | None = None
    value: float | None = None
    lower: float | None = None
    upper: float | None = None
    unit: str | None = None
    basis: str | None = None
    strength: str = "unspecified"
    description: str | None = None
    note: str = "整线级约束交由后续关联建模与产线平衡智能体进行并行工位配置和全局验证，不直接作为单台设备硬淘汰条件。"


class MatchingDiagnostic(MatchingRecord):
    code: str
    severity: Literal["blocker", "warning", "info"]
    operation_id: str | None = None
    message: str


class AgentTraceStep(MatchingRecord):
    step: str
    tool: str
    status: Literal["success", "skipped", "warning", "error"]
    duration_ms: float = Field(ge=0.0)
    summary: str


class MatchingSummary(MatchingRecord):
    total_operations: int = 0
    matched_single_count: int = 0
    matched_partial_count: int = 0
    pending_only_count: int = 0
    unmatched_count: int = 0
    blocked_count: int = 0
    average_top1_capability_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    total_duration_ms: float = Field(default=0.0, ge=0.0)
    llm_calls: int = 0
    llm_model_used: str | None = None


class ResourceMatchingOutput(MatchingRecord):
    """Formal downstream handoff contract from Resource Matching Agent to Association Modeling Agent."""
    contract_version: Literal["1.0.0"] = RESOURCE_MATCHING_OUTPUT_VERSION
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_requirement_version: str
    source_content_digest: str
    source_handoff_status: Literal["ready", "ready_with_warnings", "blocked"]
    catalog_version: str
    catalog_digest: str
    ontology_version: str
    vocabulary_version: str
    config: ResourceMatchingConfig
    modeling_task: ModelingTask | None = None
    target_product_ids: list[str] = Field(default_factory=list)
    line_constraints: list[LineConstraintReference] = Field(default_factory=list)
    process_graph: ProcessGraph
    relations: list[ProcessRelation] = Field(default_factory=list)
    operation_matches: list[OperationMatchingResult] = Field(default_factory=list)
    matching_status: OverallMatchingStatus
    summary: MatchingSummary = Field(default_factory=MatchingSummary)
    diagnostics: list[MatchingDiagnostic] = Field(default_factory=list)
    tool_trace: list[AgentTraceStep] = Field(default_factory=list)
