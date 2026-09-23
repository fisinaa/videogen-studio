from __future__ import annotations

import math
import re

from app.providers.llm import llama_cpp as llama_mod


REBUILD_SCENE_SYSTEM_PROMPT = """You rebuild ONE storyboard scene from story context.
Do NOT polish or paraphrase a broken scene. Reconstruct it so it logically connects the previous and next scenes.
Do NOT return JSON or markdown. Do NOT expose chain-of-thought.

Return exactly one block using the supplied scene id:
[scene-001]
TITLE: short corrected scene title in the requested language
NARRATION: concise narration in the requested language
DIALOGUE: optional dialogue, or NONE
ACTION: one physically clear visible action
VISUAL_RU: detailed concrete visual prompt in Russian
VISUAL_EN: detailed production-ready English prompt for an image-generation model
NEGATIVE: short English negative prompt
SEARCH: short English stock-media search query
[/scene-001]

Continuity rules:
- Previous scene state is the starting state; next scene state is the destination when one is supplied.
- Preserve canonical character traits exactly.
- Do not teleport characters between boat, shore, raft, shelter, etc. Show a plausible transition.
- Do not invent a new recurring character, vehicle, location, magic element, costume or important prop unless required by the project idea or neighboring scenes.
- Never repeat an event that has already happened in the previous scene.
- Avoid impossible actions and contradictory spatial descriptions.
- The ACTION must be drawable as one clear shot.
- VISUAL_RU and VISUAL_EN must depict the same moment as ACTION.
- NEGATIVE must be plain English only.
"""


def _strict_parse_outline_text(content: str, target_scene_count: int, target_duration: int) -> dict:
    """Parse an outline but never fabricate missing tail scenes.

    The old parser cloned the last beat and created `Continuation N` scenes when the
    model returned too few SCENE lines. That silently converted a short outline into
    repeated story events. A short answer is now rejected so `_create_outline()` can
    retry the LLM instead.
    """
    title = "Untitled project"
    logline = ""
    visual_style = "cinematic animation"
    characters: list[str] = []
    scene_ideas: list[tuple[str, str]] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("TITLE:"):
            title = llama_mod._clean_line_value(line.split(":", 1)[1]) or title
        elif upper.startswith("LOGLINE:"):
            logline = llama_mod._clean_line_value(line.split(":", 1)[1])
        elif upper.startswith("STYLE:"):
            visual_style = llama_mod._clean_line_value(line.split(":", 1)[1]) or visual_style
        elif upper.startswith("CHARACTER:"):
            value = llama_mod._clean_line_value(line.split(":", 1)[1])
            if value:
                characters.append(value)
        elif upper.startswith("SCENE:"):
            value = llama_mod._clean_line_value(line.split(":", 1)[1])
            if "::" in value:
                scene_title, beat = value.split("::", 1)
            else:
                scene_title, beat = value, value
            scene_title = scene_title.strip()
            beat = beat.strip()
            if scene_title and beat:
                scene_ideas.append((scene_title, beat))

    if len(scene_ideas) != target_scene_count:
        raise ValueError(
            f"Outline incomplete: required exactly {target_scene_count} SCENE lines, got {len(scene_ideas)}"
        )

    durations = llama_mod._scene_durations(target_duration, target_scene_count)
    scenes: list[dict] = []
    for index, ((scene_title, beat), duration) in enumerate(zip(scene_ideas, durations), start=1):
        scenes.append({
            "id": f"scene-{index:03d}",
            "title": scene_title,
            "duration_seconds": duration,
            "beat": beat,
        })

    return {
        "title": title,
        "logline": logline,
        "visual_style": visual_style,
        "characters": characters,
        "scenes": scenes,
    }


def _neighbor_summary(scene) -> str:
    return (
        f"{scene.id}: {scene.title}\n"
        f"ACTION: {scene.action[:700]}\n"
        f"NARRATION: {scene.narration[:500]}\n"
        f"VISUAL: {(scene.visual_prompt_ru or scene.visual_prompt_en or scene.visual_prompt)[:700]}"
    )


