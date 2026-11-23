from fastapi import APIRouter

from app.config import settings
from app.services.redaction import safe_endpoint
from app.services.seed import SAMPLE_DATASETS

router = APIRouter(prefix="/api/samples", tags=["samples"])


@router.get("")
def get_samples_and_env_status() -> dict:
    return {
        "samples": SAMPLE_DATASETS,
        "env_status": {
            "has_default_api_key": bool(settings.default_llm_api_key.strip()),
            "default_base_url": safe_endpoint(settings.default_llm_base_url),
            "default_model": settings.default_llm_model,
        },
    }
