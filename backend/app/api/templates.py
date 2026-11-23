from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import OutputTemplate, TemplateRevision
from app.models.schemas import (
    OutputTemplateCreate,
    OutputTemplateOut,
    OutputTemplateUpdate,
    TemplateDraftPreviewRequest,
    TemplateExportRequest,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
)
from app.services.template_filler import export_to_docx, export_to_xlsx, render_template

router = APIRouter(prefix="/api/templates", tags=["templates"])


def latest_revision(db: Session, template_id: str) -> int | None:
    return db.scalar(select(func.max(TemplateRevision.version)).where(TemplateRevision.template_id == template_id))


def template_out(db: Session, row: OutputTemplate) -> OutputTemplateOut:
    return OutputTemplateOut.model_validate(row).model_copy(update={"version": latest_revision(db, row.id) or 1})


def save_revision(db: Session, row: OutputTemplate, version: int) -> None:
    definition = OutputTemplateOut.model_validate(row).model_dump(
        mode="json", exclude={"version", "updated_at", "created_at"},
    )
    db.add(TemplateRevision(template_id=row.id, version=version, definition=definition))


@router.get("/{template_id}/versions")
def get_template_versions(template_id: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(TemplateRevision).where(TemplateRevision.template_id == template_id)
                      .order_by(TemplateRevision.version.desc())).all()
    if rows:
        return [{"version": item.version, "definition": item.definition, "created_at": item.created_at} for item in rows]
    template = db.get(OutputTemplate, template_id)
    if not template:
        raise HTTPException(404, "模板不存在")
    return [{"version": 1, "definition": OutputTemplateOut.model_validate(template).model_dump(mode="json"),
             "created_at": template.created_at}]


@router.get("", response_model=list[OutputTemplateOut])
def list_templates(db: Session = Depends(get_db)) -> list[OutputTemplateOut]:
    rows = db.query(OutputTemplate).order_by(OutputTemplate.created_at.asc()).all()
    return [template_out(db, row) for row in rows]


@router.post("", response_model=OutputTemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(payload: OutputTemplateCreate, db: Session = Depends(get_db)) -> OutputTemplateOut:
    tmpl = OutputTemplate(**payload.model_dump())
    db.add(tmpl)
    db.flush()
    save_revision(db, tmpl, 1)
    db.commit()
    return template_out(db, tmpl)


@router.patch("/{template_id}", response_model=OutputTemplateOut)
def update_template(
    template_id: str, payload: OutputTemplateUpdate, db: Session = Depends(get_db)
) -> OutputTemplateOut:
    tmpl = db.get(OutputTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    previous = latest_revision(db, tmpl.id)
    if previous is None:
        save_revision(db, tmpl, 1)
        previous = 1
    updates = payload.model_dump(exclude_none=True)
    if any(getattr(tmpl, key) != value for key, value in updates.items()):
        for key, value in updates.items():
            setattr(tmpl, key, value)
        save_revision(db, tmpl, previous + 1)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="模板已被另一次编辑更新，请刷新后重试") from exc
    return template_out(db, tmpl)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, db: Session = Depends(get_db)) -> None:
    tmpl = db.get(OutputTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    if db.query(OutputTemplate).count() <= 1:
        raise HTTPException(status_code=400, detail="至少保留一个输出模板")
    db.delete(tmpl)
    db.commit()


@router.post("/preview-draft", response_model=TemplatePreviewResponse)
def preview_template_draft(payload: TemplateDraftPreviewRequest) -> TemplatePreviewResponse:
    return render_template(
        template_id="draft",
        template_type=payload.template_type,
        template_format=payload.format,
        jinja_content=payload.jinja_content,
        table_config=payload.table_config or {},
        data=payload.data,
    )


@router.post("/{template_id}/preview", response_model=TemplatePreviewResponse)
def preview_template(
    template_id: str, payload: TemplatePreviewRequest, db: Session = Depends(get_db)
) -> TemplatePreviewResponse:
    tmpl = db.get(OutputTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    return render_template(
        template_id=tmpl.id,
        template_type=tmpl.template_type,
        template_format=tmpl.format,
        jinja_content=tmpl.jinja_content,
        table_config=tmpl.table_config or {},
        data=payload.data,
    )


@router.post("/{template_id}/export")
def export_template(
    template_id: str, payload: TemplateExportRequest, db: Session = Depends(get_db)
) -> Response:
    tmpl = db.get(OutputTemplate, template_id)
    if not tmpl:
        raise HTTPException(status_code=404, detail="模板不存在")

    safe_prefix = (payload.filename_prefix or tmpl.name or "process_output").strip()
    fmt = payload.export_format

    if fmt == "xlsx":
        content = export_to_xlsx(
            template_name=tmpl.name,
            template_type=tmpl.template_type,
            jinja_content=tmpl.jinja_content,
            table_config=tmpl.table_config or {},
            data=payload.data,
        )
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{safe_prefix}.xlsx"
    elif fmt == "docx":
        content = export_to_docx(
            template_name=tmpl.name,
            template_type=tmpl.template_type,
            jinja_content=tmpl.jinja_content,
            table_config=tmpl.table_config or {},
            data=payload.data,
        )
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"{safe_prefix}.docx"
    elif fmt == "md":
        rendered = render_template(
            template_id=tmpl.id,
            template_type=tmpl.template_type,
            template_format=tmpl.format,
            jinja_content=tmpl.jinja_content,
            table_config=tmpl.table_config or {},
            data=payload.data,
        )
        if rendered.error:
            raise HTTPException(status_code=400, detail=rendered.error)
        content = rendered.rendered_text.encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
        filename = f"{safe_prefix}.md"
    else:
        import json

        content = json.dumps(payload.data, ensure_ascii=False, indent=2).encode("utf-8")
        media_type = "application/json; charset=utf-8"
        filename = f"{safe_prefix}.json"

    encoded_name = quote(filename)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename=\"export.{fmt}\"; filename*=UTF-8''{encoded_name}"
        },
    )
