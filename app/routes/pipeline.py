from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.main import _append_candidate, _generate_scene_image, _scene_speech_text
from app.providers.tts.router import tts_router
from app.services.motion import MotionGenerationError, motion_service
from app.services.production import sync_project
from app.storage import project_store


router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


def _err(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


@router.post("/projects/{project_id}/animate-all")
async def animate_all(
    project_id: str,
    preferred_provider: str = Query(default="openai"),
    only_missing: bool = Query(default=True),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    generated = 0
    skipped = 0
    errors: list[dict] = []

    for index, scene in enumerate(project.storyboard.scenes):
        if scene.selected_media is None or scene.selected_media.media_type != "image":
            skipped += 1
            continue
        if only_missing and scene.selected_motion_media is not None:
            skipped += 1
            continue
        try:
            asset = await motion_service.generate(
                project,
                scene,
                duration_seconds=float(scene.final_duration_seconds or scene.duration_seconds or 10.0),
                preferred_provider=preferred_provider,
            )
            candidates = list(scene.motion_candidates)
            if not any(item.asset_id == asset.asset_id for item in candidates):
                candidates.append(asset)
            project.storyboard.scenes[index] = scene.model_copy(
                update={
                    "motion_candidates": candidates,
                    "selected_motion_media": asset,
                    "motion_mode": "image_to_video",
                }
            )
            project_store.save(project)
            generated += 1
        except Exception as exc:
            errors.append({"scene_id": scene.id, "error": _err(exc)})

    return {
        "project": project,
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "provider": preferred_provider,
    }


@router.post("/projects/{project_id}/build")
async def build_episode(
    project_id: str,
    image_provider: str = Query(default="local_next"),
    motion_provider: str = Query(default="openai"),
):
    """Fill missing production assets, preserving explicit scene choices.

    Images are generated only when a scene has no selected media. Audio is generated
    only when speech exists and no selected audio is present. Motion is generated only
    for scenes already marked image_to_video but missing their selected motion clip, so
    this endpoint never silently turns camera-motion scenes into paid cloud generations.
    """
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    result = {
        "images_generated": 0,
        "audio_generated": 0,
        "motion_generated": 0,
        "skipped": 0,
        "errors": [],
    }

    # Stage 1: fill missing scene images.
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.selected_media is not None:
            continue
        try:
            asset = await _generate_scene_image(
                project,
                scene,
                image_provider,
                image_provider == "openai" and project.character_reference is not None,
            )
            project.storyboard.scenes[index] = _append_candidate(scene, asset)
            project_store.save(project)
            result["images_generated"] += 1
        except Exception as exc:
            result["errors"].append({"stage": "image", "scene_id": scene.id, "error": _err(exc)})

    # Reload because image generation persisted scene changes.
    project = project_store.load(project_id) or project

    # Stage 2: fill missing narration/dialogue audio.
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.selected_audio is not None:
            continue
        text = _scene_speech_text(scene)
        if not text:
            result["skipped"] += 1
            continue
        try:
            asset = await tts_router.generate(
                text=text,
                project_id=project.id,
                scene_id=scene.id,
                audio_dir=project_store.audio_dir(project.id),
            )
            project.storyboard.scenes[index] = scene.model_copy(update={"selected_audio": asset})
            project_store.save(project)
            result["audio_generated"] += 1
        except Exception as exc:
            result["errors"].append({"stage": "audio", "scene_id": scene.id, "error": _err(exc)})

    project = project_store.load(project_id) or project

    # Stage 3: only restore missing motion for scenes explicitly configured for I2V.
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.motion_mode != "image_to_video" or scene.selected_motion_media is not None:
            continue
        if scene.selected_media is None or scene.selected_media.media_type != "image":
            result["errors"].append({
                "stage": "motion",
                "scene_id": scene.id,
                "error": "Scene is image_to_video but has no selected source image",
            })
            continue
        try:
            asset = await motion_service.generate(
                project,
                scene,
                duration_seconds=float(scene.final_duration_seconds or scene.duration_seconds or 10.0),
                preferred_provider=motion_provider,
            )
            candidates = list(scene.motion_candidates)
            if not any(item.asset_id == asset.asset_id for item in candidates):
                candidates.append(asset)
            project.storyboard.scenes[index] = scene.model_copy(
                update={"motion_candidates": candidates, "selected_motion_media": asset}
            )
            project_store.save(project)
            result["motion_generated"] += 1
        except MotionGenerationError as exc:
            result["errors"].append({"stage": "motion", "scene_id": scene.id, "error": _err(exc)})
        except Exception as exc:
            result["errors"].append({"stage": "motion", "scene_id": scene.id, "error": _err(exc)})

    project = project_store.load(project_id) or project
    sync_project(project, save=True)
    result["project"] = project_store.load(project_id) or project
    result["ready_for_studio"] = len(result["errors"]) == 0
    return result
