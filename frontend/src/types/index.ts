export interface SchemaFieldDef {
  name: string;
  type: "string" | "number" | "boolean" | "array" | "object";
  description: string;
  required: boolean;
  children: SchemaFieldDef[];
}

export interface ExtractionSchemaConfig {
  mode: "visual" | "json_schema";
  fields: SchemaFieldDef[];
  json_schema: Record<string, unknown>;
}

export type AgentType = "requirement_parsing" | "resource_matching";
export type FallbackPolicy = "degrade_deterministic" | "fail_fast";
export type LLMMode = "disabled" | "enhanced";

export type SemanticThinking = "service_default" | "disabled";
export type ThinkingParameter = "thinking" | "reasoning_effort";
export type ThinkingResult = "reasoning_returned" | "zero_reported" | "unverified" | "rejected";

export interface AgentExecutionConfig {
  timeout_seconds: number | null;
  request_budget: number | null;
  batch_max_tokens: number | null;
  semantic_thinking: SemanticThinking | null;
  thinking_parameter: ThinkingParameter;
  llm_disambiguation: boolean;
  fallback_policy?: FallbackPolicy;
  max_strategy_retries?: number;
  default_llm_mode?: LLMMode;
}

export interface AgentRoleSpec {
  agent_type: AgentType;
  display_name: string;
  description: string;
  input_contract: string;
  output_contract: string;
  prompt_version: string;
  tools: { id: string; input: string; output: string }[];
}

export interface AgentProfile {
  agent_type: AgentType;
  execution: AgentExecutionConfig;
  id: string;
  name: string;
  description: string;
  base_url: string;
  model: string;
  temperature: number;
  max_tokens: number;
  json_mode: boolean;
  require_source_quote: boolean;
  system_prompt: string;
  business_rules: string;
  extraction_schema: ExtractionSchemaConfig;
  api_key_masked: string;
  has_custom_api_key: boolean;
  using_env_api_key: boolean;
  effective_base_url: string;
  effective_model: string;
  created_at: string;
  updated_at: string;
}

export interface AgentProfilePayload {
  agent_type?: AgentType;
  execution?: AgentExecutionConfig;
  name: string;
  description: string;
  base_url: string;
  api_key?: string;
  model: string;
  temperature: number;
  max_tokens: number;
  json_mode: boolean;
  require_source_quote: boolean;
  system_prompt: string;
  business_rules: string;
  extraction_schema: ExtractionSchemaConfig;
}

export interface TableColumnMapping {
  header: string;
  path: string;
  fallback: string;
}

export interface TableConfig {
  row_source_path: string;
  columns: TableColumnMapping[];
  include_summary_sheet: boolean;
}

export interface OutputTemplate {
  version: number;
  schema_version: string;
  id: string;
  name: string;
  description: string;
  template_type: "jinja2" | "table_mapping";
  format: "markdown" | "html" | "text" | "table";
  jinja_content: string;
  table_config: TableConfig;
  created_at: string;
  updated_at: string;
}

export interface OutputTemplatePayload {
  name: string;
  description: string;
  template_type: "jinja2" | "table_mapping";
  format: "markdown" | "html" | "text" | "table";
  jinja_content: string;
  table_config: TableConfig;
}

export interface TemplatePreviewResponse {
  template_id: string;
  template_type: string;
  format: string;
  rendered_text: string;
  table_headers: string[];
  table_rows: string[][];
  cell_states?: string[][];
  mapping_issues?: string[];
  row_ids?: (string | null)[];
  error?: string | null;
}

export interface UploadedFile {
  id: string;
  session_id: string;
  filename: string;
  file_ext: string;
  file_size: number;
  parse_status: "success" | "error";
  parse_error?: string | null;
  parsed_text: string;
  parsed_preview: Record<string, unknown>;
  created_at: string;
}

interface TokenUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  reasoning_tokens?: number;
  total_tokens?: number;
}

interface ProviderAttempt {
  batch_id?: string;
  stage?: string;
  max_tokens?: number;
  request_count?: number;
  finish_reason?: string | null;
  error_code?: string;
  thinking?: "disabled";
  thinking_parameter?: ThinkingParameter;
  thinking_result?: ThinkingResult;
  usage?: TokenUsage;
}

