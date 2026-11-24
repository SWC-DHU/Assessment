from fastapi import APIRouter

from app.config import settings
from app.services.redaction import safe_endpoint
from app.services.seed import SAMPLE_DATASETS
from app.services.semantic_execution import DEFAULT_BATCH_OUTPUT, DEEPSEEK_BATCH_OUTPUT, DEEPSEEK_REQUEST_BUDGET

router = APIRouter(prefix="/api/samples", tags=["samples"])


@router.get("")
def get_samples_and_env_status() -> dict:
    return {
        "samples": SAMPLE_DATASETS,
        "env_status": {
            "has_default_api_key": bool(settings.default_llm_api_key.strip()),
            "default_base_url": safe_endpoint(settings.default_llm_base_url),
            "default_model": settings.default_llm_model,
            "semantic_batch_max_tokens": DEFAULT_BATCH_OUTPUT,
            "semantic_request_budget": settings.llm_request_budget,
            "deepseek_semantic_batch_max_tokens": DEEPSEEK_BATCH_OUTPUT,
            "deepseek_semantic_request_budget": DEEPSEEK_REQUEST_BUDGET,
        },
    }
