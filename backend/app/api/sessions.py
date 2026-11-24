import asyncio
import hashlib
from collections import deque
from contextlib import contextmanager, suppress
from pathlib import Path
from time import monotonic
import shutil
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import delete, select, update
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.config import settings
from app.api.agents import agent_execution
from app.database import get_db
from app.models.db_models import (
    AgentProfile,
    ChatMessage,
    OutputTemplate,
    SemanticVersion,
    TestSession,
    UploadedFile,
)
from app.models.schemas import (
    ChatMessageOut,
    RunChatRequest,
    RunChatResponse,
    TestSessionCreate,
    TestSessionDetail,
    TestSessionSummary,
    TestSessionUpdate,
    UploadedFileOut,
)
from app.services.llm_extractor import (
    ExtractorConfig,
    FileInputContext,
    is_official_deepseek,
    normalize_base_url,
    run_extraction,
)
from app.services.parser import parse_file_bytes
from app.services.extraction_batches import ResumeSourceError
from app.services.seed import SAMPLE_DATASETS
from app.services.template_filler import render_template
from app.services.redaction import redact
from app.services.semantic_execution import DEEPSEEK_REQUEST_BUDGET, execute
from domain_semantics.pipeline import timestamp, trace
from domain_semantics.projection import template_context
from domain_semantics.schema import BatchReport, SemanticModel
from app.services.source_inputs import uploaded_document
from domain_semantics.sources import digest, message_document

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _get_session_or_404(db: Session, session_id: str) -> TestSession:
    session = db.get(TestSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="测试会话不存在")
    return session


def _resolve_agent(db: Session, agent_id: str | None) -> AgentProfile:
    agent = db.get(AgentProfile, agent_id) if agent_id else None
    if not agent:
        agent = db.query(AgentProfile).order_by(AgentProfile.created_at.asc()).first()
    if not agent:
        raise HTTPException(status_code=400, detail="没有可用的智能体配置")
    return agent


def _resolve_template(db: Session, template_id: str | None) -> OutputTemplate | None:
    if template_id:
        tmpl = db.get(OutputTemplate, template_id)
        if tmpl:
            return tmpl
    return db.query(OutputTemplate).order_by(OutputTemplate.created_at.asc()).first()


@router.get("", response_model=list[TestSessionSummary])
def list_sessions(db: Session = Depends(get_db)) -> list[TestSession]:
    return db.query(TestSession).order_by(TestSession.updated_at.desc()).all()


@router.post("", response_model=TestSessionDetail, status_code=status.HTTP_201_CREATED)
def create_session(payload: TestSessionCreate, db: Session = Depends(get_db)) -> TestSession:
    agent_id = payload.agent_id
    if not agent_id:
        first_agent = db.query(AgentProfile).order_by(AgentProfile.created_at.asc()).first()
        agent_id = first_agent.id if first_agent else None

    template_id = payload.template_id
    if not template_id:
        first_tmpl = db.query(OutputTemplate).order_by(OutputTemplate.created_at.asc()).first()
        template_id = first_tmpl.id if first_tmpl else None

    session = TestSession(
        title=payload.title,
        agent_id=agent_id,
        template_id=template_id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/{session_id}", response_model=TestSessionDetail)
def get_session(session_id: str, db: Session = Depends(get_db)) -> TestSessionDetail:
    record = _get_session_or_404(db, session_id)
    detail = TestSessionDetail.model_validate(record)
    secrets = [settings.default_llm_api_key, *db.scalars(select(AgentProfile.api_key)).all()]
    for message in detail.messages:
        message.debug_info = redact(message.debug_info, *secrets)
        if message.role == "assistant" and message.status == "error":
            message.content = redact(message.content, *secrets)
    return detail


@router.patch("/{session_id}", response_model=TestSessionDetail)
def update_session(
    session_id: str, payload: TestSessionUpdate, db: Session = Depends(get_db)
) -> TestSessionDetail:
    session = _get_session_or_404(db, session_id)
    fields = payload.model_dump(exclude_unset=True)
    if "current_extracted_json" in fields and (
        (session.current_extracted_json or {}).get("semantic") or (fields["current_extracted_json"] or {}).get("semantic")
    ):
        raise HTTPException(status_code=409, detail="语义版本通过定向修订生成。JSON 编辑仅适用于旧抽取结果。")
    for key, value in fields.items():
        setattr(session, key, value)
    db.commit()
    db.refresh(session)
    return get_session(session_id, db)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: str, db: Session = Depends(get_db)) -> None:
    session = _get_session_or_404(db, session_id)
    session_upload_dir = settings.upload_path / session_id
    db.execute(delete(SemanticVersion).where(SemanticVersion.session_id == session_id))
    db.delete(session)
    db.commit()
    if session_upload_dir.exists():
        shutil.rmtree(session_upload_dir, ignore_errors=True)


