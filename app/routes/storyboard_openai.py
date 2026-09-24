from __future__ import annotations

import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from httpx import HTTPError

from app.config import settings
from app.main import _visual_bible
from app.providers.llm.openai_storyboard import OpenAIStoryboardProvider
from app.schemas import CreateProjectRequest, LLMRunInfo, Project
from app.storage import project_store


router = APIRouter(tags=["storyboard-openai"])
provider = OpenAIStoryboardProvider()


@router.get("/api/storyboard-provider/status")
async def storyboard_provider_status():
    return {
        "local": True,
        "openai": bool(settings.openai_api_key),
        "openai_model": provider.model_name,
    }


@router.post("/api/projects/openai")
async def create_project_openai(payload: CreateProjectRequest):
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")

    provider.reset_calls()
    started = time.perf_counter()
    try:
        storyboard = await provider.create_storyboard(payload)
    except HTTPError as exc:
        response = getattr(exc, "response", None)
        detail = response.text[:1200] if response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"OpenAI storyboard request failed: {detail}") from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI returned an invalid storyboard: {exc}") from exc

    storyboard.visual_bible = _visual_bible(storyboard)
    storyboard.llm_generation = LLMRunInfo(
        profile="quality",
        model_name=provider.model_name,
        model_path="openai",
        operation="storyboard_openai",
        duration_seconds=round(time.perf_counter() - started, 2),
        calls=provider.calls,
    )
    project = Project(id=uuid4().hex[:12], request=payload, storyboard=storyboard)
    project_store.save(project)
    return project
