from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from httpx import HTTPError

from app.config import settings
from app.providers.llm.llama_cpp import LlamaCppProvider
from app.providers.media.router import media_router
from app.providers.tts.router import tts_router
from app.schemas import CreateProjectRequest, MediaAsset, Project, SceneUpdate
from app.storage import project_store


app = FastAPI(title="VideoGen Studio", version="0.10.0")
templates = Jinja2Templates(directory="app/templates")
llm = LlamaCppProvider()


def _image_dimensions(aspect_ratio: str) -> tuple[int, int]:
    return {
        "16:9": (1536, 1024),
        "9:16": (1024, 1536),
        "1:1": (1024, 1024),
    }.get(aspect_ratio, (1536, 1024))


def _local_image_dimensions(aspect_ratio: str) -> tuple[int, int]:
    return {
        "16:9": (settings.sd_cpp_width_16_9, settings.sd_cpp_height_16_9),
        "9:16": (settings.sd_cpp_width_9_16, settings.sd_cpp_height_9_16),
        "1:1": (settings.sd_cpp_width_1_1, settings.sd_cpp_height_1_1),
    }.get(aspect_ratio, (settings.sd_cpp_width_16_9, settings.sd_cpp_height_16_9))


def _asset_from_generated_file(project: Project, scene_id: str, path: Path) -> MediaAsset:
    if "-openai-" in path.name:
        provider = "openai_image"
        width, height = _image_dimensions(project.request.aspect_ratio)
        source_url = f"openai://recovered/{path.name}"
        author = "OpenAI"
        label = f"Recovered OpenAI image · {scene_id}"
    else:
        provider = "local_image"
        width, height = _local_image_dimensions(project.request.aspect_ratio)
        source_url = f"local://recovered/{path.name}"
        author = "local"
        label = f"Recovered local image · {scene_id}"
    local_url = f"/api/projects/{project.id}/media/{path.name}"
    return MediaAsset(
        provider=provider,
        asset_id=path.name,
        media_type="image",
        preview_url=local_url,
        source_url=source_url,
        download_url=local_url,
        width=width,
        height=height,
        duration_seconds=None,
        author=author,
        label=label,
        local_path=f"media/{path.name}",
    )


def _append_candidate(scene, asset: MediaAsset):
    candidates = list(scene.media_candidates)
    if not any(item.asset_id == asset.asset_id for item in candidates):
        candidates.append(asset)
    selected = scene.selected_media or asset
    return scene.model_copy(update={"media_candidates": candidates, "selected_media": selected})


def _recover_generated_media(project: Project) -> int:
    media_dir = project_store.media_dir(project.id)
    recovered = 0
    changed = False
    for index, scene in enumerate(project.storyboard.scenes):
        updated = scene
        known = {item.asset_id for item in scene.media_candidates}
        if scene.selected_media is not None:
            known.add(scene.selected_media.asset_id)
            if not any(x.asset_id == scene.selected_media.asset_id for x in updated.media_candidates):
                updated = _append_candidate(updated, scene.selected_media)
                changed = True

        files = []
        files.extend(path for path in media_dir.glob(f"{scene.id}-openai-*.png") if path.is_file())
        files.extend(path for path in media_dir.glob(f"{scene.id}-local-*.png") if path.is_file())
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            if path.name in known:
                continue
            asset = _asset_from_generated_file(project, scene.id, path)
            updated = _append_candidate(updated, asset)
            known.add(path.name)
            recovered += 1
            changed = True
        project.storyboard.scenes[index] = updated
    if changed:
        project_store.save(project)
    return recovered


def _character_reference_path(project: Project) -> Path | None:
    reference = project.character_reference
    if reference is None or not reference.local_path:
        return None
    return project_store.media_file(project.id, Path(reference.local_path).name)


def _error_detail(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    detail = response.text[:1000] if response is not None else str(exc)
    return detail or exc.__class__.__name__


def _scene_speech_text(scene) -> str:
    narration = scene.narration.strip()
    if narration:
        return narration
    return "\n".join(line.strip() for line in scene.dialogue if line.strip()).strip()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    tts_status = tts_router.status()
    media_status = media_router.status()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "projects": project_store.list_projects()[:10],
            "llm_url": settings.llm_base_url,
            "media_status": media_status,
            "tts_enabled": bool(tts_status["piper"] or tts_status["openai"]),
            "tts_voice": settings.openai_tts_voice,
            "tts_status": tts_status,
        },
    )


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "llm_url": settings.llm_base_url,
        "media_providers": media_router.status(),
        "tts": tts_router.status(),
    }


