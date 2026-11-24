"""REST API for Resource Matching Agent (role-enforced) & Matching Run History."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.config import AgentNotFoundError, ResolvedAgentRuntime, RoleMismatchError, resolve_agent_runtime
from app.api.semantics import get_version
from app.database import get_db
from app.models.db_models import ResourceMatchingRun
from domain_semantics import SemanticModel
from domain_semantics.catalog import semantic_mappings
from domain_semantics.handoff import ResourceMatchingInput, build_resource_matching_input
from model_assets.catalog import get_default_catalog
from resource_matching.agent import MatchingExecutionError, run_resource_matching
from resource_matching.prompts import matching_prompt_manifest
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


def _resolve_matching_runtime(db: Session, agent_id: str | None) -> ResolvedAgentRuntime:
    try:
        _, runtime = resolve_agent_runtime(
            db,
            "resource_matching",
            agent_id,
            default_budget=3,
            default_timeout=60,
        )
        return runtime
    except RoleMismatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404 if agent_id else 400, detail=str(exc)) from exc


def _merge_runtime_defaults(config: ResourceMatchingConfig, runtime: ResolvedAgentRuntime) -> ResourceMatchingConfig:
    """Apply saved resource_matching agent execution defaults when not explicitly overridden."""
    updates: dict = {}
    if "fallback_policy" not in config.model_fields_set:
        updates["fallback_policy"] = runtime.fallback_policy
    if "max_llm_calls" not in config.model_fields_set and runtime.execution.request_budget is not None:
        updates["max_llm_calls"] = min(12, max(1, runtime.execution.request_budget))
    if "max_strategy_retries" not in config.model_fields_set:
        updates["max_strategy_retries"] = runtime.execution.max_strategy_retries
    return config.model_copy(update=updates) if updates else config


@router.get("/schema")
def matching_output_schema() -> dict:
    return ResourceMatchingOutput.model_json_schema()


@router.get("/tools")
def matching_tools() -> list[dict[str, str]]:
    return list_matching_tools()


@router.get("/prompts")
def matching_prompts() -> dict:
    return matching_prompt_manifest()


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

    runtime = _resolve_matching_runtime(db, req.agent_id)
    effective_config = _merge_runtime_defaults(req.config, runtime)

    try:
        output = await run_resource_matching(
            handoff_input,
            catalog=get_default_catalog(),
            config=effective_config,
            runtime=runtime,
        )
    except MatchingExecutionError as exc:
        raise HTTPException(status_code=400 if exc.error_code == "unconfigured" else 503, detail=str(exc)) from exc

    if req.save:
        db.add(
            ResourceMatchingRun(
                id=output.run_id,
                session_id=session_id,
                semantic_version_id=version_id,
                agent_id=runtime.agent_id,
                agent_name=runtime.agent_name,
                agent_type="resource_matching",
                llm_mode=output.config.llm_mode,
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
    runtime = _resolve_matching_runtime(db, body.agent_id)
    effective_config = _merge_runtime_defaults(body.config, runtime)
    try:
        output = await run_resource_matching(
            body.handoff_input,
            catalog=get_default_catalog(),
            config=effective_config,
            runtime=runtime,
        )
    except MatchingExecutionError as exc:
        raise HTTPException(status_code=400 if exc.error_code == "unconfigured" else 503, detail=str(exc)) from exc

    if body.save:
        db.add(
            ResourceMatchingRun(
                id=output.run_id,
                session_id=None,
                semantic_version_id=output.source_requirement_version,
                agent_id=runtime.agent_id,
                agent_name=runtime.agent_name,
                agent_type="resource_matching",
                llm_mode=output.config.llm_mode,
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
    agent_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(ResourceMatchingRun).order_by(ResourceMatchingRun.created_at.desc())
    if session_id:
        stmt = stmt.where(ResourceMatchingRun.session_id == session_id)
    if semantic_version_id:
        stmt = stmt.where(ResourceMatchingRun.semantic_version_id == semantic_version_id)
    if agent_id:
        stmt = stmt.where(ResourceMatchingRun.agent_id == agent_id)
    rows = db.scalars(stmt).all()
    return [
        {
            "run_id": row.id,
            "session_id": row.session_id,
            "semantic_version_id": row.semantic_version_id,
            "agent_id": row.agent_id,
            "agent_name": row.agent_name,
            "agent_type": row.agent_type,
            "llm_mode": row.llm_mode,
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
