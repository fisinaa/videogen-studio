import asyncio
import os
import sys
import base64
import io
import json
import re

import httpx
from PIL import Image
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, File, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from httpx import HTTPError

from app.config import settings
from app.providers.llm.llama_cpp import LlamaCppProvider
from app.providers.media.router import media_router
from app.providers.tts.router import tts_router
from app.schemas import CreateProjectRequest, MediaAsset, Project, SceneUpdate
from app.services.model_orchestrator import model_orchestrator
from app.storage import project_store


app = FastAPI(title="VideoGen Studio", version="0.12.0")
templates = Jinja2Templates(directory="app/templates")
llm = LlamaCppProvider()


def _image_dimensions(aspect_ratio: str) -> tuple[int, int]:
    return {"16:9": (1536, 1024), "9:16": (1024, 1536), "1:1": (1024, 1024)}.get(
        aspect_ratio, (1536, 1024)
    )


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


def _camera_animation(scene, index: int, count: int) -> str:
    chosen = scene.camera_movement or "auto"
    if chosen != "auto":
        return chosen
    content = f"{scene.title} {scene.action} {scene.narration}".casefold()
    if index == count - 1 or any(word in content for word in ("закат", "финал", "покой", "заводь", "horizon", "sunset", "arrival")):
        return "zoom-out"
    if any(word in content for word in ("мч", "ускор", "течени", "прорыв", "порог", "круж", "скорост", "брызг", "rapid", "speed", "rush", "current")):
        return "pan-right" if index % 2 else "pan-left"
    if any(word in content for word in ("ветер", "порыв", "вылет", "gust", "wind", "storm")):
        return "drift-up"
    if any(word in content for word in ("спокой", "тихо", "отдых", "reflection", "calm", "rest")):
        return "drift-down"
    return ("zoom-in", "pan-left", "drift-up", "pan-right")[index % 4]


TTS_VOICES = {"marin", "cedar", "coral", "nova", "shimmer", "fable", "onyx", "verse", "ballad", "sage", "echo", "alloy", "ash"}


def _narration_direction(scene) -> str:
    explicit = (scene.tts_direction or "").strip()
    if explicit:
        mood = explicit
    else:
        content = f"{scene.title} {scene.action} {scene.narration}".casefold()
        if any(w in content for w in ("порог", "страх", "опасн", "камн", "бурн", "rapid", "danger", "boulder")):
            mood = "Tension and urgency, hushed suspense at the start, a quickening rhythm around danger."
        elif any(w in content for w in ("ветер", "порыв", "внезапн", "wind", "gust")):
            mood = "A sudden note of surprise, a small gasp, then lively adventurous energy."
        elif any(w in content for w in ("закат", "заводь", "успоко", "sunset", "calm", "arrival")):
            mood = "Relief, tenderness and wonder, slow down gently, finish with a thoughtful pause."
        else:
            mood = "Curiosity and gentle wonder, relaxed storytelling pace and natural small pauses."
    return (
        "You are a gifted Russian-language children's audiobook narrator, not a news presenter. "
        "Read the supplied Russian text exactly, without adding stage directions or sound effects. "
        "Sound genuinely human and emotionally involved. Vary pitch, tempo and emphasis naturally; "
        "use meaningful pauses at punctuation. No robotic sing-song, no shouting or theatrical caricature. "
        "Keep the same narrator identity and tone across all scenes. "
        "Scene-specific acting direction: " + mood
    )


def _scene_speech_text(scene) -> str:
    narration = scene.narration.strip()
    if narration:
        return narration
    return "\n".join(line.strip() for line in scene.dialogue if line.strip()).strip()


def _visual_bible(storyboard) -> str:
    characters = "\n".join(f"- {item}" for item in storyboard.characters) or "- No canonical character description supplied."
    return (
        f"PROJECT VISUAL STYLE:\n{storyboard.visual_style}\n\n"
        f"CANONICAL CHARACTERS:\n{characters}\n\n"
        "CONTINUITY RULES:\n"
        "- Keep species, body proportions, fur/skin colors, eye colors, clothing and distinctive features stable.\n"
        "- Repeat visible canonical traits in every image-generation prompt.\n"
        "- Do not invent extra characters, clothing, props or text unless the scene requires them.\n"
        "- Keep recurring locations and important props visually consistent between scenes."
    )