@app.get("/api/media/status")
async def media_status():
    return {"media": media_router.status(), "tts": tts_router.status()}


@app.get("/api/projects")
async def projects():
    return project_store.list_projects()


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    _recover_generated_media(project)
    return project


@app.post("/api/projects/{project_id}/media/recover")
async def recover_project_media(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    recovered = _recover_generated_media(project)
    return {"recovered": recovered, "project": project}


@app.get("/api/projects/{project_id}/media/{filename}")
async def get_project_media(project_id: str, filename: str):
    path = project_store.media_file(project_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(path, headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/api/projects/{project_id}/audio/{filename}")
async def get_project_audio(project_id: str, filename: str):
    path = project_store.audio_file(project_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(path, headers={"Cache-Control": "no-store, max-age=0"})


@app.post("/api/projects")
async def create_project(payload: CreateProjectRequest):
    try:
        storyboard = await llm.create_storyboard(payload)
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"LLM backend request failed: {exc}") from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=f"LLM returned an invalid storyboard: {exc}") from exc
    project = Project(id=uuid4().hex[:12], request=payload, storyboard=storyboard)
    project_store.save(project)
    return project


@app.post("/api/projects/{project_id}/character-reference/generate")
async def generate_character_reference(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    characters = "; ".join(project.storyboard.characters).strip() or "the main recurring character described by the project story"
    prompt = (
        "Create a clean character reference image for a recurring animated character.\n"
        f"Character description: {characters}\n"
        f"Project visual style: {project.storyboard.visual_style}\n"
        "Show the main character clearly, full body, neutral standing pose, simple uncluttered background, "
        "readable silhouette, consistent proportions, colors, face, clothing and distinctive features. "
        "Do not include captions, labels, text, watermark, extra characters or a grid."
    )
    provider = "openai" if media_router.openai_image.enabled else "local_fast"
    try:
        asset = await media_router.generate_image(
            prompt=prompt,
            aspect_ratio="1:1",
            project_id=project.id,
            scene_id="character-reference",
            media_dir=project_store.media_dir(project.id),
            provider=provider,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Character reference generation failed: {_error_detail(exc)}") from exc
    project.character_reference = asset
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
        speech_changed = scene.narration != payload.narration or scene.dialogue != payload.dialogue
        project.storyboard.scenes[index] = scene.model_copy(
            update={
                "title": payload.title,
                "duration_seconds": payload.duration_seconds,
                "narration": payload.narration,
                "dialogue": payload.dialogue,
                "action": payload.action,
                "visual_prompt": payload.visual_prompt,
                "media_search_query": payload.media_search_query,
                "selected_audio": None if speech_changed else scene.selected_audio,
            }
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
            regenerated = await llm.regenerate_scene(project.request, project.storyboard, scene)
        except HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"LLM backend request failed: {exc}") from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=502, detail=f"LLM returned an invalid scene: {exc}") from exc
        regenerated.selected_media = scene.selected_media
        regenerated.media_candidates = scene.media_candidates
        regenerated.selected_audio = None
        project.storyboard.scenes[index] = regenerated
        project_store.save(project)
        return project
    raise HTTPException(status_code=404, detail="Scene not found")


@app.get("/api/projects/{project_id}/scenes/{scene_id}/media/search")
async def search_scene_media(project_id: str, scene_id: str, query: str | None = Query(default=None, max_length=1000)):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    scene = next((item for item in project.storyboard.scenes if item.id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    search_query = (query or scene.media_search_query or scene.visual_prompt).strip()
    if not search_query:
        raise HTTPException(status_code=400, detail="Media search query is empty")
    if not media_router.search_enabled():
        raise HTTPException(status_code=503, detail="No stock media providers enabled")
    assets = await media_router.search(search_query, limit_per_provider=4)
    return {"query": search_query, "providers": media_router.status(), "results": assets}


@app.post("/api/projects/{project_id}/scenes/{scene_id}/media/generate-ai")
async def generate_scene_ai_media(
    project_id: str,
    scene_id: str,
    provider: str = Query(default="auto", pattern="^(auto|local|local_fast|local_quality|local_next|openai)$"),
    use_reference: bool | None = Query(default=None),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    scene = next((item for item in project.storyboard.scenes if item.id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    if use_reference is None:
        use_reference = provider == "openai"
    reference_path = _character_reference_path(project) if use_reference else None
    characters = "; ".join(project.storyboard.characters)
    prompt_parts = [scene.visual_prompt.strip(), f"Visual style: {project.storyboard.visual_style.strip()}"]
    if characters:
        prompt_parts.append(f"Characters: {characters}")
    if reference_path is not None:
        prompt_parts.append(
            "The attached image is the canonical character reference. Preserve identity, face, body proportions, "
            "colors, clothing and distinctive features, while following the scene composition, environment, action, "
            "camera and objects described above."
        )
    else:
        prompt_parts.append("Prioritize the described scene composition, environment, action, camera and objects.")
    prompt_parts.append("No captions, no text, no watermark.")
    prompt = "\n".join(part for part in prompt_parts if part)
    try:
        asset = await media_router.generate_image(
            prompt=prompt,
            aspect_ratio=project.request.aspect_ratio,
            project_id=project.id,
            scene_id=scene.id,
            media_dir=project_store.media_dir(project.id),
            reference_path=reference_path,
            provider=provider,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image generation failed: {_error_detail(exc)}") from exc
    for index, item in enumerate(project.storyboard.scenes):
        if item.id == scene_id:
            project.storyboard.scenes[index] = _append_candidate(item, asset)
            project_store.save(project)
            return project
    raise HTTPException(status_code=404, detail="Scene not found")


@app.post("/api/projects/{project_id}/scenes/{scene_id}/media/select")
async def select_scene_media(project_id: str, scene_id: str, payload: MediaAsset):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        candidates = list(scene.media_candidates)
        if not any(item.asset_id == payload.asset_id for item in candidates):
            candidates.append(payload)
        project.storyboard.scenes[index] = scene.model_copy(update={"selected_media": payload, "media_candidates": candidates})
        project_store.save(project)
        return project
    raise HTTPException(status_code=404, detail="Scene not found")


@app.post("/api/projects/{project_id}/scenes/{scene_id}/media/select-candidate/{asset_id}")
async def select_scene_media_candidate(project_id: str, scene_id: str, asset_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        asset = next((item for item in scene.media_candidates if item.asset_id == asset_id), None)
        if asset is None:
            raise HTTPException(status_code=404, detail="Media candidate not found")
        project.storyboard.scenes[index] = scene.model_copy(update={"selected_media": asset})
        project_store.save(project)
        return project
    raise HTTPException(status_code=404, detail="Scene not found")


@app.delete("/api/projects/{project_id}/scenes/{scene_id}/media/candidates/{asset_id}")
async def delete_scene_media_candidate(project_id: str, scene_id: str, asset_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        if scene.selected_media is not None and scene.selected_media.asset_id == asset_id:
            raise HTTPException(status_code=409, detail="Cannot remove the currently selected media. Select another candidate first.")
        candidates = [item for item in scene.media_candidates if item.asset_id != asset_id]
        if len(candidates) == len(scene.media_candidates):
            raise HTTPException(status_code=404, detail="Media candidate not found")
        project.storyboard.scenes[index] = scene.model_copy(update={"media_candidates": candidates})
        project_store.save(project)
        return project
    raise HTTPException(status_code=404, detail="Scene not found")


@app.post("/api/projects/{project_id}/scenes/{scene_id}/audio/generate")
async def generate_scene_audio(project_id: str, scene_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    scene = next((item for item in project.storyboard.scenes if item.id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    speech_text = _scene_speech_text(scene)
    if not speech_text:
        raise HTTPException(status_code=400, detail="Scene has no narration or dialogue to synthesize")
    try:
        asset = await tts_router.generate(
            text=speech_text,
            project_id=project.id,
            scene_id=scene.id,
            audio_dir=project_store.audio_dir(project.id),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS generation failed: {_error_detail(exc)}") from exc
    for index, item in enumerate(project.storyboard.scenes):
        if item.id == scene_id:
            project.storyboard.scenes[index] = item.model_copy(update={"selected_audio": asset})
            project_store.save(project)
            return project
    raise HTTPException(status_code=404, detail="Scene not found")
