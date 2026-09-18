import math
import re

import httpx
from pydantic import ValidationError

from app.config import settings
from app.providers.llm.base import LLMProvider
from app.schemas import CreateProjectRequest, Storyboard


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
VISUAL: one compact English visual-generation prompt
SEARCH: short English stock-media search query
[/scene-001]

Preserve the supplied scene ids. Keep text concise. If no dialogue is needed, write NONE.
"""


def _scene_durations(total_seconds: int, scene_count: int) -> list[float]:
    """Distribute duration deterministically so the total is exact."""
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
            f"Outline too short: expected about {target_scene_count} scenes, "
            f"parsed {len(scene_ideas)} SCENE lines"
        )

    # If the model produced a few extra ideas, keep only the requested amount.
    scene_ideas = scene_ideas[:target_scene_count]

    # If it is only slightly short, duplicate/continue the last narrative beat instead
    # of throwing away an otherwise usable outline. Detailed generation will make each
    # scene distinct later.
    while len(scene_ideas) < target_scene_count:
        previous_title, previous_beat = scene_ideas[-1]
        scene_ideas.append((f"Continuation {len(scene_ideas) + 1}", previous_beat))

    durations = _scene_durations(target_duration, target_scene_count)
    scenes: list[dict] = []
    for index, ((scene_title, beat), duration) in enumerate(zip(scene_ideas, durations), start=1):
        scenes.append(
            {
                "id": f"scene-{index:03d}",
                "title": scene_title,
                "duration_seconds": duration,
                "beat": beat,
            }
        )

    return {
        "title": title,
        "logline": logline,
        "visual_style": visual_style,
        "characters": characters,
        "scenes": scenes,
    }


def _parse_detail_blocks(content: str, batch: list[dict]) -> list[dict]:
    result: list[dict] = []

    for source in batch:
        scene_id = source["id"]
        pattern = re.compile(
            rf"\[{re.escape(scene_id)}\](.*?)\[/{re.escape(scene_id)}\]",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(content)
        block = match.group(1) if match else ""

        fields: dict[str, str] = {}
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().upper()
            if key in {"NARRATION", "DIALOGUE", "ACTION", "VISUAL", "SEARCH"}:
                fields[key] = value.strip()

        dialogue_value = fields.get("DIALOGUE", "")
        dialogue = []
        if dialogue_value and dialogue_value.upper() not in {"NONE", "NO", "N/A", "-"}:
            dialogue = [dialogue_value]

        result.append(
            {
                "id": scene_id,
                "title": source["title"],
                "duration_seconds": source["duration_seconds"],
                "narration": fields.get("NARRATION", ""),
                "dialogue": dialogue,
                "action": fields.get("ACTION") or source["beat"],
                "visual_prompt": fields.get("VISUAL") or source["beat"],
                "media_search_query": fields.get("SEARCH", ""),
            }
        )

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

    async def _chat(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> str:
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
            return _parse_outline_text(
                content,
                target_scene_count=target_scene_count,
                target_duration=request.duration_seconds,
            )
        except ValueError:
            # One short retry is enough because the output format is now line-based,
            # not a fragile nested JSON document.
            retry_prompt = (
                prompt
                + "\nIMPORTANT: your previous answer did not contain enough SCENE: lines. "
                + f"Return exactly {target_scene_count} SCENE: lines now. No commentary."
            )
            retry_content = await self._chat(
                messages=[
                    {"role": "system", "content": OUTLINE_SYSTEM_PROMPT},
                    {"role": "user", "content": retry_prompt},
                ],
                max_tokens=min(1800, max(700, target_scene_count * 55)),
                temperature=0.3,
            )
            return _parse_outline_text(
                retry_content,
                target_scene_count=target_scene_count,
                target_duration=request.duration_seconds,
            )

    async def _expand_batch(
        self,
        request: CreateProjectRequest,
        outline: dict,
        batch: list[dict],
    ) -> list[dict]:
        scene_lines = "\n".join(
            f"{scene['id']} | {scene['duration_seconds']:.0f}s | {scene['title']} | {scene['beat']}"
            for scene in batch
        )
        prompt = (
            f"Project title: {outline['title']}\n"
            f"Logline: {outline['logline']}\n"
            f"Visual style: {outline['visual_style']}\n"
            f"Characters: {'; '.join(outline['characters'])}\n"
            f"Narration/dialogue language: {request.language}\n\n"
            "Expand these scenes:\n"
            f"{scene_lines}\n\n"
            "Return one marked block for every supplied scene id."
        )

        content = await self._chat(
            messages=[
                {"role": "system", "content": DETAIL_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200,
            temperature=settings.llm_temperature,
        )

        # Parsing is intentionally tolerant. If the model omits a block or a field,
        # _parse_detail_blocks falls back to the outline beat instead of failing the job.
        return _parse_detail_blocks(content, batch)

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

        # Durations are generated by Python, not the LLM, so this should be exact.
        actual = _duration_seconds(storyboard)
        if abs(actual - request.duration_seconds) > 0.01:
            raise ValueError(
                "Internal duration allocation error: "
                f"requested={request.duration_seconds}s, assembled={actual:.1f}s"
            )

        return storyboard
