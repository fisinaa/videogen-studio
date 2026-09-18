from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from httpx import HTTPError

from app.config import settings
from app.providers.llm.llama_cpp import LlamaCppProvider
from app.providers.media.router import media_router
from app.schemas import CreateProjectRequest, MediaAsset, Project, SceneUpdate
from app.storage import project_store


app = FastAPI(title="VideoGen Studio", version="0.3.0")
templates = Jinja2Templates(directory="app/templates")
llm = LlamaCppProvider()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "projects": project_store.list_projects()[:10],
            "llm_url": settings.llm_base_url,
            "media_status": media_router.status(),
        },
    )


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "llm_url": settings.llm_base_url,
        "media_providers": media_router.status(),
    }


@app.get("/api/media/status")
async def media_status():
    return media_router.status()


@app.get("/api/projects")
async def projects():
    return project_store.list_projects()


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.post("/api/projects")
async def create_project(payload: CreateProjectRequest):
    try:
        storyboard = await llm.create_storyboard(payload)
    except HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM backend request failed: {exc}",
        ) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM returned an invalid storyboard: {exc}",
        ) from exc

    project = Project(
        id=uuid4().hex[:12],
        request=payload,
        storyboard=storyboard,
    )
    project_store.save(project)
    return project


@app.put("/api/projects/{project_id}/scenes/{scene_id}")
async def update_scene(project_id: str, scene_id: str, payload: SceneUpdate):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue

        updated = scene.model_copy(
            update={
                "title": payload.title,
                "duration_seconds": payload.duration_seconds,
                "narration": payload.narration,
                "dialogue": payload.dialogue,
                "action": payload.action,
                "visual_prompt": payload.visual_prompt,
                "media_search_query": payload.media_search_query,
            }
        )
        project.storyboard.scenes[index] = updated
        project.request.duration_seconds = round(
            sum(item.duration_seconds for item in project.storyboard.scenes)
        )
        project_store.save(project)
        return project

    raise HTTPException(status_code=404, detail="Scene not found")


@app.post("/api/projects/{project_id}/scenes/{scene_id}/regenerate")
async def regenerate_scene(project_id: str, scene_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue

        try:
            regenerated = await llm.regenerate_scene(
                project.request,
                project.storyboard,
                scene,
            )
        except HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"LLM backend request failed: {exc}",
            ) from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=502,
                detail=f"LLM returned an invalid scene: {exc}",
            ) from exc

        regenerated.selected_media = scene.selected_media
        project.storyboard.scenes[index] = regenerated
        project_store.save(project)
        return project

    raise HTTPException(status_code=404, detail="Scene not found")


@app.get("/api/projects/{project_id}/scenes/{scene_id}/media/search")
async def search_scene_media(
    project_id: str,
    scene_id: str,
    query: str | None = Query(default=None, max_length=1000),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    scene = next((item for item in project.storyboard.scenes if item.id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")

    search_query = (query or scene.media_search_query or scene.visual_prompt).strip()
    if not search_query:
        raise HTTPException(status_code=400, detail="Media search query is empty")

    status = media_router.status()
    if not any(status.values()):
        raise HTTPException(
            status_code=503,
            detail="No media providers enabled. Add PEXELS_API_KEY and/or PIXABAY_API_KEY to .env",
        )

    assets = await media_router.search(search_query, limit_per_provider=4)
    return {
        "query": search_query,
        "providers": status,
        "results": assets,
    }


@app.post("/api/projects/{project_id}/scenes/{scene_id}/media/select")
async def select_scene_media(project_id: str, scene_id: str, payload: MediaAsset):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        project.storyboard.scenes[index] = scene.model_copy(
            update={"selected_media": payload}
        )
        project_store.save(project)
        return project

    raise HTTPException(status_code=404, detail="Scene not found")
