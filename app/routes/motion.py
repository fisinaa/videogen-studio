from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.motion import MotionGenerationError, motion_service
from app.storage import project_store


router = APIRouter(prefix="/api/motion", tags=["motion"])


def _decorate_motion_asset(asset):
    """Persist useful generation metadata in the existing MediaAsset fields.

    MediaAsset already has provider/author/label, so older saved projects remain
    compatible without a schema migration. The UI already renders label for each
    motion candidate, which makes these details visible immediately.
    """
    runtime = motion_service.runtime_status()
    provider = str(runtime.get("selected_provider") or asset.author or "unknown")
    tool = str(runtime.get("selected_tool") or "")
    elapsed = float(runtime.get("elapsed_seconds") or 0.0)
    model = ""
    estimated_cost = None

    if tool == "sora_video":
        model = "sora-2"
        seconds = float(asset.duration_seconds or 4.0)
        # OpenMontage's Sora provider currently exposes a placeholder estimate of
        # $0.50 per 4 seconds. Keep the UI explicit that this is an estimate.
        estimated_cost = 0.50 * (seconds / 4.0)
    elif tool:
        model = tool

    parts = ["AI motion", provider]
    if model:
        parts.append(model)
    if asset.duration_seconds:
        parts.append(f"{asset.duration_seconds:.1f}s clip")
    if elapsed > 0:
        parts.append(f"generated in {elapsed:.1f}s")
    if estimated_cost is not None:
        parts.append(f"est. ${estimated_cost:.2f}")

    return asset.model_copy(
        update={
            "author": model or provider,
            "label": " · ".join(parts),
        }
    )


@router.get("/status")
async def motion_status():
    return motion_service.status()


@router.get("/projects/{project_id}/scenes/{scene_id}")
async def list_scene_motion(project_id: str, scene_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    scene = next((item for item in project.storyboard.scenes if item.id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    return {
        "motion_mode": scene.motion_mode,
        "selected_motion_media": scene.selected_motion_media,
        "motion_candidates": scene.motion_candidates,
        "provider": motion_service.status(),
    }


@router.post("/projects/{project_id}/scenes/{scene_id}/generate")
async def generate_scene_motion(
    project_id: str,
    scene_id: str,
    duration_seconds: float | None = Query(default=None, ge=2.0, le=30.0),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        try:
            asset = await motion_service.generate(
                project,
                scene,
                duration_seconds=duration_seconds,
            )
            asset = _decorate_motion_asset(asset)
        except MotionGenerationError as exc:
            status = 503 if not motion_service.enabled else 502
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Motion generation failed: {exc}") from exc

        candidates = list(scene.motion_candidates)
        if not any(item.asset_id == asset.asset_id for item in candidates):
            candidates.append(asset)
        updated = scene.model_copy(
            update={
                "motion_candidates": candidates,
                "selected_motion_media": asset,
                "motion_mode": "image_to_video",
            }
        )
        project.storyboard.scenes[index] = updated
        project_store.save(project)
        return {"project": project, "asset": asset}

    raise HTTPException(status_code=404, detail="Scene not found")


@router.post("/projects/{project_id}/scenes/{scene_id}/select/{asset_id}")
async def select_scene_motion(project_id: str, scene_id: str, asset_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        asset = next((item for item in scene.motion_candidates if item.asset_id == asset_id), None)
        if asset is None:
            raise HTTPException(status_code=404, detail="Motion candidate not found")
        if asset.media_type != "video":
            raise HTTPException(status_code=400, detail="Motion candidate must be video")
        project.storyboard.scenes[index] = scene.model_copy(
            update={"selected_motion_media": asset, "motion_mode": "image_to_video"}
        )
        project_store.save(project)
        return project

    raise HTTPException(status_code=404, detail="Scene not found")


@router.post("/projects/{project_id}/scenes/{scene_id}/reset")
async def reset_scene_motion(project_id: str, scene_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        project.storyboard.scenes[index] = scene.model_copy(
            update={"selected_motion_media": None, "motion_mode": "camera_motion"}
        )
        project_store.save(project)
        return project

    raise HTTPException(status_code=404, detail="Scene not found")


@router.delete("/projects/{project_id}/scenes/{scene_id}/candidates/{asset_id}")
async def delete_scene_motion(project_id: str, scene_id: str, asset_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        if scene.selected_motion_media is not None and scene.selected_motion_media.asset_id == asset_id:
            raise HTTPException(status_code=409, detail="Select another motion clip or reset to image first")
        candidates = [item for item in scene.motion_candidates if item.asset_id != asset_id]
        if len(candidates) == len(scene.motion_candidates):
            raise HTTPException(status_code=404, detail="Motion candidate not found")

        victim = next((item for item in scene.motion_candidates if item.asset_id == asset_id), None)
        if victim is not None and victim.local_path:
            path = project_store.media_file(project.id, victim.local_path.split("/")[-1])
            if path is not None:
                path.unlink(missing_ok=True)

        project.storyboard.scenes[index] = scene.model_copy(update={"motion_candidates": candidates})
        project_store.save(project)
        return project

    raise HTTPException(status_code=404, detail="Scene not found")