@router.post("/{session_id}/clear", response_model=TestSessionDetail)
def clear_session(session_id: str, db: Session = Depends(get_db)) -> TestSession:
    session = _get_session_or_404(db, session_id)
    db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
    session.current_extracted_json = None
    db.commit()
    db.refresh(session)
    return session


@router.post("/{session_id}/files", response_model=list[UploadedFileOut], status_code=status.HTTP_201_CREATED)
async def upload_files(
    session_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> list[UploadedFile]:
    session = _get_session_or_404(db, session_id)
    session_dir = settings.upload_path / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    documents = []
    total = 0
    for upload in files:
        raw = await upload.read(settings.max_upload_bytes + 1)
        total += len(raw)
        if len(raw) > settings.max_upload_bytes or total > settings.max_upload_total_bytes:
            raise HTTPException(status_code=413, detail="上传文件超过单文件或单批大小上限")
        documents.append((Path(upload.filename or "untitled.txt").name, raw))

    saved_records: list[UploadedFile] = []
    existing = {(row.filename, row.file_size): [] for row in session.files}
    for row in session.files:
        existing[row.filename, row.file_size].append(row)
    received = {}
    for safe_name, raw in documents:
        fingerprint = (safe_name, hashlib.sha256(raw).digest())
        if fingerprint in received:
            continue
        for row in existing.get((safe_name, len(raw)), []):
            try:
                stored = await run_in_threadpool(Path(row.stored_path).read_bytes)
            except OSError:
                continue
            if raw == stored:
                received[fingerprint] = row
                saved_records.append(row)
                break
        if fingerprint in received:
            continue
        unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        dest = session_dir / unique_name
        await run_in_threadpool(dest.write_bytes, raw)

        parsed = await run_in_threadpool(_parse_uploaded, raw, safe_name)
        record = UploadedFile(
            session_id=session_id,
            filename=safe_name,
            stored_path=str(dest),
            file_ext=parsed.file_ext,
            file_size=len(raw),
            parse_status=parsed.status,
            parse_error=parsed.error,
            parsed_text=parsed.parsed_text,
            parsed_preview=parsed.parsed_preview,
        )
        db.add(record)
        received[fingerprint] = record
        saved_records.append(record)

    db.commit()
    for r in saved_records:
        db.refresh(r)
    return saved_records


@router.post("/{session_id}/files/sample/{sample_id}", response_model=UploadedFileOut, status_code=status.HTTP_201_CREATED)
def load_sample_csv_file(session_id: str, sample_id: str, db: Session = Depends(get_db)) -> UploadedFile:
    _get_session_or_404(db, session_id)
    sample = next((s for s in SAMPLE_DATASETS if s["id"] == sample_id), None)
    if not sample:
        raise HTTPException(status_code=404, detail="样例数据不存在")

    filename = sample.get("filename") or f"{sample_id}.txt"
    raw = sample["content"].encode("utf-8")

    session_dir = settings.upload_path / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    dest = session_dir / f"{uuid.uuid4().hex[:8]}_{filename}"
    dest.write_bytes(raw)

    parsed = _parse_uploaded(raw, filename)
    record = UploadedFile(
        session_id=session_id,
        filename=filename,
        stored_path=str(dest),
        file_ext=parsed.file_ext,
        file_size=len(raw),
        parse_status=parsed.status,
        parse_error=parsed.error,
        parsed_text=parsed.parsed_text,
        parsed_preview=parsed.parsed_preview,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _parse_uploaded(raw: bytes, filename: str):
    mark = timestamp()
    parsed = parse_file_bytes(raw, filename)
    parsed.parsed_preview["parse_stage"] = trace(
        "file_parsing", *mark, parsed.status, parsed.error or filename,
        "file_parse_error" if parsed.status == "error" else None,
    )
    return parsed


@router.post("/{session_id}/files/{file_id}/reparse", response_model=UploadedFileOut)
def reparse_file(session_id: str, file_id: str, db: Session = Depends(get_db)) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if not record or record.session_id != session_id:
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        raw = Path(record.stored_path).read_bytes()
    except OSError:
        raise HTTPException(status_code=409, detail="存储中的原始文件不可读取，请重新上传")
    parsed = _parse_uploaded(raw, record.filename)
    record.parsed_text = parsed.parsed_text
    record.parsed_preview = parsed.parsed_preview
    record.parse_status = parsed.status
    record.parse_error = parsed.error
    db.commit()
    return record


@router.delete("/{session_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_uploaded_file(session_id: str, file_id: str, db: Session = Depends(get_db)) -> None:
    record = (
        db.query(UploadedFile)
        .filter(UploadedFile.id == file_id, UploadedFile.session_id == session_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = Path(record.stored_path)
    db.delete(record)
    db.commit()
    if path.exists():
        path.unlink(missing_ok=True)


active_runs: dict[tuple[str, uuid.UUID], asyncio.Event] = {}
early_cancellations: dict[tuple[str, uuid.UUID], float] = {}
run_progress: dict[tuple[str, uuid.UUID], deque] = {}


def expire_cancellations() -> None:
    now = monotonic()
    for key in list(early_cancellations):
        if early_cancellations[key] <= now:
            del early_cancellations[key]


@contextmanager
def track_run(session_id: str, token: uuid.UUID | None):
    flag = asyncio.Event()
    if token is None:
        yield flag
        return
    key = (session_id, token)
    expire_cancellations()
    if key in active_runs:
        raise HTTPException(status_code=409, detail="此运行请求正在执行")
    if early_cancellations.pop(key, None) is not None:
        flag.set()
    active_runs[key] = flag
    run_progress[key] = deque(maxlen=100)
    try:
        yield flag
    finally:
        active_runs.pop(key, None)
        run_progress.pop(key, None)


@router.get("/{session_id}/runs/{token}/progress")
async def get_run_progress(session_id: str, token: uuid.UUID, db: Session = Depends(get_db)):
    _get_session_or_404(db, session_id)
    progress = run_progress.get((session_id, token))
    return {"status": "running" if progress is not None else "waiting", "steps": list(progress or [])}


@router.post("/{session_id}/runs/{token}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(session_id: str, token: uuid.UUID, db: Session = Depends(get_db)):
    _get_session_or_404(db, session_id)
    key = (session_id, token)
    flag = active_runs.get(key)
    if flag is not None:
        flag.set()
    else:
        expire_cancellations()
        if len(early_cancellations) >= 512:
            early_cancellations.pop(next(iter(early_cancellations)))
        early_cancellations[key] = monotonic() + 30
    return {"status": "cancel_requested"}


async def _while_connected(job, request: Request, cancelled: asyncio.Event):
    task = asyncio.create_task(job)
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.25)
            if cancelled.is_set() or (not task.done() and await request.is_disconnected()):
                raise HTTPException(status_code=499, detail="运行已停止")
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def model_version(state: dict | None) -> SemanticModel | None:
    value = (state or {}).get("semantic")
    return SemanticModel.model_validate(value) if value else None


async def _execute_extraction_turn(
    db: Session, session: TestSession, message_text: str, file_ids: list[str], agent: AgentProfile,
    template: OutputTemplate | None, current_state: dict | None, request: Request, cancelled: asyncio.Event,
    use_demo_fallback: bool = False, strategy: str = "semantic",
    run_token: uuid.UUID | None = None,
    history_limit: int | None = None,
    source_message: ChatMessage | None = None,
    pending_batches: list[BatchReport] | None = None,
) -> RunChatResponse:
    if cancelled.is_set():
        raise HTTPException(status_code=499, detail="运行已停止")
    mark = timestamp()
    session_id, observed_update = session.id, session.updated_at
    if file_ids and set(file_ids) - {f.id for f in session.files}:
        raise HTTPException(status_code=404, detail="部分文件已删除或不属于当前会话")
    target_files = [f for f in session.files if f.id in file_ids or (not file_ids and source_message is None)]
    if not message_text.strip() and not target_files:
        raise HTTPException(status_code=400, detail="请输入文本指令或上传至少一份文件")
    failed_files = [f for f in target_files if f.parse_status != "success"]
    good_files = [f for f in target_files if f.parse_status == "success"]
    errors = [f"{f.filename}：{f.parse_error}" for f in failed_files]
    if target_files and not good_files and not message_text.strip():
        raise HTTPException(status_code=400, detail="所选文件全部解析失败：" + "；".join(errors))

    title = session.title
    if title in {"新建工艺解析测试", "新测试会话", "未命名测试会话"}:
        title = message_text.strip()[:28] or f"解析：{target_files[0].filename[:24]}"
    user_id = source_message.id if source_message else str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    history = [{"role": m.role, "content": m.content}
               for m in list(session.messages)[:history_limit] if m.status == "success"]
    execution = agent_execution(db, agent.id)
    base_url = normalize_base_url(agent.base_url) or settings.default_llm_base_url
    official_semantic = strategy == "semantic" and is_official_deepseek(base_url)
    request_budget = DEEPSEEK_REQUEST_BUDGET if official_semantic else settings.llm_request_budget
    config = ExtractorConfig(
        base_url=base_url,
        api_key=agent.api_key.strip() or settings.default_llm_api_key,
        model=agent.model.strip() or settings.default_llm_model,
        temperature=agent.temperature, max_tokens=agent.max_tokens, json_mode=agent.json_mode,
        require_source_quote=agent.require_source_quote, system_prompt=agent.system_prompt,
        business_rules=agent.business_rules, extraction_schema=agent.extraction_schema or {},
        timeout_seconds=execution.timeout_seconds or settings.llm_timeout_seconds,
        request_budget=execution.request_budget or request_budget,
        semantic_batch_max_tokens=execution.batch_max_tokens,
        llm_disambiguation=execution.llm_disambiguation,
    )
    if source_message is not None and (not config.api_key or config.base_url.startswith("mock://")):
        raise HTTPException(status_code=409, detail="请先配置在线模型，再继续解析未完成批次。")
    selected_ids = [item.id for item in target_files]
    agent_id, agent_name = agent.id, agent.name
    template_config = None if template is None else dict(
        template_id=template.id, template_type=template.template_type, template_format=template.format,
        jinja_content=template.jinja_content, table_config=template.table_config or {},
    )
    user_content = (message_text if message_text.strip() else "") or f"（上传并解析文件：{', '.join(f.filename for f in target_files)}）"
    user_msg = source_message or ChatMessage(id=user_id, session_id=session_id, role="user", content=user_content,
                                             file_ids=selected_ids, agent_id=agent_id, agent_name=agent_name, status="success")
    base = model_version(current_state)
    active_model = (session.current_extracted_json or {}).get("semantic")
    can_activate = source_message is None or (
        base is not None and isinstance(active_model, dict) and active_model.get("run_id") == base.run_id
    )
    parsing_stages = [dict(f.parsed_preview["parse_stage"], file_id=f.id)
                      for f in target_files if f.parsed_preview.get("parse_stage")]
    if not parsing_stages:
        parsing_stages = [{"stage": "file_parsing", "status": "skipped", "summary": "本次复用历史解析或未选择文件"}]
    input_stage = trace("input_selection", *mark, "partial" if errors else "success",
                        f"{len(target_files)} 份选中文件，{len(errors)} 份解析失败")
    model = None
    if strategy == "legacy":
        contexts = [FileInputContext(filename=f.filename, parsed_text=f.parsed_text) for f in good_files]
        db.rollback()
        call = timestamp()
        result = await _while_connected(run_extraction(
            config=config, user_message=message_text, files=contexts, current_state=current_state,
            conversation_history=history, use_demo_fallback=use_demo_fallback), request, cancelled)
        result.debug_info["pipeline_stages"] = [trace(
            "llm_extraction", *call, result.status, result.error_message or "旧版抽取配置",
            result.debug_info.get("error_code"),
        )]
    else:
        processed = (set(base.metadata.processed_source_ids) if base and base.metadata else set())
        source_digests = {source.document_id: source.content_sha256 for source in base.source_refs} if base else {}
        documents = await run_in_threadpool(lambda: [
            uploaded_document(file, expand=file.id not in processed or source_digests.get(file.id) != digest(file.parsed_text))
            for file in good_files
        ])
        db.rollback()
        progress = run_progress.get((session_id, run_token))
        try:
            model, result = await _while_connected(execute(
                config, message_document(user_id, message_text), documents, run_id, base,
                offline=use_demo_fallback or config.base_url.startswith("mock://"), failed=errors,
                session_id=session_id, publish=(lambda step: progress.append(redact(step, config.api_key)))
                if progress is not None else None, resume=pending_batches,
            ), request, cancelled)
        except ResumeSourceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if model:
            exported = timestamp()
            result.extracted_json = template_context(model)
            phase = trace("export_requirement_model", *exported, "success", "已生成规范模型及模板投影")
            result.debug_info["pipeline_stages"].append(phase)
            result.debug_info["tool_trace"].append(phase)
    if cancelled.is_set() or await request.is_disconnected():
        raise HTTPException(status_code=499, detail="运行已停止")

    _get_session_or_404(db, session_id)
    started = timestamp()
    result.debug_info.update(
        base_version_id=base.run_id if base else None,
        run_id=run_id, input_message_id=user_id, original_instruction=message_text,
        strategy=strategy, offline=use_demo_fallback or config.base_url.startswith("mock://"),
    )
    if source_message is not None and base is not None:
        result.debug_info["resumed_from_version_id"] = base.run_id
    result.debug_info["pipeline_stages"] = [
        *parsing_stages,
        input_stage,
        *result.debug_info.get("pipeline_stages", []),
    ]
    patch = dict(title=title, agent_id=agent_id)
    if template_config:
        patch["template_id"] = template_config["template_id"]
    if result.status in {"success", "partial"} and result.extracted_json is not None:
        patch["current_extracted_json"] = result.extracted_json
    activated = can_activate and db.execute(update(TestSession).where(
        TestSession.id == session_id, TestSession.updated_at == observed_update,
    ).values(**patch)).rowcount == 1
    db.expire_all()
    current = _get_session_or_404(db, session_id).current_extracted_json
    if not activated:
        result.debug_info["activation_note"] = ("会话已被其他请求更新，本次结果仅保留在历史记录。" if can_activate
                                                  else "续跑的基础版本已不是当前结果，本次结果保留在历史记录。")

    assistant_msg = ChatMessage(
        id=run_id, session_id=session_id, role="assistant",
        content=result.summary_text if result.status in {"success", "partial"} else (result.error_message or "执行失败"),
        file_ids=selected_ids, agent_id=agent_id, agent_name=agent_name,
        status=result.status, extracted_json=result.extracted_json,
    )
    if source_message is None:
        db.add(user_msg)
    db.add(assistant_msg)
    if model:
        db.add(SemanticVersion(id=run_id, session_id=session_id, message_id=user_id, model=model.model_dump(mode="json")))
    db.flush()
    result.debug_info["pipeline_stages"].append(trace("persistence", *started, "success", "写入消息及本次结果"))
    preview = None
    if template_config and current is not None:
        tick = timestamp()
        preview = render_template(**template_config, data=current)
        result.debug_info["pipeline_stages"].append(trace(
            "template_render", *tick, "error" if preview.error else "success",
            preview.error or "填充当前输出模板", "template_error" if preview.error else None,
        ))
    assistant_msg.debug_info = redact(result.debug_info, config.api_key, settings.default_llm_api_key)
    db.commit()
    return RunChatResponse(user_message=ChatMessageOut.model_validate(user_msg),
                           assistant_message=ChatMessageOut.model_validate(assistant_msg),
                           current_extracted_json=current, template_preview=preview)


@router.post("/{session_id}/run", response_model=RunChatResponse)
async def run_session_turn(
    session_id: str, payload: RunChatRequest, request: Request, db: Session = Depends(get_db)
) -> RunChatResponse:
    session = _get_session_or_404(db, session_id)
    agent = _resolve_agent(db, payload.agent_id or session.agent_id)
    template = _resolve_template(db, payload.template_id or session.template_id)

    current = session.current_extracted_json
    if payload.base_version_id:
        base = db.get(SemanticVersion, payload.base_version_id)
        if base is None or base.session_id != session_id:
            raise HTTPException(status_code=404, detail="基础语义版本不存在")
        current = {"semantic": base.model}

    with track_run(session_id, payload.run_token) as cancelled:
        return await _execute_extraction_turn(
            db=db,
            session=session,
            message_text=payload.message,
            file_ids=payload.file_ids,
            agent=agent,
            template=template,
            current_state=current,
            request=request, cancelled=cancelled, strategy=payload.strategy, run_token=payload.run_token,
            use_demo_fallback=payload.use_demo_fallback,
        )


@router.post("/{session_id}/rerun/{message_id}", response_model=RunChatResponse)
async def rerun_turn(
    session_id: str,
    message_id: str,
    request: Request,
    use_demo_fallback: bool = False,
    run_token: uuid.UUID | None = None,
    db: Session = Depends(get_db),
) -> RunChatResponse:
    session = _get_session_or_404(db, session_id)
    msgs = list(session.messages)
    target_idx = next((i for i, m in enumerate(msgs) if m.id == message_id), None)
    if target_idx is None:
        raise HTTPException(status_code=404, detail="消息不存在")

    target = msgs[target_idx]
    if target.role == "assistant":
        # Find preceding user message
        user_idx = next((i for i in range(target_idx - 1, -1, -1) if msgs[i].role == "user"), None)
        if user_idx is None:
            raise HTTPException(status_code=400, detail="未找到对应的用户指令消息")
        user_msg = msgs[user_idx]
    else:
        user_idx = target_idx
        user_msg = target

    # Determine JSON state prior to that user turn
    prev_state = None
    for i in range(user_idx - 1, -1, -1):
        if msgs[i].role == "assistant" and msgs[i].status in {"success", "partial"} and msgs[i].extracted_json:
            prev_state = msgs[i].extracted_json
            break

    debug = target.debug_info or {}
    if debug.get("strategy") == "manual_review":
        raise HTTPException(status_code=409, detail="人工审核版本可重新核验来源或创建新的审核版本")
    paired = next((m for m in msgs if m.id == debug.get("input_message_id")), None)
    if paired:
        user_msg = paired
        user_idx = msgs.index(paired)
    if debug.get("strategy") == "semantic" and "base_version_id" in debug:
        parent = db.get(SemanticVersion, debug["base_version_id"]) if debug["base_version_id"] else None
        prev_state = {"semantic": parent.model} if parent else None
    agent = _resolve_agent(db, session.agent_id)
    template = _resolve_template(db, session.template_id)

    with track_run(session_id, run_token) as cancelled:
        return await _execute_extraction_turn(
            db=db,
            session=session,
            message_text=debug.get("original_instruction", user_msg.content),
            file_ids=list(user_msg.file_ids or []),
            agent=agent,
            template=template,
            current_state=prev_state,
            request=request, cancelled=cancelled, strategy=debug.get("strategy", "legacy"), history_limit=user_idx,
            run_token=run_token,
            use_demo_fallback=use_demo_fallback,
        )


@router.post("/{session_id}/resume/{message_id}", response_model=RunChatResponse)
async def resume_turn(
    session_id: str,
    message_id: str,
    request: Request,
    run_token: uuid.UUID | None = None,
    db: Session = Depends(get_db),
) -> RunChatResponse:
    session = _get_session_or_404(db, session_id)
    target = db.get(ChatMessage, message_id)
    if not target or target.session_id != session_id or target.role != "assistant":
        raise HTTPException(status_code=404, detail="运行结果不存在")
    debug = target.debug_info or {}
    version = db.get(SemanticVersion, message_id)
    if debug.get("strategy") != "semantic" or target.status != "partial" or not version or version.session_id != session_id:
        raise HTTPException(status_code=409, detail="此结果没有可继续解析的批次。")
    source = db.get(ChatMessage, version.message_id)
    if not source or source.session_id != session_id or source.role != "user":
        raise HTTPException(status_code=409, detail="原始消息不可用，无法继续解析。")

    base = SemanticModel.model_validate(version.model)
    file_ids = list(source.file_ids or [])
    source_ids = {source.id, *file_ids}
    pending = [batch for batch in base.metadata.batches if batch.status != "complete" and batch.fragments
               and batch.reason != "review_required" and set(batch.source_ids) <= source_ids]
    if not pending:
        raise HTTPException(status_code=409, detail="此结果没有可继续解析的批次。")
    instruction = source.content if any(ref.document_id == source.id for ref in base.source_refs) else ""
    agent = _resolve_agent(db, session.agent_id)
    template = _resolve_template(db, session.template_id)
    with track_run(session_id, run_token) as cancelled:
        return await _execute_extraction_turn(
            db=db, session=session, message_text=instruction, file_ids=file_ids,
            agent=agent, template=template, current_state={"semantic": version.model},
            request=request, cancelled=cancelled, run_token=run_token,
            source_message=source, pending_batches=pending,
        )
