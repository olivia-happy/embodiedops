"""Credential-safe readiness reporting for the optional local model."""

from fastapi import APIRouter, Depends

from signalforge.api.deps import get_settings
from signalforge.api.schemas import ModelHealthResponse
from signalforge.core.config import Settings
from signalforge.services.local_model import (
    LOCAL_MODEL_UNAVAILABLE,
    LocalModelError,
    LocalModelProvider,
)

router = APIRouter(tags=["health"])


@router.get("/healthz/model", response_model=ModelHealthResponse)
async def model_health(settings: Settings = Depends(get_settings)) -> ModelHealthResponse:
    """Report local-model readiness without returning endpoints, prompts or credentials."""

    provider = LocalModelProvider(settings)
    if not provider.configured:
        return ModelHealthResponse(
            configured=False,
            ready=False,
            provider=provider.provider_name,
            model_name=provider.model_name,
            error_code=LOCAL_MODEL_UNAVAILABLE,
        )

    try:
        ready = await provider.health()
    except LocalModelError as exc:
        return ModelHealthResponse(
            configured=True,
            ready=False,
            provider=provider.provider_name,
            model_name=provider.model_name,
            error_code=exc.code,
        )
    return ModelHealthResponse(
        configured=True,
        ready=ready,
        provider=provider.provider_name,
        model_name=provider.model_name,
        error_code=None,
    )
