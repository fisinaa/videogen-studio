from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.routes.media_files import hard_delete_project_file
from app.schemas import AudioAsset, MediaAsset
from app.services.production import probe_duration
from app.storage import project_store


router = APIRouter(prefix="/api/media-library", tags=["media-library"])

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


def _file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    return "file"


def _safe_upload_name(original: str) -> str:
    source = Path(original or "upload.bin")
    stem = _SAFE_STEM.sub("-", source.stem).strip("-._") or "upload"
    return f"upload-{uuid4().hex[:8]}-{stem[:64]}{source.suffix.lower()[:12]}"


def _usage_map(project) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}

    def add(filename: str | None, usage: str) -> None:
        if filename:
            result.setdefault(filename, []).append(usage)

    if project.character_reference is not None:
        add(project.character_reference.asset_id, "character reference")
    for scene in project.storyboard.scenes:
        if scene.selected_media is not None:
            add(scene.selected_media.asset_id, f"{scene.id}: selected media")
        if scene.selected_motion_media is not None:
            add(scene.selected_motion_media.asset_id, f"{scene.id}: selected motion")
        if scene.selected_audio is not None:
            add(scene.selected_audio.asset_id, f"{scene.id}: selected audio")
        for asset in scene.media_candidates:
            add(asset.asset_id, f"{scene.id}: media candidate")
        for asset in scene.motion_candidates:
            add(asset.asset_id, f"{scene.id}: motion candidate")
    return result


@router.get("/projects/{project_id}")
async def list_library(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    usages = _usage_map(project)
    items: list[dict] = []

    for bucket, directory in (
        ("media", project_store.media_dir(project_id)),
        ("audio", project_store.audio_dir(project_id)),
        ("render", project_store.render_dir(project_id)),
    ):
        for path in sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if not path.is_file():
                continue
            kind = "render" if bucket == "render" else _file_kind(path)
            url = (
                f"/api/openmontage/projects/{project_id}/renders/{path.name}"
                if bucket == "render"
                else f"/api/projects/{project_id}/{bucket}/{path.name}"
            )
            items.append({
                "filename": path.name,
                "kind": kind,
                "bucket": bucket,
                "size": path.stat().st_size,
                "url": url,
                "usages": usages.get(path.name, []),
                "in_use": bool(usages.get(path.name)),
            })

    return {"project_id": project_id, "items": items}


@router.post("/projects/{project_id}/upload")
async def upload_to_library(project_id: str, file: UploadFile = File(...)):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    filename = _safe_upload_name(file.filename or "upload.bin")
    content_type = (file.content_type or mimetypes.guess_type(filename)[0] or "").lower()
    ext = Path(filename).suffix.lower()
    is_audio = ext in _AUDIO_EXTS or content_type.startswith("audio/")
    is_media = ext in (_IMAGE_EXTS | _VIDEO_EXTS) or content_type.startswith("image/") or content_type.startswith("video/")
    if not (is_audio or is_media):
        raise HTTPException(status_code=415, detail="Supported uploads: image, video or audio")

    target = (project_store.audio_dir(project_id) if is_audio else project_store.media_dir(project_id)) / filename
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

    return {"uploaded": True, "filename": filename, "size": size, "kind": _file_kind(target)}


@router.delete("/projects/{project_id}/{bucket}/{filename}")
async def delete_library_file(project_id: str, bucket: str, filename: str):
    if bucket in {"media", "audio"}:
        return await hard_delete_project_file(project_id, filename)
    if bucket != "render":
        raise HTTPException(status_code=400, detail="Unknown library bucket")
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    path = project_store.render_file(project_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Render not found")
    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Render deletion failed: {exc}") from exc
    return {"deleted": True, "filename": filename, "bucket": bucket}


@router.post("/projects/{project_id}/assign/{scene_id}/{filename}")
async def assign_library_file(
    project_id: str,
    scene_id: str,
    filename: str,
    role: str = Query(default="media", pattern="^(media|motion|audio)$"),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    index = next((i for i, s in enumerate(project.storyboard.scenes) if s.id == scene_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    scene = project.storyboard.scenes[index]

    if role == "audio":
        path = project_store.audio_file(project_id, filename)
        if path is None:
            raise HTTPException(status_code=404, detail="Audio file not found")
        asset = AudioAsset(
            provider="library",
            asset_id=filename,
            audio_url=f"/api/projects/{project_id}/audio/{filename}",
            download_url=f"/api/projects/{project_id}/audio/{filename}",
            format=path.suffix.lstrip(".") or "audio",
            voice="",
            model="",
            label=f"Library · {filename}",
            local_path=f"audio/{filename}",
        )
        project.storyboard.scenes[index] = scene.model_copy(update={
            "selected_audio": asset,
            "audio_duration_seconds": probe_duration(path),
        })
    else:
        path = project_store.media_file(project_id, filename)
        if path is None:
            raise HTTPException(status_code=404, detail="Media file not found")
        kind = _file_kind(path)
        if role == "motion" and kind != "video":
            raise HTTPException(status_code=400, detail="Motion role requires a video file")
        if role == "media" and kind not in {"image", "video"}:
            raise HTTPException(status_code=400, detail="Media role requires image or video")
        url = f"/api/projects/{project_id}/media/{filename}"
        asset = MediaAsset(
            provider="library",
            asset_id=filename,
            media_type=kind,
            preview_url=url,
            source_url=f"library://{filename}",
            download_url=url,
            duration_seconds=probe_duration(path) if kind == "video" else None,
            author="project library",
            label=f"Library · {filename}",
            local_path=f"media/{filename}",
        )
        if role == "motion":
            candidates = list(scene.motion_candidates)
            if not any(x.asset_id == filename for x in candidates):
                candidates.append(asset)
            project.storyboard.scenes[index] = scene.model_copy(update={
                "motion_candidates": candidates,
                "selected_motion_media": asset,
                "motion_mode": "image_to_video",
            })
        else:
            candidates = list(scene.media_candidates)
            if not any(x.asset_id == filename for x in candidates):
                candidates.append(asset)
            project.storyboard.scenes[index] = scene.model_copy(update={
                "media_candidates": candidates,
                "selected_media": asset,
            })

    project_store.save(project)
    return {"project": project, "assigned": filename, "scene_id": scene_id, "role": role}