def _ensure_visual_bible(project: Project) -> bool:
    if project.storyboard.visual_bible.strip():
        return False
    project.storyboard.visual_bible = _visual_bible(project.storyboard)
    return True


def _image_prompt(project: Project, scene, use_reference: bool) -> tuple[str, Path | None]:
    reference_path = _character_reference_path(project) if use_reference else None
    base_prompt = (scene.key_frame_prompt_en or scene.visual_prompt_en or scene.visual_prompt).strip()
    if not base_prompt:
        raise ValueError("Scene visual prompt is empty")

    prompt_parts = [base_prompt]
    if project.storyboard.visual_bible.strip():
        prompt_parts.append("Continuity context:\n" + project.storyboard.visual_bible.strip())
    if reference_path is not None:
        prompt_parts.append(
            "The attached image is the canonical character reference. Preserve identity, face, body proportions, "
            "colors, clothing and distinctive features while following the requested composition and action."
        )
    if scene.negative_prompt_en.strip():
        prompt_parts.append(f"Avoid: {scene.negative_prompt_en.strip()}")
    prompt_parts.append("No captions, no text, no watermark.")
    return "\n".join(part for part in prompt_parts if part), reference_path


async def _generate_scene_image(project: Project, scene, provider: str, use_reference: bool):
    try:
        prompt, reference_path = _image_prompt(project, scene, use_reference)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await media_router.generate_image(
        prompt=prompt,
        aspect_ratio=project.request.aspect_ratio,
        project_id=project.id,
        scene_id=scene.id,
        media_dir=project_store.media_dir(project.id),
        reference_path=reference_path,
        provider=provider,
    )


def _local_profile_for_provider(provider: str) -> str | None:
    return {
        "local": "fast",
        "local_fast": "fast",
        "local_quality": "quality",
        "local_next": "next",
    }.get(provider)


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
        "orchestrator": await model_orchestrator.status(),
    }


@app.get("/api/media/status")
async def media_status():
    return {
        "media": media_router.status(),
        "tts": tts_router.status(),
        "orchestrator": await model_orchestrator.status(),
    }


@app.get("/api/projects")
async def projects():
    return project_store.list_projects()


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    changed = _ensure_visual_bible(project)
    _recover_generated_media(project)
    if changed:
        project_store.save(project)
    return project


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        deleted = project_store.delete(project_id)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Project deletion failed: {exc}") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"deleted": True, "project_id": project_id, "title": project.storyboard.title}


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
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(path, headers={"Cache-Control": "no-store, max-age=0"})


@app.post("/api/projects")
async def create_project(payload: CreateProjectRequest):
    try:
        with model_orchestrator.use_llm_profile(payload.llm_profile or settings.llm_profile_storyboard):
            storyboard = await llm.create_storyboard(payload)
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"LLM backend request failed: {exc}") from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=f"LLM returned an invalid storyboard: {exc}") from exc
    storyboard.visual_bible = _visual_bible(storyboard)
    project = Project(id=uuid4().hex[:12], request=payload, storyboard=storyboard)
    project_store.save(project)
    return project


