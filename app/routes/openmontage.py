from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.integrations.openmontage import openmontage
from app.storage import project_store


router = APIRouter(prefix="/api/openmontage", tags=["openmontage"])


@router.get("/status")
async def openmontage_status():
    return await openmontage.status()


@router.get("/logs/latest")
async def latest_openmontage_preview_log(
    tail: int = Query(default=12000, ge=500, le=100000),
):
    paths = sorted(
        Path("/tmp").glob("videogen-openmontage-preview-*.log"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    if not paths:
        return {"found": False, "path": None, "content": ""}

    path = paths[0]
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot read OpenMontage log: {exc}") from exc

    return {
        "found": True,
        "path": str(path),
        "size": path.stat().st_size,
        "content": content[-tail:],
    }


@router.post("/projects/{project_id}/studio")
async def open_project_studio(project_id: str, request: Request):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        payload = await openmontage.preview(project)
        port = int(payload.get("port") or 3002)
        studio_path = str(payload.get("studio_path") or "/")

        # HyperFrames preview binds to localhost. Returning the VideoGen host here
        # sends the browser to e.g. 192.168.x.x:<port>, where nothing is listening.
        # Use the address HyperFrames actually exposes.
        payload["url"] = f"http://127.0.0.1:{port}{studio_path}"
        return payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenMontage Studio failed: {exc}") from exc


@router.post("/projects/{project_id}/render")
async def render_project(
    project_id: str,
    runtime: str = Query(pattern="^(hyperframes|remotion)$"),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return await openmontage.render(project, runtime)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenMontage render failed: {exc}") from exc


@router.get("/projects/{project_id}/renders")
async def list_renders(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    render_dir = project_store.render_dir(project_id)
    items = []
    for path in sorted(render_dir.glob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        items.append({
            "filename": path.name,
            "size": path.stat().st_size,
            "download_url": f"/api/openmontage/projects/{project_id}/renders/{path.name}",
        })
    return {"renders": items}


@router.get("/projects/{project_id}/renders/{filename}")
async def get_render(project_id: str, filename: str):
    path = project_store.render_file(project_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Render not found")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store, max-age=0"})
