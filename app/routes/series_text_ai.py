from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.main import llm
from app.schemas import Project
from app.services.model_orchestrator import model_orchestrator
from app.storage import project_store


router = APIRouter(prefix="/api/series", tags=["series-text-ai"])


SYSTEM_PROMPT = """You extract a compact reusable visual canon for an AI animated series.
Do NOT return JSON. Do NOT use markdown. Do NOT expose chain-of-thought.

Return ONLY reference blocks in this exact line format:
REF: key | kind | name | RU text | EN text
BLOCK: key | name | comma-separated @keys

Rules:
- key must be lowercase ASCII and start with char_, prop_, loc_, style_ or visual_.
- kind must be one of: character, object, location, style.
- Create references only for recurring facts supported by the supplied project. Do not invent permanent traits, clothing, props, magic, names or lore.
- Prefer one canonical character key per recurring character and one key per important recurring prop/location/style.
- Keep RU and EN descriptions concrete and visually useful, not literary.
- BLOCK entries are convenience aliases that combine existing keys, for example @char_tim, @prop_boat, @style_cartoon.
- Do not create duplicate aliases for the same entity.
- Usually return 3-10 REF lines and 0-4 BLOCK lines.
"""


def _series_root(project: Project) -> Project:
    root_id = project.series_id or (project.id if project.request.project_type == "series" else None)
    if not root_id:
        raise HTTPException(status_code=400, detail="Project is not part of a series")
    root = project_store.load(root_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Series root project not found")
    return root


def _clean_key(value: str) -> str:
    value = value.strip().lstrip("@").lower().replace("-", "_")
    value = re.sub(r"[^a-z0-9_]", "", value)
    return value[:64]


def _parse(content: str, existing: set[str]) -> list[dict]:
    result: list[dict] = []
    seen = {key.lower() for key in existing}
    valid_kinds = {"character", "object", "location", "style"}
    for raw in content.splitlines():
        line = raw.strip().strip("` ")
        if not line:
            continue
        if line.upper().startswith("REF:"):
            parts = [part.strip() for part in line.split(":", 1)[1].split("|")]
            if len(parts) < 5:
                continue
            key = _clean_key(parts[0])
            kind = parts[1].lower()
            if not key or key in seen or kind not in valid_kinds:
                continue
            if not key.startswith(("char_", "prop_", "loc_", "style_")):
                continue
            item = {
                "key": key,
                "name": parts[2][:200] or key,
                "kind": kind,
                "text_ru": parts[3][:6000],
                "text_en": " | ".join(parts[4:])[:6000],
                "is_block": False,
                "from_episode": 1,
                "to_episode": None,
            }
            if item["text_ru"] or item["text_en"]:
                result.append(item)
                seen.add(key)
        elif line.upper().startswith("BLOCK:"):
            parts = [part.strip() for part in line.split(":", 1)[1].split("|")]
            if len(parts) < 3:
                continue
            key = _clean_key(parts[0])
            if not key or key in seen or not key.startswith("visual_"):
                continue
            refs = re.findall(r"@[A-Za-z][A-Za-z0-9_-]{1,63}", " | ".join(parts[2:]))
            if not refs:
                continue
            text = ", ".join(dict.fromkeys(ref.lower() for ref in refs))
            result.append({
                "key": key,
                "name": parts[1][:200] or key,
                "kind": "style",
                "text_ru": text,
                "text_en": text,
                "is_block": True,
                "from_episode": 1,
                "to_episode": None,
            })
            seen.add(key)
    return result


@router.post("/{project_id}/text-references/generate")
async def generate_text_reference_suggestions(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _series_root(project)

    scene_facts = "\n".join(
        f"- {scene.title}: {scene.action}"
        for scene in root.storyboard.scenes[:24]
    )
    existing = {ref.key for ref in root.series_text_references}
    prompt = (
        f"Series title: {root.series_title or root.storyboard.title}\n"
        f"Original project idea: {root.request.prompt}\n"
        f"Logline: {root.storyboard.logline}\n"
        f"Visual style: {root.storyboard.visual_style}\n"
        f"Canonical characters: {'; '.join(root.storyboard.characters)}\n"
        f"Existing keys that must not be duplicated: {', '.join('@' + key for key in sorted(existing)) or 'NONE'}\n\n"
        f"Scene facts:\n{scene_facts}\n\n"
        "Extract reusable canonical visual references and a few useful composite visual blocks."
    )

    profile = settings.llm_profile_storyboard or "quality"
    try:
        with model_orchestrator.use_llm_profile(profile):
            content = await llm._chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1400,
                temperature=0.25,
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Text reference generation failed: {exc}") from exc

    suggestions = _parse(content, existing)
    if not suggestions:
        raise HTTPException(status_code=502, detail="LLM did not return usable text references")
    return {
        "series_id": root.id,
        "profile": model_orchestrator.normalize_profile(profile),
        "suggestions": suggestions,
    }
