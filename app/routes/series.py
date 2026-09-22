from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.main import _visual_bible, llm
from app.providers.media.router import media_router
from app.schemas import CreateProjectRequest, MediaAsset, Project, ReferenceKind, SeriesReference
from app.storage import project_store


router = APIRouter(prefix="/api/series", tags=["series"])

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


class CreateEpisodePayload(BaseModel):
    prompt: str = Field(default="", max_length=10000)
    duration_seconds: int | None = Field(default=None, ge=10, le=7200)


class AddReferencePayload(BaseModel):
    filename: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    kind: ReferenceKind = "object"
    from_episode: int = Field(default=1, ge=1)
    to_episode: int | None = Field(default=None, ge=1)


def _series_root(project: Project) -> Project:
    root_id = project.series_id or (project.id if project.request.project_type == "series" else None)
    if not root_id:
        raise HTTPException(status_code=400, detail="Project is not part of a series")
    root = project_store.load(root_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Series root project not found")
    return root


def _episode_projects(root_id: str) -> list[Project]:
    result: list[Project] = []
    for raw in project_store.list_projects():
        try:
            item = Project.model_validate(raw)
        except Exception:
            continue
        if item.id == root_id or item.series_id == root_id:
            result.append(item)
    result.sort(key=lambda p: (p.episode_number or 10**9, p.id))
    return result


def _reference_context(root: Project, episode_number: int) -> str:
    active = []
    for ref in root.series_references:
        if episode_number < ref.from_episode:
            continue
        if ref.to_episode is not None and episode_number > ref.to_episode:
            continue
        active.append(ref)
    if not active:
        return ""
    lines = ["SERIES CONTINUITY REFERENCES (must remain visually stable):"]
    for ref in active:
        scope = f"episodes {ref.from_episode}+" if ref.to_episode is None else f"episodes {ref.from_episode}-{ref.to_episode}"
        lines.append(f"- {ref.kind}: {ref.name} ({scope}) — {ref.description or 'match the canonical reference image exactly'}")
    return "\n".join(lines)


def _sync_episode_from_root(episode: Project, root: Project) -> Project:
    episode.series_id = root.id
    episode.series_title = root.series_title or root.storyboard.title
    episode.character_reference = root.character_reference
    episode.series_references = list(root.series_references)
    return episode


@router.post("/{project_id}/initialize")
async def initialize_series(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.request.project_type not in {"series", "series_episode"}:
        raise HTTPException(status_code=400, detail="Project type is not series")

    if project.request.project_type == "series":
        project.series_id = project.id
        project.episode_number = project.episode_number or 1
        project.series_title = project.series_title or project.storyboard.title
    elif not project.series_id:
        raise HTTPException(status_code=400, detail="Series episode has no series_id")

    project_store.save(project)
    return {"project": project, "series": await get_series(project.series_id or project.id)}


@router.get("/tree")
async def series_tree():
    projects: list[Project] = []
    for raw in project_store.list_projects():
        try:
            projects.append(Project.model_validate(raw))
        except Exception:
            continue

    roots: dict[str, Project] = {}
    for project in projects:
        if project.request.project_type == "series":
            roots[project.id] = project
        elif project.series_id and project.series_id == project.id:
            roots[project.id] = project

    groups = []
    for root_id, root in roots.items():
        episodes = [p for p in projects if p.id == root_id or p.series_id == root_id]
        episodes.sort(key=lambda p: (p.episode_number or 10**9, p.id))
        groups.append({
            "series_id": root_id,
            "title": root.series_title or root.storyboard.title,
            "character_reference": root.character_reference,
            "references": root.series_references,
            "episodes": [
                {
                    "id": p.id,
                    "episode_number": p.episode_number or (1 if p.id == root_id else None),
                    "title": p.storyboard.title,
                }
                for p in episodes
            ],
        })
    groups.sort(key=lambda x: x["title"].lower())
    return {"series": groups}


@router.get("/{project_id}")
async def get_series(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _series_root(project)
    episodes = _episode_projects(root.id)
    return {
        "series_id": root.id,
        "title": root.series_title or root.storyboard.title,
        "root": root,
        "character_reference": root.character_reference,
        "references": root.series_references,
        "episodes": episodes,
    }


@router.post("/{project_id}/character-reference/generate")
async def generate_series_character_reference(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _series_root(project)

    characters = "; ".join(root.storyboard.characters).strip() or "the main recurring character described by the series"
    prompt = "\n".join([
        "Create the canonical character reference image for a recurring animated series.",
        f"Series: {root.series_title or root.storyboard.title}",
        f"Character description: {characters}",
        f"Series visual style: {root.storyboard.visual_style}",
        "Show the main recurring character clearly, full body, neutral standing pose, simple uncluttered background, readable silhouette, stable proportions, colors, face, clothing and distinctive features. This exact design must remain consistent across all episodes. No captions, labels, text, watermark, extra characters or grid.",
    ])
    provider = "openai" if media_router.openai_image.enabled else "local_fast"
    try:
        asset = await media_router.generate_image(
            prompt=prompt,
            aspect_ratio="1:1",
            project_id=root.id,
            scene_id="series-character-reference",
            media_dir=project_store.media_dir(root.id),
            provider=provider,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Series Character Reference generation failed: {exc}") from exc

    root.character_reference = asset
    root.series_id = root.id
    root.episode_number = root.episode_number or 1
    root.series_title = root.series_title or root.storyboard.title
    project_store.save(root)
    sync = await sync_series_references(root.id)
    return {"project": root, "asset": asset, "synced": sync["synced"]}


@router.post("/{project_id}/episodes")
async def create_episode(project_id: str, payload: CreateEpisodePayload):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _series_root(project)
    if not root.series_id:
        root.series_id = root.id
        root.episode_number = root.episode_number or 1
        root.series_title = root.series_title or root.storyboard.title
        project_store.save(root)

    episodes = _episode_projects(root.id)
    next_number = max((item.episode_number or 0 for item in episodes), default=0) + 1
    user_prompt = payload.prompt.strip() or f"Продолжи сериал «{root.series_title or root.storyboard.title}». Создай серию {next_number}, сохраняя персонажей, мир и визуальную непрерывность предыдущих серий."
    continuity = _reference_context(root, next_number)
    prompt = "\n\n".join(part for part in [
        user_prompt,
        f"SERIES TITLE: {root.series_title or root.storyboard.title}",
        f"SERIES VISUAL STYLE: {root.storyboard.visual_style}",
        f"CANONICAL CHARACTERS: {'; '.join(root.storyboard.characters)}",
        continuity,
        f"This is episode {next_number}. Do not redesign recurring characters or recurring objects.",
    ] if part)

    request = CreateProjectRequest(
        prompt=prompt,
        project_type="series_episode",
        aspect_ratio=root.request.aspect_ratio,
        duration_seconds=payload.duration_seconds or root.request.duration_seconds,
        language=root.request.language,
    )
    try:
        storyboard = await llm.create_storyboard(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Episode storyboard generation failed: {exc}") from exc

    storyboard.visual_bible = _visual_bible(storyboard)
    child = Project(
        id=uuid4().hex[:12],
        request=request,
        storyboard=storyboard,
        character_reference=root.character_reference,
        series_id=root.id,
        series_title=root.series_title or root.storyboard.title,
        episode_number=next_number,
        series_references=list(root.series_references),
    )
    child = _sync_episode_from_root(child, root)
    project_store.save(child)
    return {"project": child, "series": await get_series(root.id)}


@router.post("/{project_id}/references")
async def add_series_reference(project_id: str, payload: AddReferencePayload):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _series_root(project)
    if payload.to_episode is not None and payload.to_episode < payload.from_episode:
        raise HTTPException(status_code=400, detail="to_episode must be >= from_episode")

    path = project_store.media_file(root.id, Path(payload.filename).name)
    if path is None or path.suffix.lower() not in _IMAGE_EXTS:
        raise HTTPException(status_code=404, detail="Reference image must exist in the series root Media Manager")

    url = f"/api/projects/{root.id}/media/{path.name}"
    asset = MediaAsset(
        provider="series_reference",
        asset_id=path.name,
        media_type="image",
        preview_url=url,
        source_url=f"series://{root.id}/{path.name}",
        download_url=url,
        author="series continuity",
        label=f"Series Reference · {payload.name}",
        local_path=f"media/{path.name}",
    )
    reference = SeriesReference(
        id=uuid4().hex[:10],
        name=payload.name,
        description=payload.description,
        kind=payload.kind,
        asset=asset,
        from_episode=payload.from_episode,
        to_episode=payload.to_episode,
    )
    root.series_references = [*root.series_references, reference]
    project_store.save(root)
    await sync_series_references(root.id)
    return {"reference": reference, "series": await get_series(root.id)}


@router.delete("/{project_id}/references/{reference_id}")
async def delete_series_reference(project_id: str, reference_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _series_root(project)
    refs = [ref for ref in root.series_references if ref.id != reference_id]
    if len(refs) == len(root.series_references):
        raise HTTPException(status_code=404, detail="Series reference not found")
    root.series_references = refs
    project_store.save(root)
    await sync_series_references(root.id)
    return {"deleted": True, "series": await get_series(root.id)}


@router.post("/{project_id}/sync-references")
async def sync_series_references(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _series_root(project)
    updated = 0
    for episode in _episode_projects(root.id):
        episode = _sync_episode_from_root(episode, root)
        project_store.save(episode)
        updated += 1
    return {"synced": updated, "series_id": root.id}