@app.post("/api/projects/{project_id}/character-reference/generate")
async def generate_character_reference(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    _ensure_visual_bible(project)
    characters = "; ".join(project.storyboard.characters).strip() or "the main recurring character described by the project story"
    prompt = "\n".join(
        [
            "Create a clean character reference image for a recurring animated character.",
            f"Character description: {characters}",
            f"Project visual style: {project.storyboard.visual_style}",
            "Show the main character clearly, full body, neutral standing pose, simple uncluttered background, readable silhouette, consistent proportions, colors, face, clothing and distinctive features. Do not include captions, labels, text, watermark, extra characters or a grid.",
        ]
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
                "key_frame_prompt_en": scene.key_frame_prompt_en if payload.key_frame_prompt_en is None else payload.key_frame_prompt_en,
                "key_frame_prompt_ru": scene.key_frame_prompt_ru if payload.key_frame_prompt_ru is None else payload.key_frame_prompt_ru,
                "motion_prompt_en": scene.motion_prompt_en if payload.motion_prompt_en is None else payload.motion_prompt_en,
                "visual_prompt_ru": scene.visual_prompt_ru if payload.visual_prompt_ru is None else payload.visual_prompt_ru,
                "visual_prompt_en": scene.visual_prompt_en if payload.visual_prompt_en is None else payload.visual_prompt_en,
                "negative_prompt_en": scene.negative_prompt_en if payload.negative_prompt_en is None else payload.negative_prompt_en,
                "media_search_query": payload.media_search_query,
                "camera_movement": scene.camera_movement if payload.camera_movement is None else payload.camera_movement,
                "tts_voice": scene.tts_voice if payload.tts_voice is None else payload.tts_voice,
                "tts_direction": scene.tts_direction if payload.tts_direction is None else payload.tts_direction,
                "selected_audio": None if speech_changed else scene.selected_audio,
            }
        )
        project_store.save(project)
        return project
    raise HTTPException(status_code=404, detail="Scene not found")


@app.post("/api/projects/{project_id}/scenes/{scene_id}/visual-prompt/rebuild")
async def rebuild_scene_visual_prompt(project_id: str, scene_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    _ensure_visual_bible(project)
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id != scene_id:
            continue
        try:
            rebuilt = await llm.rebuild_visual_prompt(project.storyboard, scene)
        except HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"LLM backend request failed: {exc}") from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=502, detail=f"Prompt builder failed: {exc}") from exc
        project.storyboard.scenes[index] = rebuilt
        project_store.save(project)
        return project
    raise HTTPException(status_code=404, detail="Scene not found")


