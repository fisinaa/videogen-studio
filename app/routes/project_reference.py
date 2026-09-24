from __future__ import annotations

import re
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from httpx import HTTPError

from app.config import settings
from app.main import _visual_bible, llm
from app.providers.llm.openai_storyboard import OpenAIStoryboardProvider
from app.schemas import CreateProjectRequest, LLMRunInfo, MediaAsset, Project
from app.storage import project_store


router = APIRouter(tags=["project-reference"])
openai_storyboard = OpenAIStoryboardProvider()

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_REFERENCE_BYTES = 20 * 1024 * 1024


def _safe_stem(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return (value or "reference")[:80]


async def _store_character_reference(
    project: Project,
    upload: UploadFile,
    *,
    name: str = "",
    prompt: str = "",
) -> MediaAsset:
    content_type = (upload.content_type or "").lower()
    suffix = Path(upload.filename or "").suffix.lower()
    if content_type not in _ALLOWED_IMAGE_TYPES and suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Character Reference must be JPG, PNG or WEBP")

    data = await upload.read(_MAX_REFERENCE_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded reference image is empty")
    if len(data) > _MAX_REFERENCE_BYTES:
        raise HTTPException(status_code=413, detail="Character Reference is larger than 20 MB")

    suffix = suffix if suffix in _ALLOWED_SUFFIXES else ".jpg"
    filename = f"character-reference-{uuid4().hex[:10]}-{_safe_stem(Path(upload.filename or 'reference').stem)}{suffix}"
    target = project_store.media_dir(project.id) / filename
    target.write_bytes(data)

    url = f"/api/projects/{project.id}/media/{filename}"
    asset = MediaAsset(
        provider="upload",
        asset_id=filename,
        media_type="image",
        preview_url=url,
        source_url=f"upload://{filename}",
        download_url=url,
        author="user",
        label=name.strip() or "Uploaded Character Reference",
        local_path=f"media/{filename}",
    )
    project.character_reference = asset
    project.character_reference_name = name.strip()[:200]
    project.character_reference_prompt = prompt.strip()[:4000]
    project_store.save(project)
    return asset


@router.post("/api/projects/interactive")
async def create_project_interactive(
    project_name: str = Form(default=""),
    prompt: str = Form(...),
    project_type: str = Form(default="cartoon"),
    aspect_ratio: str = Form(default="16:9"),
    duration_seconds: int = Form(default=180),
    language: str = Form(default="ru"),
    storyboard_provider: str = Form(default="local"),
    character_name: str = Form(default=""),
    character_prompt: str = Form(default=""),
    reference_image: UploadFile | None = File(default=None),
):
    payload = CreateProjectRequest(
        prompt=prompt,
        project_name=project_name.strip(),
        project_type=project_type,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
        language=language,
        llm_profile="quality",
    )

    started = time.perf_counter()
    provider_name = storyboard_provider.strip().lower()
    try:
        if provider_name == "openai":
            if not settings.openai_api_key:
                raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
            openai_storyboard.reset_calls()
            storyboard = await openai_storyboard.create_storyboard(payload)
            storyboard.llm_generation = LLMRunInfo(
                profile="quality",
                model_name=openai_storyboard.model_name,
                model_path="openai",
                operation="storyboard_openai",
                duration_seconds=round(time.perf_counter() - started, 2),
                calls=openai_storyboard.calls,
            )
        elif provider_name == "local":
            storyboard = await llm.create_storyboard(payload)
        else:
            raise HTTPException(status_code=400, detail="storyboard_provider must be local or openai")
    except HTTPException:
        raise
    except HTTPError as exc:
        response = getattr(exc, "response", None)
        detail = response.text[:1200] if response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"Storyboard provider request failed: {detail}") from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=f"Storyboard provider returned invalid data: {exc}") from exc

    storyboard.visual_bible = _visual_bible(storyboard)
    project = Project(
        id=uuid4().hex[:12],
        request=payload,
        storyboard=storyboard,
        name=project_name.strip()[:200],
        character_reference_name=character_name.strip()[:200],
        character_reference_prompt=character_prompt.strip()[:4000],
    )
    project_store.save(project)

    if reference_image is not None and reference_image.filename:
        await _store_character_reference(
            project,
            reference_image,
            name=character_name,
            prompt=character_prompt,
        )

    return project


@router.post("/api/projects/{project_id}/character-reference/upload")
async def upload_character_reference(
    project_id: str,
    file: UploadFile = File(...),
    name: str = Form(default=""),
    prompt: str = Form(default=""),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await _store_character_reference(project, file, name=name, prompt=prompt)
    return project


@router.delete("/api/projects/{project_id}/character-reference")
async def delete_character_reference(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    old = project.character_reference
    if old is not None and old.local_path:
        path = project_store.media_file(project.id, Path(old.local_path).name)
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass

    project.character_reference = None
    project.character_reference_name = ""
    project.character_reference_prompt = ""
    project_store.save(project)
    return project
