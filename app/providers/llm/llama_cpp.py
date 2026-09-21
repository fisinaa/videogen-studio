import math
import re

import httpx
from pydantic import ValidationError

from app.config import settings
from app.providers.llm.base import LLMProvider
from app.schemas import CreateProjectRequest, Scene, Storyboard
from app.services.model_orchestrator import model_orchestrator


OUTLINE_SYSTEM_PROMPT = """You are a planning engine for an AI video production studio.
Do NOT return JSON. Do NOT use markdown. Do NOT expose chain-of-thought.

Return a compact outline using ONLY these line prefixes:
TITLE: project title
LOGLINE: one short sentence
STYLE: short visual style description
CHARACTER: one character description
SCENE: short scene title :: one short sentence describing what happens

You may output multiple CHARACTER and SCENE lines.
Never use the token :: inside a title or description except as the separator shown above.
If the user references an existing literary work, use only high-level plot ideas and
character archetypes unless the user provides text they have rights to. Do not quote
or closely reproduce copyrighted prose.
"""


DETAIL_SYSTEM_PROMPT = """You expand a SMALL batch of scene outlines for video production.
Do NOT return JSON. Do NOT use markdown. Do NOT expose chain-of-thought.

For every requested scene, return exactly one block in this format:
[scene-001]
NARRATION: concise narration in the requested language
DIALOGUE: optional dialogue, or NONE
ACTION: concise visible action
VISUAL_RU: detailed concrete visual prompt in Russian
VISUAL_EN: detailed production-ready English prompt for an image-generation model
NEGATIVE: short English negative prompt describing unwanted visual errors
SEARCH: short English stock-media search query
[/scene-001]

Preserve the supplied scene ids. Always include ALL seven fields.

VISUAL prompt rules:
- Do NOT write a literary retelling. Describe what must be visible in the frame.
- Explicitly describe the main character, stable physical traits, visible action, environment and key props.
- Preserve supplied character traits across scenes. Do not silently change color, species, clothes or distinctive features.
- Include camera/framing/composition, lighting, mood and the supplied project visual style.
- State important spatial relationships when relevant: foreground/background, left/right, near/far, on the river/on the bank.
- Do not invent extra characters or major objects unless the scene needs them.
- Prefer concrete visual language over vague words such as beautiful, interesting or magical.
- VISUAL_EN should normally be 70-160 words and be directly usable by a local image model.
- VISUAL_RU should describe the same frame for human editing.
- NEGATIVE should suppress text, watermark, extra characters, anatomy errors and scene-specific mistakes.
- SEARCH must stay short and utilitarian.
"""


SINGLE_SCENE_SYSTEM_PROMPT = """You rewrite ONE existing video scene.
Do NOT return JSON. Do NOT use markdown. Do NOT expose chain-of-thought.

Return exactly one block using the supplied scene id:
[scene-001]
NARRATION: concise narration in the requested language
DIALOGUE: optional dialogue, or NONE
ACTION: concise visible action
VISUAL_RU: detailed concrete visual prompt in Russian
VISUAL_EN: detailed production-ready English prompt for an image-generation model
NEGATIVE: short English negative prompt
SEARCH: short English stock-media search query
[/scene-001]

Always include ALL seven fields. Keep the scene consistent with the project title,
logline, visual style, character descriptions and neighboring context. Do not change
the scene id or duration. VISUAL_EN must describe subject, action, environment, props,
camera/composition, lighting, mood and style using concrete visual language.
"""


VISUAL_ONLY_SYSTEM_PROMPT = """You are the visual prompt builder for an AI animation studio.
Do NOT return JSON. Do NOT use markdown. Do NOT expose chain-of-thought.

Return exactly this block for the supplied scene id:
[scene-001]
VISUAL_RU: detailed concrete visual prompt in Russian
VISUAL_EN: detailed production-ready English prompt for an image-generation model
NEGATIVE: short English negative prompt
SEARCH: short English stock-media search query
[/scene-001]

This is a production prompt, not prose. VISUAL_EN should normally be 70-160 words.
Describe the exact visible frame: main character and stable traits, action, environment,
key objects, spatial relationships, camera/framing/composition, lighting, mood and the
project visual style. Preserve all supplied character traits. Do not invent extra
characters. Make important story objects unmistakable. NEGATIVE should suppress text,
watermarks, extra characters, anatomy errors and scene-specific mistakes.
"""


