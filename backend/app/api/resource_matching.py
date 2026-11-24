"""REST API for Resource Matching Agent & Matching Run History."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.semantics import get_version
from app.config import settings
from app.database import get_db
from app.models.db_models import AgentProfile, ResourceMatchingRun
from app.services.llm_extractor import ExtractorConfig
from domain_semantics import SemanticModel
from domain_semantics.catalog import semantic_mappings
from domain_semantics.handoff import ResourceMatchingInput, build_resource_matching_input
from model_assets.catalog import get_default_catalog
from resource_matching.agent import run_resource_matching
from resource_matching.schema import ResourceMatchingConfig, ResourceMatchingOutput
from resource_matching.tools import list_matching_tools

router = APIRouter(prefix="/api/resource-matching", tags=["resource_matching"])


class RunVersionMatchingRequest(BaseModel):
    config: ResourceMatchingConfig = Field(default_factory=ResourceMatchingConfig)
    agent_id: str | None = None
    save: bool = True


class RunDirectMatchingRequest(BaseModel):
    handoff_input: ResourceMatchingInput
    config: ResourceMatchingConfig = Field(default_factory=ResourceMatchingConfig)
    agent_id: str | None = None
    save: bool = False


def _resolve_llm_config(db: Session, agent_id: str | None) -> ExtractorConfig | None:
    agent = db.get(AgentProfile, agent_id) if agent_id else db.query(AgentProfile).order_by(AgentProfile.created_at.asc()).first()
    if agent is None:
        if not settings.default_llm_api_key:
            return None
        return ExtractorConfig(
            base_url=settings.default_llm_base_url,
            api_key=settings.default_llm_api_key,
            model=settings.default_llm_model,
            temperature=0.1,
            max_tokens=2048,
            json_mode=True,
        )
    base_url = agent.base_url or settings.default_llm_base_url
    api_key = agent.api_key or settings.default_llm_api_key
    model = agent.model or settings.default_llm_model
    if not api_key and not base_url.startswith("mock://"):
        return None
    return ExtractorConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=agent.temperature,
        max_tokens=min(agent.max_tokens, 4096),
        json_mode=agent.json_mode,
        timeout_seconds=60,
        request_budget=1,
    )


@router.get("/schema")
def matching_output_schema() -> dict:
    return ResourceMatchingOutput.model_json_schema()


@router.get("/tools")
def matching_tools() -> list[dict[str, str]]:
    return list_matching_tools()


@router.get("/mappings")
def ontology_mappings() -> list[dict]:
    return semantic_mappings()


@router.post(
    "/sessions/{session_id}/versions/{version_id}/run",
    response_model=ResourceMatchingOutput,
    status_code=status.HTTP_201_CREATED,
)
async def run_for_version(
    session_id: str,
    version_id: str,
    body: RunVersionMatchingRequest | None = None,
    db: Session = Depends(get_db),
) -> ResourceMatchingOutput:
    req = body or RunVersionMatchingRequest()
    stored = get_version(db, session_id, version_id)
    semantic_model = SemanticModel.model_validate(stored.model)
    handoff_input = build_resource_matching_input(semantic_model)
    llm_cfg = _resolve_llm_config(db, req.agent_id) if (req.config.use_llm or req.config.method == "B") else None

    output = await run_resource_matching(
        handoff_input,
        catalog=get_default_catalog(),
        config=req.config,
        llm_config=llm_cfg,
    )
    if req.save:
        db.add(
            ResourceMatchingRun(
                id=output.run_id,
                session_id=session_id,
                semantic_version_id=version_id,
                method=output.config.method,
                matching_status=output.matching_status,
                catalog_version=output.catalog_version,
                config=output.config.model_dump(mode="json"),
                output=output.model_dump(mode="json"),
            )
        )
        db.commit()
    return output


@router.post("/run-direct", response_model=ResourceMatchingOutput)
async def run_direct(
    body: RunDirectMatchingRequest,
    db: Session = Depends(get_db),
) -> ResourceMatchingOutput:
    llm_cfg = _resolve_llm_config(db, body.agent_id) if (body.config.use_llm or body.config.method == "B") else None
    output = await run_resource_matching(
        body.handoff_input,
        catalog=get_default_catalog(),
        config=body.config,
        llm_config=llm_cfg,
    )
    if body.save:
        db.add(
            ResourceMatchingRun(
                id=output.run_id,
                session_id=None,
                semantic_version_id=output.source_requirement_version,
                method=output.config.method,
                matching_status=output.matching_status,
                catalog_version=output.catalog_version,
                config=output.config.model_dump(mode="json"),
                output=output.model_dump(mode="json"),
            )
        )
        db.commit()
    return output


@router.get("/runs")
def list_matching_runs(
    session_id: str | None = None,
    semantic_version_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(ResourceMatchingRun).order_by(ResourceMatchingRun.created_at.desc())
    if session_id:
        stmt = stmt.where(ResourceMatchingRun.session_id == session_id)
    if semantic_version_id:
        stmt = stmt.where(ResourceMatchingRun.semantic_version_id == semantic_version_id)
    rows = db.scalars(stmt).all()
    return [
        {
            "run_id": row.id,
            "session_id": row.session_id,
            "semantic_version_id": row.semantic_version_id,
            "method": row.method,
            "matching_status": row.matching_status,
            "catalog_version": row.catalog_version,
            "top_k": (row.config or {}).get("top_k", 3),
            "summary": (row.output or {}).get("summary"),
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/runs/{run_id}", response_model=ResourceMatchingOutput)
def get_matching_run(run_id: str, db: Session = Depends(get_db)):
    row = db.get(ResourceMatchingRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="资源匹配记录不存在")
    return row.output


@router.get("/runs/{run_id}/export")
def export_matching_run(run_id: str, db: Session = Depends(get_db)) -> Response:
    row = db.get(ResourceMatchingRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="资源匹配记录不存在")
    model = ResourceMatchingOutput.model_validate(row.output)
    return Response(
        model.model_dump_json(indent=2) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="resource-matching-output-{run_id}.json"'},
    )


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_matching_run(run_id: str, db: Session = Depends(get_db)) -> Response:
    row = db.get(ResourceMatchingRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="资源匹配记录不存在")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
