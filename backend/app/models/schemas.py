from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.llm_extractor import SemanticThinking, ThinkingParameter, ThinkingResult, normalize_base_url
from domain_semantics.schema import SCHEMA_VERSION


def mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * min(len(key) - 8, 12)}{key[-4:]}"


class SchemaFieldDef(BaseModel):
    name: str
    type: str = "string"  # string | number | boolean | array | object
    description: str = ""
    required: bool = False
    children: list["SchemaFieldDef"] = Field(default_factory=list)


class ExtractionSchemaConfig(BaseModel):
    mode: Literal["visual", "json_schema"] = "visual"
    fields: list[SchemaFieldDef] = Field(default_factory=list)
    json_schema: dict[str, Any] = Field(default_factory=dict)


class AgentExecutionConfig(BaseModel):
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    request_budget: int | None = Field(default=None, ge=1, le=32)
    batch_max_tokens: int | None = Field(default=None, ge=64, le=128000)
    semantic_thinking: SemanticThinking | None = None
    thinking_parameter: ThinkingParameter = "thinking"
    llm_disambiguation: bool = True


class AgentProfileBase(BaseModel):
    execution: AgentExecutionConfig = Field(default_factory=AgentExecutionConfig)
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=64, le=128000)
    json_mode: bool = True
    require_source_quote: bool = True
    system_prompt: str = ""
    business_rules: str = ""
    extraction_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "model", mode="before")
    @classmethod
    def _strip_text(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_url(cls, v: Any) -> Any:
        return normalize_base_url(v) if isinstance(v, str) else v


class AgentProfileCreate(AgentProfileBase):
    api_key: str = ""

    @field_validator("extraction_schema", mode="before")
    @classmethod
    def _validate_schema(cls, v: Any) -> dict[str, Any]:
        if not v:
            return ExtractionSchemaConfig().model_dump()
        return ExtractionSchemaConfig.model_validate(v).model_dump()


class AgentProfileUpdate(BaseModel):
    execution: AgentExecutionConfig | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # None means keep existing; "" clears custom key to use .env
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=64, le=128000)
    json_mode: bool | None = None
    require_source_quote: bool | None = None
    system_prompt: str | None = None
    business_rules: str | None = None
    extraction_schema: dict[str, Any] | None = None

    @field_validator("name", "model", mode="before")
    @classmethod
    def _strip_opt_text(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_opt_url(cls, v: Any) -> Any:
        return normalize_base_url(v) if isinstance(v, str) else v

    @field_validator("extraction_schema", mode="before")
    @classmethod
    def _validate_opt_schema(cls, v: Any) -> dict[str, Any] | None:
        if v is None:
            return None
        return ExtractionSchemaConfig.model_validate(v).model_dump()


class AgentProfileOut(AgentProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    api_key_masked: str = ""
    has_custom_api_key: bool = False
    using_env_api_key: bool = False
    effective_base_url: str = ""
    effective_model: str = ""
    created_at: datetime
    updated_at: datetime


class AgentConnectionTestRequest(BaseModel):
    agent_id: str | None = None
    base_url: str = ""
    api_key: str | None = None
    clear_api_key: bool = False
    model: str = ""
    json_mode: bool = True


class AgentConnectionTestResponse(BaseModel):
    error_code: str | None = None
    ok: bool
    message: str
    latency_ms: int = 0
    status_code: int | None = None
    effective_base_url: str = ""
    effective_model: str = ""
    json_mode_supported: bool | None = None
    available_models: list[str] = Field(default_factory=list)


class AgentJsonProbeRequest(AgentConnectionTestRequest):
    max_tokens: int = Field(default=4096, ge=64, le=128000)
    execution: AgentExecutionConfig = Field(default_factory=AgentExecutionConfig)


class AgentJsonProbeResponse(BaseModel):
    ok: bool
    message: str
    max_tokens: int
    finish_reason: str | None = None
    error_code: str | None = None
    thinking_parameter: ThinkingParameter | None = None
    thinking_result: ThinkingResult | None = None
    usage: dict[str, int] = Field(default_factory=dict)


class AgentModelsResponse(BaseModel):
    models: list[str] = Field(default_factory=list)
    effective_base_url: str = ""
    error: str | None = None


class TableColumnMapping(BaseModel):
    header: str
    path: str
    fallback: str = ""


class TableConfig(BaseModel):
    row_source_path: str = "operations"
    columns: list[TableColumnMapping] = Field(default_factory=list)
    include_summary_sheet: bool = True


class OutputTemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    template_type: Literal["jinja2", "table_mapping"] = "jinja2"
    format: Literal["markdown", "html", "text", "table"] = "markdown"
    jinja_content: str = ""
    table_config: dict[str, Any] = Field(default_factory=dict)


class OutputTemplateCreate(OutputTemplateBase):
    pass


class OutputTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    template_type: Literal["jinja2", "table_mapping"] | None = None
    format: Literal["markdown", "html", "text", "table"] | None = None
    jinja_content: str | None = None
    table_config: dict[str, Any] | None = None


class OutputTemplateOut(OutputTemplateBase):
    version: int = 1
    schema_version: str = SCHEMA_VERSION
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime


class TemplatePreviewRequest(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)


class TemplateDraftPreviewRequest(BaseModel):
    template_type: Literal["jinja2", "table_mapping"] = "jinja2"
    format: Literal["markdown", "html", "text", "table"] = "markdown"
    jinja_content: str = ""
    table_config: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)


class TemplatePreviewResponse(BaseModel):
    template_id: str
    template_type: str
    format: str
    rendered_text: str = ""
    table_headers: list[str] = Field(default_factory=list)
    table_rows: list[list[str]] = Field(default_factory=list)
    table_values: list[list[str | int | float]] = Field(default_factory=list, exclude=True)
    cell_states: list[list[str]] = Field(default_factory=list)
    row_ids: list[str | None] = Field(default_factory=list)
    mapping_issues: list[str] = Field(default_factory=list)
    error: str | None = None


class TemplateExportRequest(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)
    export_format: Literal["xlsx", "docx", "md", "json"] = "xlsx"
    filename_prefix: str = "process_output"


class UploadedFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    filename: str
    file_ext: str
    file_size: int
    parse_status: str
    parse_error: str | None = None
    parsed_text: str
    parsed_preview: dict[str, Any]
    created_at: datetime


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: str
    content: str
    file_ids: list[str]
    agent_id: str | None = None
    agent_name: str | None = None
    status: str
    extracted_json: dict[str, Any] | None = None
    debug_info: dict[str, Any] | None = None
    created_at: datetime


class TestSessionCreate(BaseModel):
    title: str = "新测试会话"
    agent_id: str | None = None
    template_id: str | None = None


class TestSessionUpdate(BaseModel):
    title: str | None = None
    agent_id: str | None = None
    template_id: str | None = None
    current_extracted_json: dict[str, Any] | None = None


class TestSessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    agent_id: str | None = None
    template_id: str | None = None
    created_at: datetime
    updated_at: datetime


class TestSessionDetail(TestSessionSummary):
    current_extracted_json: dict[str, Any] | None = None
    files: list[UploadedFileOut] = Field(default_factory=list)
    messages: list[ChatMessageOut] = Field(default_factory=list)


class RunChatRequest(BaseModel):
    message: str = Field(default="", max_length=200_000)
    file_ids: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    template_id: str | None = None
    use_demo_fallback: bool = False
    base_version_id: str | None = None
    run_token: UUID | None = None
    strategy: Literal["semantic", "legacy"] = "semantic"


class RunChatResponse(BaseModel):
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut
    current_extracted_json: dict[str, Any] | None = None
    template_preview: TemplatePreviewResponse | None = None
