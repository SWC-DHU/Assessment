from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services.redaction import safe_endpoint
from app.models.db_models import AgentExecutionSettings, AgentProfile
from app.models.schemas import (
    AgentConnectionTestRequest,
    AgentExecutionConfig,
    AgentConnectionTestResponse,
    AgentModelsResponse,
    AgentProfileCreate,
    AgentProfileOut,
    AgentProfileUpdate,
    mask_api_key,
)
from app.services.llm_extractor import (
    list_available_models,
    normalize_base_url,
    test_llm_connection,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])


def agent_execution(db: Session, agent_id: str) -> AgentExecutionConfig:
    row = db.get(AgentExecutionSettings, agent_id)
    return AgentExecutionConfig.model_validate(row.config) if row else AgentExecutionConfig()


def save_execution(db: Session, agent_id: str, config: AgentExecutionConfig) -> None:
    db.merge(AgentExecutionSettings(agent_id=agent_id, config=config.model_dump()))


def _to_agent_out(db: Session, agent: AgentProfile) -> AgentProfileOut:
    has_custom = bool(agent.api_key.strip())
    using_env = not has_custom and bool(settings.default_llm_api_key.strip())
    return AgentProfileOut(
        id=agent.id,
        execution=agent_execution(db, agent.id),
        name=agent.name,
        description=agent.description,
        base_url=agent.base_url,
        model=agent.model,
        temperature=agent.temperature,
        max_tokens=agent.max_tokens,
        json_mode=agent.json_mode,
        require_source_quote=agent.require_source_quote,
        system_prompt=agent.system_prompt,
        business_rules=agent.business_rules,
        extraction_schema=agent.extraction_schema or {},
        api_key_masked=mask_api_key(agent.api_key) if has_custom else "",
        has_custom_api_key=has_custom,
        using_env_api_key=using_env,
        effective_base_url=safe_endpoint(normalize_base_url(agent.base_url) or settings.default_llm_base_url),
        effective_model=agent.model.strip() or settings.default_llm_model,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


@router.get("", response_model=list[AgentProfileOut])
def list_agents(db: Session = Depends(get_db)) -> list[AgentProfileOut]:
    agents = db.query(AgentProfile).order_by(AgentProfile.created_at.asc()).all()
    return [_to_agent_out(db, a) for a in agents]


@router.post("", response_model=AgentProfileOut, status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentProfileCreate, db: Session = Depends(get_db)) -> AgentProfileOut:
    agent = AgentProfile(**payload.model_dump(exclude={"execution"}))
    db.add(agent)
    db.flush()
    save_execution(db, agent.id, payload.execution)
    db.commit()
    db.refresh(agent)
    return _to_agent_out(db, agent)


@router.patch("/{agent_id}", response_model=AgentProfileOut)
def update_agent(agent_id: str, payload: AgentProfileUpdate, db: Session = Depends(get_db)) -> AgentProfileOut:
    agent = db.get(AgentProfile, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="智能体不存在")

    update_data = payload.model_dump(exclude_none=True, exclude={"execution"})
    if payload.execution is not None:
        save_execution(db, agent.id, payload.execution)
    for k, v in update_data.items():
        setattr(agent, k, v)

    db.commit()
    db.refresh(agent)
    return _to_agent_out(db, agent)


@router.post("/{agent_id}/duplicate", response_model=AgentProfileOut, status_code=status.HTTP_201_CREATED)
def duplicate_agent(agent_id: str, db: Session = Depends(get_db)) -> AgentProfileOut:
    src = db.get(AgentProfile, agent_id)
    if not src:
        raise HTTPException(status_code=404, detail="智能体不存在")

    copy_agent = AgentProfile(
        name=f"{src.name} (副本)",
        description=src.description,
        base_url=src.base_url,
        api_key=src.api_key,
        model=src.model,
        temperature=src.temperature,
        max_tokens=src.max_tokens,
        json_mode=src.json_mode,
        require_source_quote=src.require_source_quote,
        system_prompt=src.system_prompt,
        business_rules=src.business_rules,
        extraction_schema=src.extraction_schema,
    )
    db.add(copy_agent)
    db.flush()
    save_execution(db, copy_agent.id, agent_execution(db, src.id))
    db.commit()
    db.refresh(copy_agent)
    return _to_agent_out(db, copy_agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(agent_id: str, db: Session = Depends(get_db)) -> None:
    agent = db.get(AgentProfile, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="智能体不存在")
    if db.query(AgentProfile).count() <= 1:
        raise HTTPException(status_code=400, detail="至少保留一个智能体配置")
    runtime = db.get(AgentExecutionSettings, agent.id)
    if runtime:
        db.delete(runtime)
    db.delete(agent)
    db.commit()


def _resolve_test_credentials(
    payload: AgentConnectionTestRequest, db: Session
) -> tuple[str, str, str]:
    """Resolve effective (base_url, api_key, model) for draft connection probing."""
    eff_base_url = normalize_base_url(payload.base_url) or settings.default_llm_base_url
    eff_model = (payload.model or "").strip() or settings.default_llm_model

    eff_api_key = (payload.api_key or "").strip()
    if not eff_api_key and not payload.clear_api_key and payload.agent_id:
        saved = db.get(AgentProfile, payload.agent_id)
        if saved and saved.api_key.strip():
            eff_api_key = saved.api_key.strip()
    if not eff_api_key:
        eff_api_key = settings.default_llm_api_key.strip()

    return eff_base_url, eff_api_key, eff_model


@router.post("/test-connection", response_model=AgentConnectionTestResponse)
async def test_agent_connection(
    payload: AgentConnectionTestRequest,
    db: Session = Depends(get_db),
) -> AgentConnectionTestResponse:
    eff_base_url, eff_api_key, eff_model = _resolve_test_credentials(payload, db)
    result = await test_llm_connection(
        base_url=eff_base_url,
        api_key=eff_api_key,
        model=eff_model,
        json_mode=payload.json_mode,
        timeout_seconds=min(settings.llm_timeout_seconds, 25),
    )
    return AgentConnectionTestResponse(
        ok=result.ok,
        error_code=result.error_code,
        message=result.message,
        latency_ms=result.latency_ms,
        status_code=result.status_code,
        effective_base_url=safe_endpoint(result.effective_base_url),
        effective_model=result.effective_model,
        json_mode_supported=result.json_mode_supported,
        available_models=result.available_models,
    )


@router.post("/models", response_model=AgentModelsResponse)
async def fetch_agent_models(
    payload: AgentConnectionTestRequest,
    db: Session = Depends(get_db),
) -> AgentModelsResponse:
    eff_base_url, eff_api_key, _ = _resolve_test_credentials(payload, db)
    models, err = await list_available_models(
        base_url=eff_base_url,
        api_key=eff_api_key,
        timeout_seconds=15,
    )
    return AgentModelsResponse(
        models=models,
        effective_base_url=safe_endpoint(eff_base_url),
        error=err,
    )
