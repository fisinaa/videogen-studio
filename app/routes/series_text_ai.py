from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.main import llm
from app.schemas import Project
from app.services.model_orchestrator import model_orchestrator
from app.storage import project_store


router = APIRouter(prefix="/api/series", tags=["series-text-ai"])


SYSTEM_PROMPT = """You extract a compact reusable visual canon for an AI animated series from REVIEWED storyboard scenes.
Do NOT return JSON. Do NOT use markdown. Do NOT expose chain-of-thought.

Return ONLY reference blocks in this exact line format:
REF: key | kind | name | RU text | EN text
BLOCK: key | name | comma-separated @keys

Rules:
- key must be lowercase ASCII and start with char_, prop_, loc_, style_ or visual_.
- kind must be one of: character, object, location, style.
- Treat supplied scenes as the approved source of truth. Prefer facts repeated across scenes or clearly canonical in character/style fields.
- Do not create a permanent reference for a disposable one-scene object unless it is clearly important to the continuing story.
- Merge synonyms that describe the same entity into one canonical key.
- Do not invent permanent traits, clothing, props, magic, names or lore.
- Prefer one canonical character key per recurring character and one key per important recurring prop/location/style.
- Keep RU and EN descriptions concrete and visually useful, not literary.
- BLOCK entries are convenience aliases that combine existing keys, for example @char_tim, @prop_boat, @style_cartoon.
- Do not create duplicate aliases for the same entity or duplicate existing keys.
- Usually return 3-10 REF lines and 0-4 BLOCK lines.
"""