def _extract_title(content: str, scene_id: str) -> str:
    block_match = re.search(
        rf"\[{re.escape(scene_id)}\](.*?)\[/{re.escape(scene_id)}\]",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    block = block_match.group(1) if block_match else content
    match = re.search(r"(?im)^\s*TITLE\s*:\s*(.+?)\s*$", block)
    return match.group(1).strip().strip("` ") if match else ""


async def _regenerate_scene_from_context(self, request, storyboard, scene):
    scenes = list(storyboard.scenes)
    try:
        index = next(i for i, item in enumerate(scenes) if item.id == scene.id)
    except StopIteration:
        index = -1

    previous = scenes[index - 1] if index > 0 else None
    following = scenes[index + 1] if 0 <= index < len(scenes) - 1 else None

    prompt_parts = [
        f"Project title: {storyboard.title}",
        f"Logline: {storyboard.logline}",
        f"Visual style: {storyboard.visual_style}",
        f"Canonical character descriptions: {'; '.join(storyboard.characters)}",
        f"Narration/dialogue language: {request.language}",
        f"Scene id: {scene.id}",
        f"Scene duration: {scene.duration_seconds:.0f}s",
        "",
    ]
    if previous:
        prompt_parts.extend(["PREVIOUS SCENE — authoritative starting state:", _neighbor_summary(previous), ""])

    # Keep only the intended event hint from the broken scene. Existing action,
    # narration and visual prompt are deliberately excluded so the model cannot
    # merely paraphrase the same contradiction again.
    prompt_parts.extend([
        "CURRENT SCENE — intent hint only; rewrite it freely if illogical:",
        f"OLD TITLE: {scene.title}",
        "",
    ])

    if following:
        prompt_parts.extend(["NEXT SCENE — continuity target, do not repeat it early:", _neighbor_summary(following), ""])

    prompt_parts.append(
        "Rebuild the current scene from scratch. Make the physical state transition explicit and causal. "
        "If the old title conflicts with neighboring states, correct the title too. /no_think"
    )

    content = await self._chat(
        messages=[
            {"role": "system", "content": REBUILD_SCENE_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(prompt_parts)},
        ],
        max_tokens=1100,
        temperature=0.45,
    )

    fields = llama_mod._extract_block_fields(content, scene.id)
    title = _extract_title(content, scene.id)
    visual_en = fields.get("VISUAL_EN") or fields.get("VISUAL")
    required = {
        "ACTION": fields.get("ACTION", "").strip(),
        "VISUAL_RU": fields.get("VISUAL_RU", "").strip(),
        "VISUAL_EN": (visual_en or "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Scene rebuild returned incomplete fields: {', '.join(missing)}")

    dialogue_value = fields.get("DIALOGUE", "").strip()
    dialogue = [] if dialogue_value.upper() in {"", "NONE", "NO", "N/A", "-"} else [dialogue_value]

    # model_copy preserves reference_keys, motion settings and media history. The old
    # regenerate implementation rebuilt Scene from a partial dict and could lose new
    # schema fields such as reference_keys.
    return scene.model_copy(update={
        "title": title or scene.title,
        "narration": fields.get("NARRATION", "").strip() or scene.narration,
        "dialogue": dialogue,
        "action": fields["ACTION"].strip(),
        "visual_prompt": visual_en.strip(),
        "visual_prompt_ru": fields["VISUAL_RU"].strip(),
        "visual_prompt_en": visual_en.strip(),
        "negative_prompt_en": fields.get("NEGATIVE", "").strip() or scene.negative_prompt_en,
        "media_search_query": fields.get("SEARCH", "").strip() or scene.media_search_query,
        "selected_audio": None,
    })


def install_storyboard_quality() -> None:
    """Install storyboard safety/continuity fixes without changing public APIs."""
    llama_mod._parse_outline_text = _strict_parse_outline_text
    llama_mod.LlamaCppProvider.regenerate_scene = _regenerate_scene_from_context