@app.post("/api/projects/{project_id}/visual-prompts/rebuild-all")
async def rebuild_all_visual_prompts(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    _ensure_visual_bible(project)
    ok = 0
    errors: list[dict] = []
    for index, scene in enumerate(project.storyboard.scenes):
        try:
            project.storyboard.scenes[index] = await llm.rebuild_visual_prompt(project.storyboard, scene)
            project_store.save(project)
            ok += 1
        except Exception as exc:
            errors.append({"scene_id": scene.id, "error": _error_detail(exc)})
    return {"project": project, "rebuilt": ok, "errors": errors}


@app.post("/api/projects/{project_id}/scenes/{scene_id}/regenerate")
async def regenerate_scene(project_id: str, scene_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    _ensure_visual_bible(project)
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
    search_query = (query or scene.media_search_query or scene.visual_prompt_en or scene.visual_prompt).strip()
    if not search_query:
        raise HTTPException(status_code=400, detail="Media search query is empty")
    if not media_router.search_enabled():
        raise HTTPException(status_code=503, detail="No stock media providers enabled")
    assets = await media_router.search(search_query, limit_per_provider=4)
    return {"query": search_query, "providers": media_router.status(), "results": assets}


@app.post("/api/projects/{project_id}/scenes/{scene_id}/keyframe/generate")
async def generate_scene_keyframe(project_id: str, scene_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    for scene in project.storyboard.scenes:
        if scene.id != scene_id:
            continue
        try:
            with model_orchestrator.use_llm_profile(settings.llm_profile_visual_prompt):
                prompts = await llm.build_key_frame(project.storyboard, scene)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Keyframe prompt failed: {_error_detail(exc)}") from exc
        for key, value in prompts.items():
            setattr(scene, key, value)
        project_store.save(project)
        return project
    raise HTTPException(status_code=404, detail="Scene not found")


@app.post("/api/projects/{project_id}/keyframes/generate-all")
async def generate_all_keyframes(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    generated = 0
    errors = []
    with model_orchestrator.use_llm_profile(settings.llm_profile_visual_prompt):
        for scene in project.storyboard.scenes:
            try:
                prompts = await llm.build_key_frame(project.storyboard, scene)
                for key, value in prompts.items():
                    setattr(scene, key, value)
                generated += 1
                project_store.save(project)
            except Exception as exc:
                errors.append({"scene_id": scene.id, "error": _error_detail(exc)})
    return {"project": project, "generated": generated, "errors": errors}


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
    _ensure_visual_bible(project)
    scene = next((item for item in project.storyboard.scenes if item.id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    if use_reference is None:
        use_reference = provider == "openai"
    try:
        asset = await _generate_scene_image(project, scene, provider, use_reference)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image generation failed: {_error_detail(exc)}") from exc
    for index, item in enumerate(project.storyboard.scenes):
        if item.id == scene_id:
            project.storyboard.scenes[index] = _append_candidate(item, asset)
            project_store.save(project)
            return project
    raise HTTPException(status_code=404, detail="Scene not found")


@app.post("/api/projects/{project_id}/media/generate-batch")
async def generate_project_images_batch(
    project_id: str,
    provider: str = Query(default="local_next", pattern="^(local|local_fast|local_quality|local_next|openai)$"),
    use_reference: bool = Query(default=False),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    _ensure_visual_bible(project)

    ok = 0
    errors: list[dict] = []
    profile = _local_profile_for_provider(provider)

    async def run_all() -> None:
        nonlocal ok
        for index, scene in enumerate(project.storyboard.scenes):
            try:
                ref = use_reference or (provider == "openai" and project.character_reference is not None)
                asset = await _generate_scene_image(project, scene, provider, ref)
                project.storyboard.scenes[index] = _append_candidate(project.storyboard.scenes[index], asset)
                project_store.save(project)
                ok += 1
            except Exception as exc:
                errors.append({"scene_id": scene.id, "error": _error_detail(exc)})

    if profile is not None:
        async with model_orchestrator.local_image_batch(profile):
            await run_all()
    else:
        await run_all()

    return {"project": project, "generated": ok, "errors": errors}


async def _review_image(project: Project, scene) -> dict:
    asset = scene.selected_media
    if asset is None or not asset.local_path:
        raise ValueError("У сцены нет выбранного локального изображения")
    path = project_store.media_file(project.id, Path(asset.local_path).name)
    if path is None:
        raise ValueError("Файл выбранного изображения не найден")
    if not settings.llm_quality_mmproj.is_file():
        raise ValueError("LLM_QUALITY_MMPROJ не настроен или файл mmproj отсутствует")
    with Image.open(path) as original:
        image = original.convert("RGB")
        image.thumbnail((1024, 1024))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=83)
    image_data = base64.b64encode(output.getvalue()).decode("ascii")
    instructions = (
        "Inspect the actual image, not just the text. You are a strict but fair "
        "quality reviewer for an animated film. Compare the image to the requested "
        "scene, visible action, and canonical protagonist appearance. "
        "Do not claim to see things that are not visible. "
        "Return ONLY a JSON object: "
        '{"score": integer 0-10, "verdict": "pass" or "review", '
        '"character": "short Russian explanation", '
        '"action": "short Russian explanation", '
        '"issues": ["short Russian issue"], '
        '"recommendation": "short Russian improvement"}. '
        "When no reference image is supplied, do not confidently claim character "
        "identity consistency: compare against the written canonical description."
    )
    context = (
        f"SCENE ID: {scene.id}\nTITLE: {scene.title}\n"
        f"EXPECTED ACTION: {scene.action}\n"
        f"EXPECTED VISUAL: {scene.visual_prompt_ru or scene.visual_prompt_en or scene.visual_prompt}\n"
        f"CANONICAL CHARACTER: {'; '.join(project.storyboard.characters)}\n"
        f"STYLE: {project.storyboard.visual_style}"
    )
    payload = {
        "model": settings.llm_model,
        "temperature": 0.2,
        "max_tokens": 600,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": [
                {"type": "text", "text": context},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_data}},
            ]},
        ],
    }
    if settings.llm_disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    async with model_orchestrator.llm_slot(profile="quality"):
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout_seconds)) as client:
            response = await client.post(
                settings.llm_base_url.rstrip("/") + "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        raise ValueError("Модель не вернула JSON-оценку изображения")
    try:
        review = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError("Невалидный JSON от модели проверки изображения") from exc
    score = review.get("score")
    if not isinstance(score, int) or not 0 <= score <= 10:
        raise ValueError("Некорректная оценка AI, ожидалось целое число 0-10")
    review["verdict"] = "pass" if score >= 8 and not review.get("issues") else "review"
    review["asset_id"] = asset.asset_id
    return review


@app.post("/api/projects/{project_id}/scenes/{scene_id}/image-review")
async def review_scene_image(project_id: str, scene_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.id == scene_id:
            try:
                review = await _review_image(project, scene)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=_error_detail(exc)) from exc
            project.storyboard.scenes[index].image_review = review
            project_store.save(project)
            return {"review": review, "project": project}
    raise HTTPException(status_code=404, detail="Scene not found")


@app.post("/api/projects/{project_id}/image-reviews/run-all")
async def review_all_images(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    checked = 0
    skipped = []
    errors = []
    for scene in project.storyboard.scenes:
        if not scene.selected_media:
            skipped.append(scene.id)
            continue
        try:
            scene.image_review = await _review_image(project, scene)
            checked += 1
            project_store.save(project)
        except Exception as exc:
            errors.append({"scene_id": scene.id, "error": _error_detail(exc)})
    return {"project": project, "checked": checked, "skipped": skipped, "errors": errors}


async def _run_montage(runner: Path, action: str, job: dict | None = None) -> dict:
    if not runner.is_file():
        raise ValueError(f"OpenMontage runner not found: {runner}")
    python_bin = settings.openmontage_python
    if not python_bin.is_file():
        raise ValueError(f"OpenMontage Python is missing: {python_bin}")
    env = os.environ.copy()
    env["OPENMONTAGE_ROOT"] = str(settings.openmontage_root)
    # pydantic-settings reads VideoGen's .env but does not export it into the
    # process environment. OpenMontage runs as a subprocess and needs credentials
    # in its own environment to discover available video providers.
    if settings.fal_key and not env.get("FAL_KEY"):
        env["FAL_KEY"] = settings.fal_key
    if settings.openai_api_key and not env.get("OPENAI_API_KEY"):
        env["OPENAI_API_KEY"] = settings.openai_api_key
    cmd = [str(python_bin), str(runner), action]
    if job is not None:
        cmd.append(json.dumps(job, ensure_ascii=False))
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=settings.openmontage_timeout_seconds
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise ValueError("OpenMontage operation timed out") from exc
    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip()
    try:
        result = json.loads(output.splitlines()[-1]) if output else {}
    except (ValueError, IndexError):
        result = {}
    if process.returncode != 0 or result.get("success") is False:
        raise ValueError(str(result.get("error") or error[-1200:] or output[-1200:] or "OpenMontage failed"))
    if not result and action != "status":
        raise ValueError(f"OpenMontage returned no result: {error[-700:]}")
    return result


@app.get("/api/openmontage/status")
async def openmontage_status():
    results = {}
    for name, runner in (("motion", settings.motion_openmontage_runner),
                         ("render", settings.videogen_openmontage_runner)):
        try:
            results[name] = await _run_montage(runner, "status")
        except Exception as exc:
            results[name] = {"success": False, "error": _error_detail(exc)}
    return results


@app.post("/api/projects/{project_id}/scenes/{scene_id}/motion/generate")
async def generate_scene_motion(
    project_id: str, scene_id: str,
    provider: str = Query(default="auto", max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    scene = next((scene for scene in project.storyboard.scenes if scene.id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    if not scene.selected_media or not scene.selected_media.local_path:
        raise HTTPException(status_code=400, detail="Select a locally saved scene image first")
    reference_path = project_store.media_file(project_id, Path(scene.selected_media.local_path).name)
    if reference_path is None:
        raise HTTPException(status_code=404, detail="Selected image file is missing")
    output = project_store.media_dir(project_id) / f"{scene.id}-motion-{uuid4().hex[:10]}.mp4"
    prompt = scene.motion_prompt_en or (
        f"Animate this still image naturally: {scene.action}. "
        "Keep character appearance, clothing, setting and camera continuity; no morphing."
    )
    job = {
        "reference_image_path": str(reference_path.resolve()),
        "output_path": str(output.resolve()),
        "duration": min(float(scene.duration_seconds), float(settings.motion_max_duration_seconds)),
        "preferred_provider": provider,
        "aspect_ratio": project.request.aspect_ratio,
        "prompt": prompt,
    }
    try:
        if provider == "local_camera":
            # Local fallback: camera motion over the still frame, NOT generated
            # character animation. Requires ffmpeg but no cloud video provider.
            duration = max(1.0, float(job["duration"]))
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-loop", "1", "-framerate", "24",
                "-i", str(reference_path),
                "-vf", ("scale=960:540:force_original_aspect_ratio=increase,"
                        "crop=960:540,"
                        "zoompan=z='min(zoom+0.00035,1.08)':d=1:"
                        "x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':"
                        "s=960x540:fps=24,format=yuv420p"),
                "-t", str(duration),
                "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "23", "-movflags", "+faststart", str(output),
            ]
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=settings.motion_timeout_seconds
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                raise ValueError("Local camera motion timed out")
            if process.returncode != 0:
                raise ValueError(
                    "Local camera FFmpeg error: "
                    + stderr.decode("utf-8", errors="replace")[-1000:]
                )
            result = {"success": True, "selected_provider": "local_camera",
                      "note": "Camera zoom only; character remains a still image"}
        else:
            async with model_orchestrator.local_motion_slot():
                result = await _run_montage(settings.motion_openmontage_runner, "generate", job)
        if not output.is_file() or output.stat().st_size == 0:
            raise ValueError("Video provider returned success without an output MP4")
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=f"OpenMontage motion failed: {_error_detail(exc)}") from exc
    relative_url = f"/api/projects/{project_id}/media/{output.name}"
    clip = MediaAsset(
        provider=("local_camera" if provider == "local_camera"
                  else f"openmontage:{result.get('selected_provider') or provider}"),
        asset_id=output.name, media_type="video",
        preview_url=relative_url, source_url=relative_url, download_url=relative_url,
        local_path=f"media/{output.name}",
        duration_seconds=job["duration"],
        label=(f"Camera movement {scene.id} (FFmpeg)" if provider == "local_camera"
               else f"Animated {scene.id} via OpenMontage"),
    )
    scene.motion_candidates.append(clip)
    scene.selected_motion_media = clip
    scene.motion_mode = "image_to_video"
    project_store.save(project)
    return {"project": project, "result": result}


@app.get("/api/projects/{project_id}/renders/{filename}")
async def get_project_render(project_id: str, filename: str):
    path = project_store.render_file(project_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Rendered video not found")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/projects/{project_id}/audio/status")
async def project_audio_status(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    ready, missing = [], []
    for scene in project.storyboard.scenes:
        asset = scene.selected_audio
        filename = Path(asset.local_path).name if asset and asset.local_path else ""
        file = project_store.audio_file(project_id, filename) if filename else None
        if file is not None and file.stat().st_size > 0:
            ready.append(scene.id)
        elif _scene_speech_text(scene):
            missing.append(scene.id)
    return {"ready": ready, "missing": missing,
            "music_file": project.music_file, "ambience_file": project.ambience_file}


@app.post("/api/projects/{project_id}/soundtrack/{kind}")
async def upload_soundtrack(project_id: str, kind: str, file: UploadFile = File(...)):
    if kind not in {"music", "ambience"}:
        raise HTTPException(status_code=400, detail="Use music or ambience")
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    extension = Path(file.filename or "").suffix.lower()
    if extension not in {".mp3", ".wav", ".flac", ".ogg", ".m4a"}:
        raise HTTPException(status_code=400, detail="Supported audio: mp3, wav, flac, ogg, m4a")
    filename = f"{kind}-{uuid4().hex[:10]}{extension}"
    dest = project_store.audio_dir(project_id) / filename
    total = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise ValueError("Audio file exceeds 100 MB")
                out.write(chunk)
        if not total:
            raise ValueError("Empty audio file")
        from app.services.render_finish import media_streams
        if "audio" not in await media_streams(dest):
            raise ValueError("Uploaded file contains no audio stream")
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid audio: {_error_detail(exc)}") from exc
    finally:
        await file.close()
    if kind == "music":
        project.music_file = filename
    else:
        project.ambience_file = filename
    project_store.save(project)
    return {"project": project, "filename": filename,
            "url": f"/api/projects/{project_id}/audio/{filename}"}


@app.delete("/api/projects/{project_id}/soundtrack/{kind}")
async def clear_soundtrack(project_id: str, kind: str):
    if kind not in {"music", "ambience"}:
        raise HTTPException(status_code=400, detail="Use music or ambience")
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if kind == "music":
        project.music_file = ""
    else:
        project.ambience_file = ""
    project_store.save(project)
    return {"project": project}


@app.post("/api/projects/{project_id}/openmontage/render")
async def render_project_openmontage(
    project_id: str,
    runtime: str = Query(default="hyperframes", pattern="^(hyperframes|remotion)$"),
    include_narration: bool = True,
    smooth_transitions: bool = True,
    music_volume: float = Query(default=0.15, ge=0, le=1),
    ambience_volume: float = Query(default=0.18, ge=0, le=1),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    cuts = []
    assets = []
    cursor = 0.0
    narration_tracks = []
    for index, scene in enumerate(project.storyboard.scenes):
        chosen = scene.selected_motion_media or scene.selected_media
        if not chosen or not chosen.local_path:
            raise HTTPException(status_code=400, detail=f"{scene.id} has no chosen media")
        path = project_store.media_file(project_id, Path(chosen.local_path).name)
        if path is None:
            raise HTTPException(status_code=400, detail=f"{scene.id} media file missing")
        length = float(scene.duration_seconds)
        source = str(path.resolve())
        cuts.append({
            "id": scene.id,
            "source": source,
            "in_seconds": cursor,
            "out_seconds": cursor + length,
            "subtitle": scene.subtitle_text or scene.narration if scene.subtitle_enabled else "",
            "animation": (_camera_animation(scene, index, len(project.storyboard.scenes))
                          if chosen.media_type == "image" else "static"),
        })
        assets.append({"id": scene.id, "path": source, "type": chosen.media_type})
        if include_narration and scene.selected_audio:
            name = Path(scene.selected_audio.local_path or "").name
            if name:
                audio_path = project_store.audio_file(project_id, name)
                if audio_path is not None:
                    narration_tracks.append((str(audio_path.resolve()), cursor, length, scene.id))
        cursor += length
    if include_narration:
        expected = [scene.id for scene in project.storyboard.scenes if _scene_speech_text(scene)]
        present = {track[3] for track in narration_tracks}
        missing = [scene_id for scene_id in expected if scene_id not in present]
        if missing:
            raise HTTPException(
                status_code=409,
                detail=("Нет готовой озвучки у сцен: " + ", ".join(missing)
                        + ". Нажми «Озвучить отсутствующие» или отключи «Озвучка в MP4»."),
            )
    music_path = (project_store.audio_file(project_id, project.music_file)
                  if project.music_file else None)
    ambience_path = (project_store.audio_file(project_id, project.ambience_file)
                     if project.ambience_file else None)
    if project.music_file and music_path is None:
        raise HTTPException(status_code=409, detail="Music file is missing; upload again")
    if project.ambience_file and ambience_path is None:
        raise HTTPException(status_code=409, detail="Ambience file is missing; upload again")
    output = project_store.render_dir(project_id) / f"{project_id}-{runtime}-{uuid4().hex[:8]}.mp4"
    job = {
        "runtime": runtime,
        "workspace_path": str(project_store.render_dir(project_id).resolve()),
        "output_path": str(output.resolve()),
        "edit_decisions": {"cuts": cuts},
        "asset_manifest": {"assets": assets},
        "fps": 30,
        "profile": {"width": 1920, "height": 1080, "fps": 30},
        "script_text": project.storyboard.story_arc or project.storyboard.logline,
    }
    try:
        result = await _run_montage(settings.videogen_openmontage_runner, "render", job)
        if not output.is_file() or output.stat().st_size == 0:
            raise ValueError("OpenMontage returned no usable MP4")
        from app.services.render_finish import finish_render
        audio_result = await finish_render(
            output, narration_tracks,
            boundaries=[float(cut["out_seconds"]) for cut in cuts[:-1]],
            fade_seconds=0.34 if smooth_transitions else 0.0,
            music_file=music_path, ambience_file=ambience_path,
            music_volume=music_volume, ambience_volume=ambience_volume,
        )
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise HTTPException(
            status_code=502,
            detail=f"OpenMontage {runtime} render failed: {_error_detail(exc)}",
        ) from exc
    return {
        "success": True,
        "video_url": f"/api/projects/{project_id}/renders/{output.name}",
        "filename": output.name,
        "narration_tracks": len(narration_tracks),
        "audio": audio_result,
        "smooth_transitions": smooth_transitions,
        "camera_movements": {cut["id"]: cut["animation"] for cut in cuts},
        "result": result,
    }


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
        project.storyboard.scenes[index] = scene.model_copy(
            update={"selected_media": payload, "media_candidates": candidates}
        )
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
            raise HTTPException(
                status_code=409,
                detail="Cannot remove the currently selected media. Select another candidate first.",
            )
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
    if scene.tts_voice not in TTS_VOICES:
        raise HTTPException(status_code=400, detail="Unsupported TTS voice")
    if not speech_text:
        raise HTTPException(status_code=400, detail="Scene has no narration or dialogue to synthesize")
    try:
        asset = await tts_router.generate(
            text=speech_text,
            project_id=project.id,
            scene_id=scene.id,
            audio_dir=project_store.audio_dir(project.id),
            voice=scene.tts_voice,
            instructions=_narration_direction(scene),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS generation failed: {_error_detail(exc)}") from exc
    for index, item in enumerate(project.storyboard.scenes):
        if item.id == scene_id:
            project.storyboard.scenes[index] = item.model_copy(update={"selected_audio": asset, "audio_candidates": list(item.audio_candidates) + [asset]})
            project_store.save(project)
            return project
    raise HTTPException(status_code=404, detail="Scene not found")


@app.post("/api/projects/{project_id}/audio/generate-missing")
async def generate_missing_audio(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    ok = 0
    skipped = 0
    errors: list[dict] = []
    for index, scene in enumerate(project.storyboard.scenes):
        if scene.selected_audio is not None:
            skipped += 1
            continue
        text = _scene_speech_text(scene)
        if not text:
            skipped += 1
            continue
        try:
            asset = await tts_router.generate(
                text=text,
                project_id=project.id,
                scene_id=scene.id,
                audio_dir=project_store.audio_dir(project.id),
                voice=scene.tts_voice,
                instructions=_narration_direction(scene),
            )
            project.storyboard.scenes[index] = scene.model_copy(update={"selected_audio": asset, "audio_candidates": list(scene.audio_candidates) + [asset]})
            project_store.save(project)
            ok += 1
        except Exception as exc:
            errors.append({"scene_id": scene.id, "error": _error_detail(exc)})
    return {"project": project, "generated": ok, "skipped": skipped, "errors": errors}