ASSIGN_SYSTEM_PROMPT = """Assign approved visual reference keys to storyboard scenes.
Return ONLY one short line per supplied scene. No JSON, markdown, explanation or reasoning.
Copy each supplied SCENE_ID exactly.

Exact format:
SCENE: scene-001 | @char_tim, @loc_river
SCENE: scene-002 | @prop_boat

Rules:
- Use ONLY keys listed in AVAILABLE REFERENCES.
- Assign keys only when visually relevant to that scene.
- Prefer an accurate @visual_ composite block instead of repeating all of its components.
- Do not invent keys.
- If nothing applies, still return the scene line with an empty right side.
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


def _active_for_episode(item, episode_number: int) -> bool:
    if episode_number < item.from_episode:
        return False
    if item.to_episode is not None and episode_number > item.to_episode:
        return False
    return True


def _scene_dump(project: Project, limit: int = 40) -> str:
    blocks: list[str] = []
    for scene in project.storyboard.scenes[:limit]:
        dialogue = " / ".join(scene.dialogue[:6])
        blocks.append(
            "\n".join(
                [
                    f"SCENE_ID: {scene.id}",
                    f"TITLE: {scene.title}",
                    f"ACTION: {scene.action[:1800]}",
                    f"NARRATION: {scene.narration[:1800]}",
                    f"DIALOGUE: {dialogue[:1200]}",
                    f"VISUAL_RU: {scene.visual_prompt_ru[:2200]}",
                    f"VISUAL_EN: {(scene.visual_prompt_en or scene.visual_prompt)[:2200]}",
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)


def _assignment_scene_dump(scenes) -> str:
    """Small assignment-only representation that comfortably fits a 4k context."""
    blocks: list[str] = []
    for scene in scenes:
        blocks.append(
            "\n".join(
                [
                    f"SCENE_ID: {scene.id}",
                    f"TITLE: {scene.title[:180]}",
                    f"ACTION: {scene.action[:420]}",
                    f"VISUAL_RU: {scene.visual_prompt_ru[:560]}",
                    f"VISUAL_EN: {(scene.visual_prompt_en or scene.visual_prompt)[:560]}",
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)


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


def _parse_assignments(content: str, scene_ids: set[str], allowed: set[str]) -> dict[str, list[str]]:
    """Parse Qwen output tolerantly.

    Local models sometimes omit `SCENE:`, change separators, remove the @ sign,
    or put KEYS on the following line. We anchor on the exact known scene ids and
    then scan that scene's output segment for known keys only.
    """
    result: dict[str, list[str]] = {scene_id: [] for scene_id in scene_ids}
    if not content.strip():
        return result

    positions: list[tuple[int, str]] = []
    for scene_id in scene_ids:
        match = re.search(re.escape(scene_id), content, flags=re.IGNORECASE)
        if match:
            positions.append((match.start(), scene_id))
    positions.sort()

    for index, (start, scene_id) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(content)
        segment = content[start:end]
        found: list[str] = []
        for key in sorted(allowed, key=len, reverse=True):
            pattern = rf"(?<![A-Za-z0-9_-])@?{re.escape(key)}(?![A-Za-z0-9_-])"
            if re.search(pattern, segment, flags=re.IGNORECASE) and key not in found:
                found.append(key)
        result[scene_id] = found

    return result


@router.post("/{project_id}/text-references/generate")
async def generate_text_reference_suggestions(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _series_root(project)

    existing = {ref.key for ref in root.series_text_references}
    prompt = (
        f"Series title: {root.series_title or root.storyboard.title}\n"
        f"Current reviewed episode: {project.episode_number or 1} — {project.storyboard.title}\n"
        f"Original project idea: {root.request.prompt}\n"
        f"Logline: {project.storyboard.logline}\n"
        f"Visual style: {project.storyboard.visual_style}\n"
        f"Canonical characters: {'; '.join(project.storyboard.characters)}\n"
        f"Existing keys that must not be duplicated: {', '.join('@' + key for key in sorted(existing)) or 'NONE'}\n\n"
        f"REVIEWED SCENES — source of truth:\n{_scene_dump(project)}\n\n"
        "Extract reusable canonical visual references and a few useful composite visual blocks from these reviewed scenes."
    )

    profile = settings.llm_profile_storyboard or "quality"
    try:
        with model_orchestrator.use_llm_profile(profile):
            content = await llm._chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1800,
                temperature=0.2,
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Text reference generation failed: {exc}") from exc

    suggestions = _parse(content, existing)
    if not suggestions:
        raise HTTPException(status_code=502, detail="LLM did not return usable text references")
    return {
        "series_id": root.id,
        "project_id": project.id,
        "profile": model_orchestrator.normalize_profile(profile),
        "suggestions": suggestions,
    }


@router.post("/{project_id}/text-references/assign-scenes")
async def assign_text_references_to_scenes(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _series_root(project)
    episode_number = int(project.episode_number or 1)
    refs = [ref for ref in root.series_text_references if _active_for_episode(ref, episode_number)]
    if not refs:
        raise HTTPException(status_code=400, detail="No accepted Text References are available for this episode")

    available_lines: list[str] = []
    allowed: set[str] = set()
    for ref in refs:
        allowed.add(ref.key.lower())
        kind = "BLOCK" if ref.is_block else ref.kind.upper()
        available_lines.append(
            f"@{ref.key} [{kind}] {ref.name}: {(ref.text_ru or ref.text_en)[:240]}"
        )
    available_text = "\n".join(available_lines)

    profile = settings.llm_profile_storyboard or "quality"
    assignments: dict[str, list[str]] = {}
    scenes = list(project.storyboard.scenes)
    batch_size = 6
    calls = 0
    previews: list[str] = []

    try:
        with model_orchestrator.use_llm_profile(profile):
            for start in range(0, len(scenes), batch_size):
                batch = scenes[start:start + batch_size]
                scene_ids = {scene.id for scene in batch}
                expected = "\n".join(f"SCENE: {scene.id} |" for scene in batch)
                prompt = (
                    "/no_think\n\n"
                    "AVAILABLE REFERENCES:\n"
                    + available_text
                    + "\n\nSCENES:\n"
                    + _assignment_scene_dump(batch)
                    + "\n\nReturn exactly these scene ids, one line each, adding only applicable keys after |:\n"
                    + expected
                )
                content = await llm._chat(
                    messages=[
                        {"role": "system", "content": ASSIGN_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=520,
                    temperature=0.0,
                )
                parsed = _parse_assignments(content, scene_ids, allowed)
                assignments.update(parsed)
                previews.append(" ".join(content.strip().split())[:500])
                calls += 1
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Scene reference assignment failed in batch {calls + 1}: {exc}",
        ) from exc

    assigned_count = sum(1 for keys in assignments.values() if keys)
    if assigned_count == 0:
        preview = previews[0] if previews else "EMPTY RESPONSE"
        raise HTTPException(
            status_code=502,
            detail=(
                "Qwen completed assignment but no usable keys were parsed. "
                f"First response: {preview or 'EMPTY RESPONSE'}"
            ),
        )

    for index, scene in enumerate(project.storyboard.scenes):
        keys = assignments.get(scene.id, [])
        project.storyboard.scenes[index] = scene.model_copy(update={"reference_keys": keys})
    project_store.save(project)
    return {
        "project": project,
        "profile": model_orchestrator.normalize_profile(profile),
        "assigned_scenes": assigned_count,
        "total_scenes": len(project.storyboard.scenes),
        "calls": calls,
        "batch_size": batch_size,
    }