def _scene_durations(total_seconds: int, scene_count: int) -> list[float]:
    scene_count = max(1, scene_count)
    base = total_seconds // scene_count
    remainder = total_seconds % scene_count
    return [float(base + (1 if index < remainder else 0)) for index in range(scene_count)]


def _clean_line_value(value: str) -> str:
    return value.strip().strip("` ")


def _parse_outline_text(content: str, target_scene_count: int, target_duration: int) -> dict:
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
            title = _clean_line_value(line.split(":", 1)[1]) or title
        elif upper.startswith("LOGLINE:"):
            logline = _clean_line_value(line.split(":", 1)[1])
        elif upper.startswith("STYLE:"):
            visual_style = _clean_line_value(line.split(":", 1)[1]) or visual_style
        elif upper.startswith("CHARACTER:"):
            value = _clean_line_value(line.split(":", 1)[1])
            if value:
                characters.append(value)
        elif upper.startswith("SCENE:"):
            value = _clean_line_value(line.split(":", 1)[1])
            if "::" in value:
                scene_title, beat = value.split("::", 1)
            else:
                scene_title, beat = value, value
            scene_title = scene_title.strip() or f"Scene {len(scene_ideas) + 1}"
            beat = beat.strip() or "Continue the story."
            scene_ideas.append((scene_title, beat))

    minimum = max(1, math.floor(target_scene_count * 0.70))
    if len(scene_ideas) < minimum:
        raise ValueError(
            f"Outline too short: expected about {target_scene_count} scenes, parsed {len(scene_ideas)} SCENE lines"
        )

    scene_ideas = scene_ideas[:target_scene_count]
    while len(scene_ideas) < target_scene_count:
        _, previous_beat = scene_ideas[-1]
        scene_ideas.append((f"Continuation {len(scene_ideas) + 1}", previous_beat))

    durations = _scene_durations(target_duration, target_scene_count)
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


