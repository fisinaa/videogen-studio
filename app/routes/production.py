from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas import MediaAsset
from app.services.production import sync_project
from app.storage import project_store


router = APIRouter(prefix="/api/production", tags=["production"])


@router.post("/projects/{project_id}/sync")
async def sync_project_timing(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project = sync_project(project, save=True)
    return project


@router.post("/projects/{project_id}/scenes/{scene_id}/motion/select")
async def select_motion_media(project_id: str, scene_id: str, payload: MediaAsset):
    if payload.media_type != "video":
        raise HTTPException(status_code=400, detail="Motion asset must be video")
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        candidates = list(scene.motion_candidates)
        if not any(item.asset_id == payload.asset_id for item in candidates):
            candidates.append(payload)
        project.storyboard.scenes[index] = scene.model_copy(
            update={
                "motion_candidates": candidates,
                "selected_motion_media": payload,
                "motion_mode": "image_to_video",
            }
        )
        project_store.save(project)
        return project
    raise HTTPException(status_code=404, detail="Scene not found")


@router.post("/projects/{project_id}/scenes/{scene_id}/motion/disable")
async def disable_motion_media(project_id: str, scene_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        project.storyboard.scenes[index] = scene.model_copy(update={"motion_mode": "camera_motion"})
        project_store.save(project)
        return project
    raise HTTPException(status_code=404, detail="Scene not found")
