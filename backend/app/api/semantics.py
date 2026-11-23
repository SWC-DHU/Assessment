"""Read and project manufacturing semantic versions through the Assessment adapter."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.db_models import AgentProfile, ChatMessage, SemanticVersion, TestSession
from app.services.redaction import redact
from app.services.source_inputs import uploaded_document
from app.services.semantic_tools import TOOL_CATALOG
from domain_semantics import SemanticModel
from domain_semantics.catalog import ONTOLOGY_VERSION, normalize_term, query_ontology
from domain_semantics.process_graph import build_process_graph
from domain_semantics.review import ReviewRequest, apply_review
from domain_semantics.schema import ProcessGraph
from domain_semantics.pipeline import Builder, legacy_candidates, table_rows
from domain_semantics.projection import template_context
from domain_semantics.sources import locate, message_document
from domain_semantics.validation import COLLECTIONS, validate_model

router = APIRouter(prefix="/api/semantics", tags=["semantics"])


class ProjectRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list)
    message_id: str | None = None
    candidates: dict | None = None


def get_version(db: Session, session_id: str, version_id: str) -> SemanticVersion:
    version = db.get(SemanticVersion, version_id)
    if not version or version.session_id != session_id:
        raise HTTPException(status_code=404, detail="语义版本不存在")
    return version


@router.get("/catalog")
def catalog(version: str = ONTOLOGY_VERSION, term: str | None = None) -> dict:
    try:
        return query_ontology(term, version)
    except KeyError:
        raise HTTPException(status_code=404, detail="本体版本不存在")


@router.get("/terms")
def terms(group: str, text: str):
    try:
        return normalize_term(group, text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tools")
def tools():
    return [{"id": key, "input": args, "output": output} for key, (args, output) in TOOL_CATALOG.items()]


@router.get("/schema")
def schema() -> dict:
    return SemanticModel.model_json_schema()


@router.get("/sessions/{session_id}/versions")
def versions(session_id: str, db: Session = Depends(get_db)) -> list[dict]:
    if not db.get(TestSession, session_id):
        raise HTTPException(status_code=404, detail="测试会话不存在")
    rows = db.scalars(select(SemanticVersion).where(SemanticVersion.session_id == session_id)
                      .order_by(SemanticVersion.created_at.desc()))
    return [{"id": row.id, "message_id": row.message_id, "created_at": row.created_at,
             "status": row.model["validation"]["status"],
             "extraction_status": (row.model.get("metadata") or {}).get("extraction_status"),
             "parent_version_id": row.model["parent_version_id"], "digest": row.model["content_digest"]} for row in rows]


@router.get("/sessions/{session_id}/versions/{version_id}", response_model=SemanticModel)
def version(session_id: str, version_id: str, db: Session = Depends(get_db)):
    return get_version(db, session_id, version_id).model


@router.get("/sessions/{session_id}/versions/{version_id}/graph", response_model=ProcessGraph)
def graph(session_id: str, version_id: str, db: Session = Depends(get_db)):
    model = SemanticModel.model_validate(get_version(db, session_id, version_id).model)
    return model.process_graph or build_process_graph(model)


@router.post("/sessions/{session_id}/versions/{version_id}/review", response_model=SemanticModel,
             status_code=status.HTTP_201_CREATED)
def review(session_id: str, version_id: str, payload: ReviewRequest, db: Session = Depends(get_db)):
    base = SemanticModel.model_validate(get_version(db, session_id, version_id).model)
    session = db.get(TestSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="测试会话不存在")
    observed = session.updated_at
    run_id, message_id = str(uuid.uuid4()), str(uuid.uuid4())
    # Keep credentials out of both human review and the persisted evidence.
    secrets = [settings.default_llm_api_key, *db.scalars(select(AgentProfile.api_key)).all()]
    safe_payload = type(payload).model_validate(redact(payload.model_dump(), *secrets))
    try:
        model, statement = apply_review(base, run_id, message_id, safe_payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    model.metadata.session_id = session_id
    projected = template_context(model)
    activated = db.execute(update(TestSession).where(
        TestSession.id == session_id, TestSession.updated_at == observed,
    ).values(current_extracted_json=projected)).rowcount == 1
    if not activated:
        db.rollback()
        raise HTTPException(status_code=409, detail="会话已有新结果，请重新选择审核基础")
    db.add_all([
        ChatMessage(id=message_id, session_id=session_id, role="user", content=statement),
        ChatMessage(id=run_id, session_id=session_id, role="assistant", content="已保存人工审核版本。",
                    extracted_json=projected, debug_info={"strategy": "manual_review", "input_message_id": message_id,
                                                          "base_version_id": version_id, "request_count": 0}),
        SemanticVersion(id=run_id, session_id=session_id, message_id=message_id, model=model.model_dump(mode="json")),
    ])
    db.commit()
    return model


@router.get("/sessions/{session_id}/versions/{version_id}/context")
def context(session_id: str, version_id: str, db: Session = Depends(get_db)):
    return template_context(SemanticModel.model_validate(get_version(db, session_id, version_id).model))


@router.get("/sessions/{session_id}/versions/{version_id}/issues")
def issues(session_id: str, version_id: str, db: Session = Depends(get_db)):
    return get_version(db, session_id, version_id).model["validation"]


@router.get("/sessions/{session_id}/versions/{version_id}/export")
def export_version(session_id: str, version_id: str, db: Session = Depends(get_db)):
    model = SemanticModel.model_validate(get_version(db, session_id, version_id).model)
    return Response(model.model_dump_json(indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="semantic-{version_id}.json"'})


def source_documents(session: TestSession, file_ids: set[str], message_ids: set[str]):
    docs = [uploaded_document(f) for f in session.files
            if f.id in file_ids and f.parse_status == "success"]
    docs.extend(message_document(m.id, m.content) for m in session.messages
                if m.id in message_ids and m.role == "user")
    return docs


@router.post("/sessions/{session_id}/versions/{version_id}/validate")
def revalidate(session_id: str, version_id: str, db: Session = Depends(get_db)):
    stored = get_version(db, session_id, version_id)
    session = db.get(TestSession, session_id)
    semantic = SemanticModel.model_validate(stored.model)
    ids = {ref.document_id for ref in semantic.source_refs if ref.kind == "file"}
    message_ids = {ref.document_id for ref in semantic.source_refs if ref.kind == "message"}
    docs = source_documents(session, ids, message_ids)
    originals = {ref.document_id: ref.content_sha256 for ref in semantic.source_refs}
    docs = [doc for doc in docs if doc.ref.content_sha256 == originals[doc.ref.document_id]]
    for group in (*COLLECTIONS, "relations", "modeling_intents"):
        for fact in getattr(semantic, group):
            for cite in [*fact.evidence, *getattr(fact, "equipment_evidence", []), *fact.review_evidence]:
                located = locate(docs, cite.quote, cite.extraction_batch,
                                 document_id=cite.document_id, fragment_id=cite.fragment_id)
                cite.status = "verified" if located and located[0].location == cite.location else "pending"
    preserved = [issue for issue in semantic.validation.issues if issue.code in {
        "field_conflict", "identity_conflict", "candidate_shape", "candidate_schema", "file_parse_error",
        "empty_extraction", "revision_pending", "revision_target_unresolved", "evidence_not_located",
    }]
    preserved.extend(issue for issue in semantic.validation.issues if issue.code.startswith("batch_"))
    result = validate_model(semantic, preserved)
    result.validation.issues.extend(build_process_graph(result).issues)
    if result.validation.status == "verified" and result.validation.issues:
        result.validation.status = "pending"
    return result.validation


@router.post("/sessions/{session_id}/project", response_model=SemanticModel, status_code=status.HTTP_201_CREATED)
def project_existing(session_id: str, payload: ProjectRequest, db: Session = Depends(get_db)):
    session = db.get(TestSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="测试会话不存在")
    ids = set(payload.file_ids)
    message = db.get(ChatMessage, payload.message_id) if payload.message_id else None
    if payload.message_id and (not message or message.session_id != session_id):
        raise HTTPException(status_code=404, detail="抽取消息不存在")
    if not ids:
        ids = set(message.file_ids if message and message.file_ids else [f.id for f in session.files])
    if ids - {f.id for f in session.files}:
        raise HTTPException(status_code=404, detail="来源文件不存在")
    user_ids = {message.id} if message and message.role == "user" else set()
    if message and message.role == "assistant":
        paired = (message.debug_info or {}).get("input_message_id")
        if not paired:
            history = list(session.messages)
            before = history[:history.index(message)]
            paired = next((item.id for item in reversed(before) if item.role == "user"), None)
        if paired:
            user_ids.add(paired)
    docs = source_documents(session, ids, user_ids)
    run_id = str(uuid.uuid4())
    builder = Builder(docs, run_id)
    builder.model.metadata.session_id = session_id
    builder.table(list(table_rows(docs)))
    for doc in docs:
        if doc.ref.kind == "message":
            builder.explicit_requirements(doc)
    supplied = payload.candidates if payload.candidates is not None else message.extracted_json if message else None
    if supplied:
        supplied = redact(supplied, settings.default_llm_api_key)
        if payload.candidates is None and message:
            supplied = legacy_candidates(supplied)
        builder.candidates(supplied)
    builder.mapping_queue()
    if not builder.by_id:
        builder.issue("empty_extraction", "未从历史结果生成可核验事实", "extraction")
        builder.model.metadata.extraction_status = "failed"
    semantic = builder.finish()
    db.add(SemanticVersion(id=run_id, session_id=session_id, message_id=payload.message_id or run_id,
                           model=semantic.model_dump(mode="json")))
    db.commit()
    return semantic