def _extract_block_fields(content: str, scene_id: str) -> dict[str, str]:
    pattern = re.compile(
        rf"\[{re.escape(scene_id)}\](.*?)\[/{re.escape(scene_id)}\]",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(content)
    block = match.group(1) if match else ""
    fields: dict[str, str] = {}
    valid = {
        "NARRATION", "DIALOGUE", "ACTION", "VISUAL", "VISUAL_RU",
        "VISUAL_EN", "NEGATIVE", "SEARCH",
    }
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().upper()
        if key in valid:
            fields[key] = value.strip()
    return fields


def _parse_detail_blocks(content: str, batch: list[dict]) -> list[dict]:
    result: list[dict] = []
    for source in batch:
        scene_id = source["id"]
        fields = _extract_block_fields(content, scene_id)
        dialogue_value = fields.get("DIALOGUE", "")
        dialogue = []
        if dialogue_value and dialogue_value.upper() not in {"NONE", "NO", "N/A", "-"}:
            dialogue = [dialogue_value]

        visual_en = fields.get("VISUAL_EN") or fields.get("VISUAL") or source.get("beat") or "cinematic animation"
        visual_ru = fields.get("VISUAL_RU", "")
        result.append({
            "id": scene_id,
            "title": source["title"],
            "duration_seconds": source["duration_seconds"],
            "narration": fields.get("NARRATION", ""),
            "dialogue": dialogue,
            "action": fields.get("ACTION") or source.get("beat") or "Continue the story.",
            "visual_prompt": visual_en,
            "visual_prompt_ru": visual_ru,
            "visual_prompt_en": visual_en,
            "negative_prompt_en": fields.get("NEGATIVE", ""),
            "media_search_query": fields.get("SEARCH", ""),
            "_raw_fields": fields,
        })
    return result


def _duration_seconds(storyboard: Storyboard) -> float:
    return sum(scene.duration_seconds for scene in storyboard.scenes)


class LlamaCppProvider(LLMProvider):
    batch_size = 4

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        return headers

    async def _chat(self, messages: list[dict], max_tokens: int, temperature: float) -> str:
        await model_orchestrator.ensure_llm_running()
        payload = {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        timeout = httpx.Timeout(settings.llm_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                settings.llm_base_url.rstrip("/") + "/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def _create_outline(self, request: CreateProjectRequest) -> dict:
        target_scene_count = max(1, round(request.duration_seconds / 10))
        prompt = (
            f"User idea:\n{request.prompt}\n\n"
            f"Project type: {request.project_type}\n"
            f"Aspect ratio: {request.aspect_ratio}\n"
            f"Target duration: {request.duration_seconds} seconds\n"
            f"Required SCENE lines: exactly {target_scene_count}\n"
            f"Language for TITLE, LOGLINE, CHARACTER and SCENE text: {request.language}\n\n"
            f"Return exactly {target_scene_count} SCENE: lines. Keep each scene idea very short."
        )
        content = await self._chat(
            messages=[
                {"role": "system", "content": OUTLINE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=min(1800, max(700, target_scene_count * 55)),
            temperature=settings.llm_temperature,
        )
        try:
            return _parse_outline_text(content, target_scene_count, request.duration_seconds)
        except ValueError:
            retry_prompt = prompt + (
                "\nIMPORTANT: your previous answer did not contain enough SCENE: lines. "
                f"Return exactly {target_scene_count} SCENE: lines now. No commentary."
            )
            retry_content = await self._chat(
                messages=[
                    {"role": "system", "content": OUTLINE_SYSTEM_PROMPT},
                    {"role": "user", "content": retry_prompt},
                ],
                max_tokens=min(1800, max(700, target_scene_count * 55)),
                temperature=0.3,
            )
            return _parse_outline_text(retry_content, target_scene_count, request.duration_seconds)

    async def _expand_batch(self, request: CreateProjectRequest, outline: dict, batch: list[dict]) -> list[dict]:
        scene_lines = "\n".join(
            f"{scene['id']} | {scene['duration_seconds']:.0f}s | {scene['title']} | {scene['beat']}"
            for scene in batch
        )
        prompt = (
            f"Project title: {outline['title']}\n"
            f"Logline: {outline['logline']}\n"
            f"Visual style: {outline['visual_style']}\n"
            f"Canonical character descriptions: {'; '.join(outline['characters'])}\n"
            f"Narration/dialogue language: {request.language}\n\n"
            "Expand these scenes:\n"
            f"{scene_lines}\n\n"
            "For every image prompt, repeat the stable character traits that are actually visible in the shot. "
            "Make the scene's important object and spatial relationship explicit. Return one marked block for every supplied scene id."
        )
        content = await self._chat(
            messages=[
                {"role": "system", "content": DETAIL_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1800,
            temperature=settings.llm_temperature,
        )
        parsed = _parse_detail_blocks(content, batch)
        for item in parsed:
            item.pop("_raw_fields", None)
        return parsed

    async def rebuild_visual_prompt(self, storyboard: Storyboard, scene: Scene) -> Scene:
        prompt = (
            f"Project title: {storyboard.title}\n"
            f"Logline: {storyboard.logline}\n"
            f"Visual style: {storyboard.visual_style}\n"
            f"Canonical character descriptions: {'; '.join(storyboard.characters)}\n\n"
            f"Scene id: {scene.id}\n"
            f"Scene title: {scene.title}\n"
            f"Visible action: {scene.action}\n"
            f"Narration: {scene.narration}\n"
            f"Dialogue: {' | '.join(scene.dialogue) if scene.dialogue else 'NONE'}\n"
            f"Current visual prompt: {scene.visual_prompt_en or scene.visual_prompt}\n\n"
            "Rebuild only the visual-generation prompt. Keep the story facts and canonical character traits. "
            "Be specific enough that a local image model does not need to invent the composition."
        )
        content = await self._chat(
            messages=[
                {"role": "system", "content": VISUAL_ONLY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
            temperature=0.35,
        )
        fields = _extract_block_fields(content, scene.id)
        visual_en = fields.get("VISUAL_EN") or fields.get("VISUAL")
        if not visual_en:
            raise ValueError("Prompt builder returned no VISUAL_EN field")
        return scene.model_copy(update={
            "visual_prompt": visual_en,
            "visual_prompt_ru": fields.get("VISUAL_RU", scene.visual_prompt_ru),
            "visual_prompt_en": visual_en,
            "negative_prompt_en": fields.get("NEGATIVE", scene.negative_prompt_en),
            "media_search_query": fields.get("SEARCH", scene.media_search_query),
        })

    async def regenerate_scene(self, request: CreateProjectRequest, storyboard: Storyboard, scene: Scene) -> Scene:
        source = {
            "id": scene.id,
            "title": scene.title,
            "duration_seconds": scene.duration_seconds,
            "beat": scene.action,
        }
        prompt = (
            f"Project title: {storyboard.title}\n"
            f"Logline: {storyboard.logline}\n"
            f"Visual style: {storyboard.visual_style}\n"
            f"Canonical character descriptions: {'; '.join(storyboard.characters)}\n"
            f"Narration/dialogue language: {request.language}\n"
            f"Scene duration: {scene.duration_seconds:.0f}s\n"
            f"Existing title: {scene.title}\n"
            f"Existing action: {scene.action}\n"
            f"Existing narration: {scene.narration}\n"
            f"Existing dialogue: {' | '.join(scene.dialogue) if scene.dialogue else 'NONE'}\n"
            f"Existing visual prompt: {scene.visual_prompt_en or scene.visual_prompt}\n"
            f"Existing search query: {scene.media_search_query}\n\n"
            f"Rewrite only {scene.id}. Keep the same story purpose but improve the scene."
        )
        content = await self._chat(
            messages=[
                {"role": "system", "content": SINGLE_SCENE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
            temperature=settings.llm_temperature,
        )
        parsed = _parse_detail_blocks(content, [source])[0]
        raw_fields = parsed.pop("_raw_fields", {})
        merged = {
            "id": scene.id,
            "title": scene.title,
            "duration_seconds": scene.duration_seconds,
            "narration": parsed["narration"] if raw_fields.get("NARRATION", "").strip() else scene.narration,
            "dialogue": parsed["dialogue"] if "DIALOGUE" in raw_fields else scene.dialogue,
            "action": parsed["action"] if raw_fields.get("ACTION", "").strip() else scene.action,
            "visual_prompt": parsed["visual_prompt"] if raw_fields.get("VISUAL_EN", raw_fields.get("VISUAL", "")).strip() else scene.visual_prompt,
            "visual_prompt_ru": parsed["visual_prompt_ru"] if raw_fields.get("VISUAL_RU", "").strip() else scene.visual_prompt_ru,
            "visual_prompt_en": parsed["visual_prompt_en"] if raw_fields.get("VISUAL_EN", raw_fields.get("VISUAL", "")).strip() else (scene.visual_prompt_en or scene.visual_prompt),
            "negative_prompt_en": parsed["negative_prompt_en"] if raw_fields.get("NEGATIVE", "").strip() else scene.negative_prompt_en,
            "media_search_query": parsed["media_search_query"] if raw_fields.get("SEARCH", "").strip() else scene.media_search_query,
            "selected_media": scene.selected_media,
            "media_candidates": scene.media_candidates,
            "selected_audio": None,
        }
        return Scene.model_validate(merged)

    async def create_storyboard(self, request: CreateProjectRequest) -> Storyboard:
        outline = await self._create_outline(request)
        outline_scenes = outline["scenes"]
        detailed_scenes: list[dict] = []
        for start in range(0, len(outline_scenes), self.batch_size):
            batch = outline_scenes[start : start + self.batch_size]
            detailed_scenes.extend(await self._expand_batch(request, outline, batch))

        payload = {
            "title": outline["title"],
            "logline": outline["logline"],
            "visual_style": outline["visual_style"],
            "characters": outline["characters"],
            "scenes": detailed_scenes,
        }
        try:
            storyboard = Storyboard.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"Assembled storyboard schema invalid: {exc}") from exc

        actual = _duration_seconds(storyboard)
        if abs(actual - request.duration_seconds) > 0.01:
            raise ValueError(
                "Internal duration allocation error: "
                f"requested={request.duration_seconds}s, assembled={actual:.1f}s"
            )
        return storyboard