export interface DebugInfo {
  pipeline_stages?: PipelineStage[];
  batches?: ExtractionBatch[];
  extraction_status?: ExtractionStatus;
  tool_trace?: PipelineStage[];
  strategy?: "semantic" | "legacy" | "manual_review";
  error_code?: string;
  finish_reason?: string | null;
  requested_model?: string;
  request_count?: number;
  request_budget?: number;
  max_tokens?: number;
  batch_max_tokens?: number;
  prompt_version?: string;
  provider_attempts?: ProviderAttempt[];
  resumed_from_version_id?: string;
  input_digest?: string;
  activation_note?: string;
  system_prompt?: string;
  user_content?: string;
  raw_response?: string;
  validation_errors?: string[];
  duration_ms?: number;
  usage?: TokenUsage;
  model_used?: string;
  base_url?: string;
  error_message?: string | null;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  file_ids: string[];
  agent_id?: string | null;
  agent_name?: string | null;
  status: "success" | "partial" | "error";
  extracted_json?: Record<string, unknown> | null;
  debug_info?: DebugInfo | null;
  created_at: string;
}

export interface TestSessionSummary {
  id: string;
  title: string;
  agent_id?: string | null;
  template_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TestSessionDetail extends TestSessionSummary {
  current_extracted_json?: Record<string, unknown> | null;
  files: UploadedFile[];
  messages: ChatMessage[];
}

export interface RunChatResponse {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  current_extracted_json?: Record<string, unknown> | null;
  template_preview?: TemplatePreviewResponse | null;
}

export interface SampleDataset {
  id: string;
  title: string;
  description: string;
  kind: "text" | "csv";
  filename?: string | null;
  content: string;
}

export interface AgentConnectionTestPayload {
  agent_id?: string | null;
  base_url: string;
  api_key?: string;
  clear_api_key?: boolean;
  model: string;
  json_mode?: boolean;
}

export interface AgentConnectionTestResponse {
  error_code?: string | null;
  ok: boolean;
  message: string;
  latency_ms: number;
  status_code?: number | null;
  effective_base_url: string;
  effective_model: string;
  json_mode_supported?: boolean | null;
  available_models: string[];
}

export interface AgentJsonProbePayload extends AgentConnectionTestPayload {
  max_tokens: number;
  execution: AgentExecutionConfig;
}

export interface AgentJsonProbeResponse {
  ok: boolean;
  message: string;
  max_tokens: number;
  finish_reason?: string | null;
  error_code?: string | null;
  thinking_parameter?: ThinkingParameter | null;
  thinking_result?: ThinkingResult | null;
  usage: TokenUsage;
}

export interface AgentModelsResponse {
  models: string[];
  effective_base_url: string;
  error?: string | null;
}

export interface EnvStatus {
  has_default_api_key: boolean;
  default_base_url: string;
  default_model: string;
  semantic_batch_max_tokens: number;
  semantic_request_budget: number;
  deepseek_semantic_batch_max_tokens: number;
  deepseek_semantic_request_budget: number;
}

export type ReviewState = "verified" | "pending" | "conflict" | "invalid" | "unprocessed";
export type SemanticGroup = "products" | "operations" | "materials" | "material_states" | "capabilities" | "constraints" | "objectives";

export interface Evidence {
  document_id: string;
  fragment_id: string;
  quote: string;
  location: {
    sheet?: string | null;
    row?: number | null;
    cell?: string | null;
    page?: number | null;
    line?: number | null;
    paragraph?: number | null;
    message_id?: string | null;
    start?: number | null;
    end?: number | null;
  };
  extraction_batch: string;
  method?: "legacy" | "structured" | "rule" | "llm" | "manual";
  status: ReviewState;
}

export interface SemanticEntity {
  id: string;
  name: string;
  concept: string;
  status: ReviewState;
  evidence: Evidence[];
  review?: "confirmed" | "unreviewed";
  review_evidence?: Evidence[];
  standard_term?: string | null;
  mapping_basis?: string | null;
  material_id?: string;
  input_state_ids?: string[];
  output_state_ids?: string[];
  number?: string | null;
  operation_type?: string | null;
  precursor_declaration?: string[] | null;
  predecessor_ids?: string[];
  input_material_ids?: string[];
  output_material_ids?: string[];
  capability_ids?: string[];
  constraint_ids?: string[];
  component_ids?: string[];
  quantity?: number | null;
  part_number?: string | null;
  raw_names?: string[];
  equipment_requirement?: string | null;
  equipment_evidence?: Evidence[];
  information_flows?: { name: string; direction: "input" | "output"; status: ReviewState; evidence: Evidence[] }[];
  code?: string | null;
  mapping?: string;
  candidates?: string[];
  operation_ids?: string[];
  parameter_constraint_ids?: string[];
  consumed_by?: string[];
  produced_by?: string[];
  material_type?: string | null;
  target_id?: string | null;
  parameter_code?: string | null;
  related_parameter_codes?: string[];
  comparator?: string | null;
  value?: number | null;
  lower?: number | null;
  upper?: number | null;
  unit?: string | null;
  basis?: string | null;
  description?: string | null;
  constraint_type?: string | null;
  strength?: string | null;
}

export interface SemanticRelation {
  id: string;
  type: string;
  source_id: string;
  target_id: string;
  condition?: string | null;
  max_iterations?: number | null;
  quantity?: QuantitySpecification | null;
  asserted_as?: "explicit" | "inferred";
  review?: "confirmed" | "unreviewed";
  review_evidence?: Evidence[];
  evidence: Evidence[];
  status: ReviewState;
}

export interface SemanticIssue {
  code: string;
  message: string;
  path: string;
  status: ReviewState;
}

export type ModelingScope = "production_line" | "workstation" | "operation" | "product";
export interface ModelingTask {
  task_type: "digital_twin_modeling";
  target_scope: ModelingScope | null;
  target_product_id: string | null;
  required_outputs: "digital_twin_model"[];
  objective_ids: string[];
  constraint_ids: string[];
  review_evidence: Evidence[];
}
export interface SemanticObjective {
  id: string;
  kind: string;
  description: string;
  status?: ReviewState;
  review?: SemanticEntity["review"];
  evidence: Evidence[];
  review_evidence?: Evidence[];
}

export interface SemanticModel extends Record<Exclude<SemanticGroup, "objectives">, SemanticEntity[]> {
  schema_version: string;
  ontology_version: string;
  vocabulary_version: string;
  run_id: string;
  parent_version_id: string | null;
  content_digest: string;
  input_digest: string;
  metadata?: { extraction_status: ExtractionStatus; batches: ExtractionBatch[] } | null;
  modeling_intents?: SemanticObjective[];
  modeling_task?: ModelingTask | null;
  process_graph?: ProcessGraph | null;
  source_refs: { document_id: string; name: string; kind: string }[];
  relations: SemanticRelation[];
  unresolved_items: SemanticIssue[];
  validation: { status: ReviewState; issues: SemanticIssue[] };
}

export interface SemanticVersion {
  id: string;
  created_at: string;
  status: ReviewState;
  parent_version_id: string | null;
}

export interface PipelineStage {
  stage: string;
  status: string;
  summary: string;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number;
  input_summary?: string;
  batch_id?: string | null;
  error_code?: string | null;
}

export function semanticFrom(data: Record<string, unknown> | null | undefined) {
  const model = data?.semantic as SemanticModel | undefined;
  return model && (model.material_states ? model : { ...model, material_states: [] }) || null;
}


export type ExtractionStatus = "complete" | "partial" | "failed";
export interface ExtractionBatch {
  id: string;
  scope: string;
  status: "complete" | "partial" | "failed" | "unprocessed";
  input_characters: number;
  accepted_rows: number;
  reason?: string | null;
  fragments?: string[];
}
export interface QuantitySpecification { value: number; unit: string; basis?: string }
export interface ProcessGraph {
  nodes: { id: string; number?: string | null; name: string; category: string; layer?: number | null }[];
  edges: { id: string; source: string; target: string; kind: string; direct: boolean;
    condition?: string | null; max_iterations?: number | null; status: ReviewState;
    evidence: Evidence[]; relation_ids: string[] }[];
  issues: SemanticIssue[];
}

export type HandoffStatus = "ready" | "ready_with_warnings" | "blocked";
export interface HandoffIssue extends SemanticIssue { severity: "blocker" | "warning"; operation_ids: string[] }
export interface OperationResourceRequirement {
  operation_id: string;
  operation_name: string;
  operation_number: string | null;
  operation_type: string | null;
  standard_term: string | null;
  required_capability_codes: string[];
  capability_ids: string[];
  constraint_ids: string[];
  input_material_ids: string[];
  output_material_ids: string[];
  input_state_ids: string[];
  output_state_ids: string[];
  predecessor_ids: string[];
  information_flows: NonNullable<SemanticEntity["information_flows"]>;
  equipment_requirement: string | null;
  equipment_evidence: Evidence[];
  source_status: ReviewState;
  review: NonNullable<SemanticEntity["review"]>;
  evidence: Evidence[];
  review_evidence: Evidence[];
  has_blocking_issues: boolean;
}
export interface ResourceMatchingInput {
  contract_version: "1.0.0";
  source_model: Pick<SemanticModel, "run_id" | "schema_version" | "ontology_version" | "vocabulary_version" | "content_digest"> & {
    extraction_status: ExtractionStatus | null;
    semantic_validation_status: ReviewState;
  };
  modeling_task: ModelingTask | null;
  target_product_ids: string[];
  line_constraint_ids: string[];
  operation_requirements: OperationResourceRequirement[];
  process_graph: ProcessGraph;
  products: Record<string, SemanticEntity>;
  materials: Record<string, SemanticEntity>;
  material_states: Record<string, SemanticEntity>;
  capabilities: Record<string, SemanticEntity>;
  constraints: Record<string, SemanticEntity>;
  objectives: Record<string, SemanticObjective>;
  relations: SemanticRelation[];
  source_refs: SemanticModel["source_refs"];
  unresolved_items: SemanticIssue[];
  handoff_validation: { status: HandoffStatus; issues: HandoffIssue[] };
}

export type ContractKind = "candidates" | "formal" | "handoff" | "model_asset" | "matching_output";
export interface ContractSchema {
  title?: string;
  description?: string;
  type?: string;
  const?: string;
  enum?: string[];
  $ref?: string;
  anyOf?: ContractSchema[];
  items?: ContractSchema;
  additionalProperties?: ContractSchema | boolean;
  properties?: Record<string, ContractSchema>;
  required?: string[];
  $defs?: Record<string, ContractSchema>;
}

export interface OntologyTerm {
  id: string; iri: string; label: string; definition: string; domain: string[]; range: string[]; subclass_of: string[];
}
export interface OntologyCatalog {
  ontology_version: string;
  classes: OntologyTerm[];
  object_properties: OntologyTerm[];
  data_properties: OntologyTerm[];
  core_concepts?: string[];
  core_relations?: string[];
  instance_concepts?: Record<string, string>;
  vocabulary: { capabilities: Record<string, string[]> };
}
export type ReviewRequest = { note: string } & (
  { action: "rename_operation"; operation_id: string; name: string }
  | { action: "set_component_quantity"; relation_id: string; quantity: QuantitySpecification }
  | { action: "confirm_capability"; capability_id: string; code: string }
  | { action: "add_transition"; source_id: string; target_id: string; type: string; condition?: string; max_iterations?: number }
  | { action: "confirm_fact"; fact_id: string }
  | { action: "set_modeling_task"; target_scope?: ModelingScope; target_product_id?: string }
);

// ===== Model Asset Library Types =====
export type AssetCategory =
  | "feeding" | "cleaning" | "adhesive" | "stacking" | "assembly"
  | "welding" | "inspection" | "transport" | "robot" | "auxiliary";

export type BindingStatus = "unbound" | "reserved" | "bound";
export type ModelFileFormat = "glb" | "gltf" | "step" | "stp" | "usd" | "obj" | "fbx" | "none";

export interface ModelAsset {
  schema_version: string;
  data_source: "synthetic" | "vendor_spec" | "calibrated" | "imported";
  instantiation_policy: {
    reusable: boolean;
    max_parallel_instances?: number | null;
    instance_id_pattern: string;
    notes?: string | null;
  };
  identity: {
    asset_id: string;
    name: string;
    category: AssetCategory;
    subcategory?: string | null;
    description: string;
    version: string;
    tags: string[];
  };
  capabilities: {
    code: string;
    name: string;
    description: string;
    qualitative_features: string[];
    is_primary: boolean;
  }[];
  material_compatibility: {
    supported_material_types: string[];
    input_material_types: string[];
    output_material_types: string[];
    specifications: {
      material_type: string;
      spec_name: string;
      min_value?: number | null;
      max_value?: number | null;
      unit?: string | null;
      note?: string | null;
    }[];
    input_states: string[];
    output_states: string[];
    state_transitions: {
      material_type?: string | null;
      from_state: string;
      to_state: string;
      condition?: string | null;
    }[];
    notes?: string | null;
  };
  parameters: {
    parameter_code: string;
    name: string;
    value?: number | null;
    lower?: number | null;
    upper?: number | null;
    unit: string;
    basis?: string | null;
    capability_code?: string | null;
    note?: string | null;
  }[];
  interfaces: {
    interface_id: string;
    name: string;
    interface_type: "material_flow" | "mechanical" | "electrical" | "pneumatic" | "information" | "control";
    direction: "input" | "output" | "bidirectional";
    compatible_conditions: string[];
    protocol?: string | null;
    payload_schema?: string | null;
    optional: boolean;
  }[];
  geometry?: {
    dimensions_mm?: { length: number; width: number; height: number } | null;
    coordinate_system?: string | null;
    mounting_datum?: string | null;
    connection_points: {
      point_id: string;
      interface_id?: string | null;
      position_mm: [number, number, number];
      normal?: [number, number, number] | null;
    }[];
    completeness: "complete" | "partial" | "placeholder";
  } | null;
  behavior?: {
    cycle_time_s?: number | null;
    cycle_time_basis?: string | null;
    batch_size?: number | null;
    operating_states: string[];
    behavior_model_ref?: string | null;
    behavior_model_type?: string | null;
    availability?: number | null;
  } | null;
  model_binding: {
    model_uri?: string | null;
    file_format?: ModelFileFormat | null;
    binding_status: BindingStatus;
    asset_ref_key?: string | null;
    checksum_sha256?: string | null;
    notes?: string | null;
  };
  provenance: {
    author: string;
    created_at: string;
    synthetic_disclaimer?: string | null;
    notes?: string | null;
  };
}

export interface AssetValidationIssue {
  code: string;
  severity: "error" | "warning";
  path: string;
  message: string;
}

export interface AssetValidationResult {
  asset_id?: string | null;
  valid: boolean;
  completeness_score: number;
  missing_fields: string[];
  issues: AssetValidationIssue[];
}

export interface AssetCatalogItem {
  asset: ModelAsset;
  validation: AssetValidationResult | null;
}

export interface AssetCatalogListResponse {
  catalog_version: string;
  catalog_digest: string;
  total: number;
  items: AssetCatalogItem[];
}

export interface AssetCatalogSummary {
  schema_version: string;
  catalog_version: string;
  catalog_digest: string;
  total_assets: number;
  valid_assets: number;
  by_category: { code: string; label: string; count: number }[];
  by_capability: Record<string, number>;
  by_binding_status: Record<string, number>;
  by_data_source: Record<string, number>;
}

// ===== Resource Matching Agent Types =====
export type CheckVerdict = "PASS" | "FAIL" | "UNKNOWN";
export type MatchingMethod = "A" | "B" | "C";

export interface ScoringWeights {
  functional_fit: number;
  material_fit: number;
  technical_performance: number;
  modeling_completeness: number;
}

export interface ResourceMatchingConfig {
  top_k: number;
  partial_top_k: number;
  method: MatchingMethod;
  llm_mode: LLMMode;
  use_llm: boolean;
  fallback_policy: FallbackPolicy;
  max_llm_calls: number;
  max_strategy_retries: number;
  allow_warnings_input: boolean;
  force_blocked_diagnostic: boolean;
  include_rejected_details: boolean;
  weights: ScoringWeights;
}

export interface AgentRunMetadata {
  agent_type: AgentType;
  agent_id?: string | null;
  agent_name?: string | null;
  model_requested?: string | null;
  model_used?: string | null;
  prompt_version: string;
  input_version?: string | null;
  execution_mode: string;
  config_snapshot: Record<string, unknown>;
  tool_calls_count: number;
  llm_calls: number;
  llm_retries: number;
  fallback_triggered: boolean;
  fallback_reason?: string | null;
  elapsed_ms: number;
}

export interface LLMStrategyRecord {
  triggered: boolean;
  trigger_reasons: string[];
  proposed_asset_ids: string[];
  verified_added_asset_ids: string[];
  rejected_hallucinated_ids: string[];
  rejected_hard_constraint_ids: string[];
  preferred_categories: string[];
  capability_decomposition_hint: string[][];
  qualitative_refinements: string[];
  retries_used: number;
}

export interface StructuredFeedbackItem {
  feedback_id: string;
  issue_source: "upstream_requirement" | "asset_catalog_gap" | "constraint_ambiguity" | "llm_runtime";
  severity: "blocker" | "warning" | "info";
  operation_ids: string[];
  message: string;
  suggested_action: string;
  mutates_upstream: false;
}

export interface ConstraintCheckItem {
  check_id: string;
  scope: "operation" | "capability" | "line" | "material" | "state" | "interface" | "equipment";
  constraint_id?: string | null;
  parameter_code?: string | null;
  is_hard: boolean;
  verdict: CheckVerdict;
  required_summary: string;
  asset_summary: string;
  rationale: string;
}

export interface ScoreBreakdown {
  functional_fit: number;
  material_fit: number;
  technical_performance: number;
  modeling_completeness: number;
  total_score: number;
}

export interface CandidateEvaluation {
  asset_id: string;
  asset_name: string;
  asset_category: string;
  data_source: string;
  binding_status: string;
  disposition: "qualified" | "partial_qualified" | "pending_confirmation" | "rejected";
  rank?: number | null;
  covered_capabilities: string[];
  missing_capabilities: string[];
  capability_coverage: number;
  hard_constraint_verdict: CheckVerdict;
  constraint_checks: ConstraintCheckItem[];
  score_breakdown: ScoreBreakdown;
  match_reasons: string[];
  rejection_reasons: string[];
  missing_information: string[];
  line_takt_reference?: string | null;
}

export interface CapabilityPartialCandidates {
  capability_code: string;
  capability_name?: string | null;
  candidates: CandidateEvaluation[];
  pending_candidates: CandidateEvaluation[];
  unmet: boolean;
}

export interface UnmetRequirement {
  code: string;
  scope: string;
  message: string;
  capability_codes: string[];
}

export interface OperationMatchingResult {
  operation_id: string;
  operation_number?: string | null;
  operation_name: string;
  operation_type?: string | null;
  required_capability_codes: string[];
  input_materials: string[];
  output_materials: string[];
  input_states: string[];
  output_states: string[];
  information_flows: string[];
  equipment_requirement?: string | null;
  operation_constraint_ids: string[];
  capability_constraint_ids: string[];
  match_status: "matched_single" | "matched_partial_composable" | "pending_only" | "unmatched" | "blocked";
  multi_asset_recommended: boolean;
  top_k_candidates: CandidateEvaluation[];
  partial_candidates_by_capability: CapabilityPartialCandidates[];
  pending_candidates: CandidateEvaluation[];
  rejected_candidates: CandidateEvaluation[];
  unmet_requirements: UnmetRequirement[];
  llm_strategy?: LLMStrategyRecord | null;
  llm_explanation?: string | null;
  diagnostics: string[];
}

export interface LineConstraintReference {
  constraint_id: string;
  name: string;
  parameter_code?: string | null;
  comparator?: string | null;
  value?: number | null;
  lower?: number | null;
  upper?: number | null;
  unit?: string | null;
  basis?: string | null;
  strength: string;
  description?: string | null;
  note: string;
}

export interface MatchingSummary {
  total_operations: number;
  matched_single_count: number;
  matched_partial_count: number;
  pending_only_count: number;
  unmatched_count: number;
  blocked_count: number;
  average_top1_capability_coverage: number;
  total_duration_ms: number;
  llm_calls: number;
  llm_retries?: number;
  llm_model_used?: string | null;
  fallback_triggered?: boolean;
  fallback_reason?: string | null;
}

export interface ResourceMatchingOutput {
  contract_version: "1.0.0";
  run_id: string;
  created_at: string;
  source_requirement_version: string;
  source_content_digest: string;
  source_handoff_status: HandoffStatus;
  catalog_version: string;
  catalog_digest: string;
  ontology_version: string;
  vocabulary_version: string;
  config: ResourceMatchingConfig;
  run_metadata?: AgentRunMetadata | null;
  modeling_task?: ModelingTask | null;
  target_product_ids: string[];
  line_constraints: LineConstraintReference[];
  process_graph: ProcessGraph;
  relations: SemanticRelation[];
  operation_matches: OperationMatchingResult[];
  matching_status: "completed" | "completed_with_warnings" | "partial" | "blocked";
  summary: MatchingSummary;
  diagnostics: { code: string; severity: "blocker" | "warning" | "info"; operation_id?: string | null; message: string }[];
  feedback_items?: StructuredFeedbackItem[];
  tool_trace: { step: string; tool: string; status: string; duration_ms: number; summary: string }[];
}

export interface MatchingRunSummary {
  run_id: string;
  session_id?: string | null;
  semantic_version_id?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
  agent_type?: AgentType;
  llm_mode?: LLMMode;
  method: MatchingMethod;
  matching_status: string;
  catalog_version: string;
  top_k: number;
  summary?: MatchingSummary | null;
  created_at: string;
}
