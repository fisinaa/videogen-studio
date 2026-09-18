from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from httpx import HTTPError

from app.config import settings
from app.providers.llm.llama_cpp import LlamaCppProvider
from app.schemas import CreateProjectRequest, Project
from app.storage import project_store


app = FastAPI(title="VideoGen Studio", version="0.1.0")
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
        },
    )


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "llm_url": settings.llm_base_url,
    }


@app.get("/api/projects")
async def projects():
    return project_store.list_projects()


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
