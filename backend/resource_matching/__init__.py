"""Resource Matching Agent module (reusable & decoupled for Metatwin migration)."""
from .agent import run_resource_matching
from .constraints import build_line_takt_reference, evaluate_asset_constraints
from .ranking import compute_score_breakdown, rank_evaluations
from .retrieval import dereference_input, retrieve_keyword_candidates, retrieve_semantic_candidates
from .schema import (
    AgentTraceStep,
    CandidateDisposition,
    CandidateEvaluation,
    CapabilityPartialCandidates,
    CheckVerdict,
    ConstraintCheckItem,
    LineConstraintReference,
    MatchingDiagnostic,
    MatchingMethod,
    MatchingSummary,
    OperationMatchStatus,
    OperationMatchingResult,
    OverallMatchingStatus,
    RESOURCE_MATCHING_OUTPUT_VERSION,
    ResourceMatchingConfig,
    ResourceMatchingOutput,
    ScoreBreakdown,
    ScoringWeights,
    UnmetRequirement,
)
from .tools import MATCHING_TOOL_CATALOG, list_matching_tools

__all__ = [
    "AgentTraceStep",
    "CandidateDisposition",
    "CandidateEvaluation",
    "CapabilityPartialCandidates",
    "CheckVerdict",
    "ConstraintCheckItem",
    "LineConstraintReference",
    "MATCHING_TOOL_CATALOG",
    "MatchingDiagnostic",
    "MatchingMethod",
    "MatchingSummary",
    "OperationMatchStatus",
    "OperationMatchingResult",
    "OverallMatchingStatus",
    "RESOURCE_MATCHING_OUTPUT_VERSION",
    "ResourceMatchingConfig",
    "ResourceMatchingOutput",
    "ScoreBreakdown",
    "ScoringWeights",
    "UnmetRequirement",
    "build_line_takt_reference",
    "compute_score_breakdown",
    "dereference_input",
    "evaluate_asset_constraints",
    "list_matching_tools",
    "rank_evaluations",
    "retrieve_keyword_candidates",
    "retrieve_semantic_candidates",
    "run_resource_matching",
]
