from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas import AudioAsset, MediaAsset
from app.services.production import probe_duration
from app.storage import project_store


router = APIRouter(prefix="/api/projects", tags=["project-files"])

_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _safe_name(original: str) -> str:
    source = Path(original or "upload.bin")
    suffix = source.suffix.lower()[:12]
    stem = _SAFE_STEM.sub("-", source.stem).strip("-._") or "upload"
    return f"upload-{uuid4().hex[:8]}-{stem[:64]}{suffix}"


def _kind_for(filename: str, content_type: str | None) -> str:
    suffix = Path(filename).suffix.lower()
    mime = (content_type or mimetypes.guess_type(filename)[0] or "").lower()
    if suffix in _IMAGE_EXTS or mime.startswith("image/"):
        return "image"
    if suffix in _VIDEO_EXTS or mime.startswith("video/"):
        return "video"
    if suffix in _AUDIO_EXTS or mime.startswith("audio/"):
        return "audio"
    raise HTTPException(status_code=415, detail="Supported uploads: image, video or audio files")


@router.post("/{project_id}/scenes/{scene_id}/upload")
async def upload_scene_file(project_id: str, scene_id: str, file: UploadFile = File(...)):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    scene_index = next((i for i, scene in enumerate(project.storyboard.scenes) if scene.id == scene_id), None)
    if scene_index is None:
        raise HTTPException(status_code=404, detail="Scene not found")

    filename = _safe_name(file.filename or "upload.bin")
    kind = _kind_for(filename, file.content_type)
    target_dir = project_store.audio_dir(project_id) if kind == "audio" else project_store.media_dir(project_id)
    target = target_dir / filename

    size = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Upload exceeds 512 MB limit")
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    scene = project.storyboard.scenes[scene_index]
    if kind == "audio":
        duration = probe_duration(target)
        asset = AudioAsset(
            provider="upload",
            asset_id=filename,
            audio_url=f"/api/projects/{project_id}/audio/{filename}",
            download_url=f"/api/projects/{project_id}/audio/{filename}",
            format=target.suffix.lstrip(".") or "audio",
            voice="",
            model="",
            label=f"Uploaded · {file.filename or filename}",
            local_path=f"audio/{filename}",
        )
        project.storyboard.scenes[scene_index] = scene.model_copy(
            update={
                "selected_audio": asset,
                "audio_duration_seconds": duration,
            }
        )
    else:
        duration = probe_duration(target) if kind == "video" else None
        local_url = f"/api/projects/{project_id}/media/{filename}"
        asset = MediaAsset(
            provider="upload",
            asset_id=filename,
            media_type=kind,
            preview_url=local_url,
            source_url=f"upload://{filename}",
            download_url=local_url,
            duration_seconds=duration,
            author="local upload",
            label=f"Uploaded · {file.filename or filename}",
            local_path=f"media/{filename}",
        )
        candidates = list(scene.media_candidates)
        candidates.append(asset)
        project.storyboard.scenes[scene_index] = scene.model_copy(
            update={
                "media_candidates": candidates,
                "selected_media": scene.selected_media or asset,
            }
        )

    project_store.save(project)
    return {"project": project, "asset": asset, "kind": kind, "size": size}


@router.delete("/{project_id}/files/{filename}")
async def hard_delete_project_file(project_id: str, filename: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    media_path = project_store.media_file(project_id, filename)
    audio_path = project_store.audio_file(project_id, filename)
    path = media_path or audio_path
    if path is None:
        raise HTTPException(status_code=404, detail="Project file not found")

    changed_refs = 0
    for index, scene in enumerate(project.storyboard.scenes):
        media_candidates = [item for item in scene.media_candidates if item.asset_id != filename]
        motion_candidates = [item for item in scene.motion_candidates if item.asset_id != filename]
        selected_media = scene.selected_media
        selected_motion = scene.selected_motion_media
        selected_audio = scene.selected_audio
        motion_mode = scene.motion_mode

        if selected_media is not None and selected_media.asset_id == filename:
            selected_media = media_candidates[0] if media_candidates else None
            changed_refs += 1
        if selected_motion is not None and selected_motion.asset_id == filename:
            selected_motion = None
            motion_mode = "camera_motion"
            changed_refs += 1
        if selected_audio is not None and selected_audio.asset_id == filename:
            selected_audio = None
            changed_refs += 1
        changed_refs += len(scene.media_candidates) - len(media_candidates)
        changed_refs += len(scene.motion_candidates) - len(motion_candidates)

        project.storyboard.scenes[index] = scene.model_copy(
            update={
                "media_candidates": media_candidates,
                "motion_candidates": motion_candidates,
                "selected_media": selected_media,
                "selected_motion_media": selected_motion,
                "selected_audio": selected_audio,
                "audio_duration_seconds": None if selected_audio is None else scene.audio_duration_seconds,
                "motion_mode": motion_mode,
            }
        )

    if project.character_reference is not None and project.character_reference.asset_id == filename:
        project.character_reference = None
        changed_refs += 1

    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"File deletion failed: {exc}") from exc

    project_store.save(project)
    return {
        "deleted": True,
        "filename": filename,
        "removed_references": changed_refs,
        "project": project,
    }
