from datetime import datetime, timezone
import uuid
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """SQLite returns naive timestamps for date-times stored in UTC."""
    impl = DateTime
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class AgentProfile(Base):
    __tablename__ = "agent_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_type: Mapped[str] = mapped_column(String(40), default="requirement_parsing", nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    base_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    api_key: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(150), default="", nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.1, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    json_mode: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_source_quote: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    business_rules: Mapped[str] = mapped_column(Text, default="", nullable=False)
    extraction_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class OutputTemplate(Base):
    __tablename__ = "output_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    template_type: Mapped[str] = mapped_column(String(40), default="jinja2", nullable=False)
    format: Mapped[str] = mapped_column(String(40), default="markdown", nullable=False)
    jinja_content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    table_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class TestSession(Base):
    __tablename__ = "test_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200), default="未命名测试会话", nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_extracted_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    files: Mapped[list["UploadedFile"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="UploadedFile.created_at"
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_sessions.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_ext: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    parsed_preview: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, nullable=False)

    session: Mapped["TestSession"] = relationship(back_populates="files")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("test_sessions.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    file_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    extracted_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    debug_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, nullable=False)

    session: Mapped["TestSession"] = relationship(back_populates="messages")


class SemanticVersion(Base):
    __tablename__ = "semantic_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("test_sessions.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, nullable=False)


class TemplateRevision(Base):
    __tablename__ = "template_revisions"

    template_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, nullable=False)


class AgentExecutionSettings(Base):
    __tablename__ = "agent_execution_settings"

    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)


class ResourceMatchingRun(Base):
    __tablename__ = "resource_matching_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("test_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    semantic_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agent_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    agent_type: Mapped[str] = mapped_column(String(40), default="resource_matching", nullable=False)
    llm_mode: Mapped[str] = mapped_column(String(24), default="disabled", nullable=False)
    method: Mapped[str] = mapped_column(String(10), default="C", nullable=False)
    matching_status: Mapped[str] = mapped_column(String(32), default="completed", nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), default=_utcnow, nullable=False)
