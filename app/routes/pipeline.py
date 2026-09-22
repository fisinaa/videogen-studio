from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query

from app.main import _append_candidate, _generate_scene_image, _scene_speech_text
from app.providers.tts.router import tts_router
from app.services.motion import MotionGenerationError, motion_service
from app.services.production import sync_project
from app.storage import project_store


router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

_runtime: dict = {
    "state": "idle",
    "operation": None,
    "stage": None,
    "project_id": None,
    "current": 0,
    "total": 0,
    "scene_id": None,
    "detail": "",
    "started": None,
    "elapsed_seconds": 0.0,
}


def _set_runtime(**updates) -> None:
    _runtime.update(updates)
    started = _runtime.get("started")
    _runtime["elapsed_seconds"] = round(time.monotonic() - started, 1) if started else 0.0


def _status() -> dict:
    result = dict(_runtime)
    started = result.pop("started", None)
    result["elapsed_seconds"] = round(time.monotonic() - started, 1) if started else float(result.get("elapsed_seconds") or 0.0)
    return result


def _err(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


@router.get("/status")
async def pipeline_status():
    return _status()


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
    scenes = list(project.storyboard.scenes)
    _runtime.update({
        "state": "running", "operation": "animate-all", "stage": "motion",
        "project_id": project_id, "current": 0, "total": len(scenes),
        "scene_id": None, "detail": f"Provider: {preferred_provider}",
        "started": time.monotonic(), "elapsed_seconds": 0.0,
    })

    try:
        for index, scene in enumerate(scenes):
            _set_runtime(current=index + 1, scene_id=scene.id, detail=f"Motion {index + 1}/{len(scenes)} · {scene.id}")
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
    finally:
        _set_runtime(
            state="complete" if not errors else "complete-with-errors",
            scene_id=None,
            detail=f"Готово: {generated}, пропущено: {skipped}, ошибок: {len(errors)}",
        )

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
    total = max(1, len(project.storyboard.scenes) * 3)
    step = 0
    _runtime.update({
        "state": "running", "operation": "build-episode", "stage": "images",
        "project_id": project_id, "current": 0, "total": total,
        "scene_id": None, "detail": "Заполняю недостающие изображения",
        "started": time.monotonic(), "elapsed_seconds": 0.0,
    })

    try:
        # Stage 1: fill missing scene images.
        for index, scene in enumerate(project.storyboard.scenes):
            step += 1
            _set_runtime(stage="images", current=step, scene_id=scene.id, detail=f"Images · {scene.id}")
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

        project = project_store.load(project_id) or project

        # Stage 2: fill missing narration/dialogue audio.
        for index, scene in enumerate(project.storyboard.scenes):
            step += 1
            _set_runtime(stage="audio", current=step, scene_id=scene.id, detail=f"Audio · {scene.id}")
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
            step += 1
            _set_runtime(stage="motion", current=step, scene_id=scene.id, detail=f"Motion · {scene.id}")
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
        _set_runtime(stage="sync", current=total, scene_id=None, detail="Синхронизирую тайминги и OpenMontage workspace")
        sync_project(project, save=True)
        result["project"] = project_store.load(project_id) or project
        result["ready_for_studio"] = len(result["errors"]) == 0
        return result
    finally:
        _set_runtime(
            state="complete" if not result["errors"] else "complete-with-errors",
            current=total,
            scene_id=None,
            detail=(
                f"Build finished: images {result['images_generated']}, audio {result['audio_generated']}, "
                f"motion {result['motion_generated']}, errors {len(result['errors'])}"
            ),
        )
