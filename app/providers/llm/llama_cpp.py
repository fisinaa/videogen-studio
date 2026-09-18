import json
import math
import re

import httpx
from pydantic import ValidationError

from app.config import settings
from app.providers.llm.base import LLMProvider
from app.schemas import CreateProjectRequest, Storyboard


OUTLINE_SYSTEM_PROMPT = """You are a planning engine for an AI video production studio.
Return ONLY valid JSON. Never use markdown and never expose chain-of-thought.

Create a compact scene outline, not a detailed screenplay.
If the user references an existing literary work, use only high-level plot ideas and
character archetypes unless the user provides text they have rights to. Do not quote
or closely reproduce copyrighted prose.

Required JSON schema:
{
  "title": "string",
  "logline": "string",
  "visual_style": "string",
  "characters": ["string"],
  "scenes": [
    {
      "id": "scene-001",
      "title": "string",
      "duration_seconds": 10,
      "beat": "one short sentence describing what happens"
    }
  ]
}

Rules:
- Every scene must contain id, title, duration_seconds and beat.
- Scene ids must be sequential: scene-001, scene-002, ...
- Prefer scenes of 8-12 seconds.
- The sum of duration_seconds must be close to the requested total duration.
- Keep beat extremely concise so the JSON stays small and reliable.
"""


DETAIL_SYSTEM_PROMPT = """You expand a SMALL batch of scene outline items into render-ready scene data.
Return ONLY valid JSON. Never use markdown and never expose chain-of-thought.

Required JSON schema:
{
  "scenes": [
    {
      "id": "scene-001",
      "title": "string",
      "duration_seconds": 10,
      "narration": "string",
      "dialogue": ["string"],
      "action": "string",
      "visual_prompt": "compact English visual-generation prompt",
      "media_search_query": "short English stock-media search query"
    }
  ]
}

Rules:
- Preserve each input scene id, title and duration_seconds exactly.
- Return exactly the scenes supplied in the batch and no others.
- Keep narration/dialogue short enough for the scene duration.
- visual_prompt must be one compact English sentence.
- media_search_query must be short and searchable.
"""


REPAIR_SYSTEM_PROMPT = """You repair malformed JSON.
Return ONLY corrected valid JSON, with no markdown and no explanation.
Preserve the original data as much as possible.
Fix missing commas, colons, quotes, brackets and braces.
"""


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    elif text.startswith("~~~"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    return text


def _extract_json(text: str) -> dict:
    text = _strip_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM response does not contain a complete JSON object")
        return json.loads(text[start : end + 1])


def _duration_seconds(storyboard: Storyboard) -> float:
    return sum(scene.duration_seconds for scene in storyboard.scenes)


def _duration_is_acceptable(storyboard: Storyboard, target: int) -> bool:
    if not storyboard.scenes:
        return False
    actual = _duration_seconds(storyboard)
    return target * 0.85 <= actual <= target * 1.15


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
        json_mode: bool = True,
    ) -> str:
        payload = {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        timeout = httpx.Timeout(settings.llm_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                settings.llm_base_url.rstrip("/") + "/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            if response.status_code == 400 and json_mode:
                payload.pop("response_format", None)
                response = await client.post(
                    settings.llm_base_url.rstrip("/") + "/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
            response.raise_for_status()

        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def _parse_json_with_repair(
        self,
        content: str,
        max_tokens: int,
        label: str,
    ) -> dict:
        try:
            return _extract_json(content)
        except (json.JSONDecodeError, ValueError) as first_error:
            repaired = await self._chat(
                messages=[
                    {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Repair this {label} JSON.\n"
                            f"Parser error: {first_error}\n\n"
                            f"Malformed JSON:\n{content}"
                        ),
                    },
                ],
                max_tokens=max_tokens,
                temperature=0.0,
                json_mode=True,
            )
            try:
                return _extract_json(repaired)
            except (json.JSONDecodeError, ValueError) as second_error:
                raise ValueError(
                    f"{label} JSON remained malformed after automatic repair: {second_error}"
                ) from second_error

    def _normalize_outline(self, data: dict, target_scene_count: int) -> dict:
        if not isinstance(data, dict):
            raise ValueError("Outline must be a JSON object")

        scenes = data.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("Outline does not contain scenes")

        normalized: list[dict] = []
        for index, raw in enumerate(scenes, start=1):
            if not isinstance(raw, dict):
                continue
            duration = raw.get("duration_seconds", 10)
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                duration = 10.0
            duration = max(4.0, min(20.0, duration))
            normalized.append(
                {
                    "id": f"scene-{index:03d}",
                    "title": str(raw.get("title") or f"Scene {index}"),
                    "duration_seconds": duration,
                    "beat": str(raw.get("beat") or raw.get("action") or "Continue the story."),
                }
            )

        # If the model returned far too few scenes, fail here rather than trying to
        # fabricate a long film from one or two outline items.
        minimum = max(1, math.floor(target_scene_count * 0.70))
        if len(normalized) < minimum:
            raise ValueError(
                f"Outline too short: expected about {target_scene_count} scenes, "
                f"got {len(normalized)}"
            )

        result = dict(data)
        result["title"] = str(result.get("title") or "Untitled project")
        result["logline"] = str(result.get("logline") or "")
        result["visual_style"] = str(result.get("visual_style") or "cinematic animation")
        characters = result.get("characters")
        result["characters"] = characters if isinstance(characters, list) else []
        result["scenes"] = normalized
        return result

    async def _create_outline(self, request: CreateProjectRequest) -> dict:
        target_scene_count = max(1, round(request.duration_seconds / 10))
        prompt = (
            f"User idea:\n{request.prompt}\n\n"
            f"Project type: {request.project_type}\n"
            f"Aspect ratio: {request.aspect_ratio}\n"
            f"Target duration: {request.duration_seconds} seconds\n"
            f"Target scene count: about {target_scene_count}\n"
            f"Language: {request.language}\n"
            "Create the COMPLETE compact outline now."
        )

        content = await self._chat(
            messages=[
                {"role": "system", "content": OUTLINE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=min(2200, max(900, target_scene_count * 75)),
            temperature=settings.llm_temperature,
            json_mode=True,
        )
        data = await self._parse_json_with_repair(
            content,
            max_tokens=min(2200, max(900, target_scene_count * 75)),
            label="outline",
        )
        return self._normalize_outline(data, target_scene_count)

    async def _expand_batch(
        self,
        request: CreateProjectRequest,
        outline: dict,
        batch: list[dict],
    ) -> list[dict]:
        context = {
            "project_title": outline["title"],
            "logline": outline["logline"],
            "visual_style": outline["visual_style"],
            "characters": outline["characters"],
            "language": request.language,
            "scenes_to_expand": batch,
        }
        content = await self._chat(
            messages=[
                {"role": "system", "content": DETAIL_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Expand this batch:\n" + json.dumps(context, ensure_ascii=False),
                },
            ],
            max_tokens=1500,
            temperature=settings.llm_temperature,
            json_mode=True,
        )
        data = await self._parse_json_with_repair(content, max_tokens=1500, label="scene batch")
        scenes = data.get("scenes") if isinstance(data, dict) else None
        if not isinstance(scenes, list):
            raise ValueError("Scene batch response does not contain a scenes array")

        by_id = {str(item.get("id")): item for item in scenes if isinstance(item, dict)}
        result: list[dict] = []
        for source in batch:
            item = dict(by_id.get(source["id"], {}))
            item["id"] = source["id"]
            item["title"] = source["title"]
            item["duration_seconds"] = source["duration_seconds"]
            item.setdefault("narration", "")
            item.setdefault("dialogue", [])
            item.setdefault("action", source["beat"])
            item.setdefault("visual_prompt", source["beat"])
            item.setdefault("media_search_query", "")
            if isinstance(item["dialogue"], str):
                item["dialogue"] = [item["dialogue"]] if item["dialogue"].strip() else []
            result.append(item)
        return result

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

        if not _duration_is_acceptable(storyboard, request.duration_seconds):
            actual = _duration_seconds(storyboard)
            raise ValueError(
                "Storyboard duration mismatch after batched generation: "
                f"requested={request.duration_seconds}s, generated={actual:.1f}s, "
                f"scenes={len(storyboard.scenes)}"
            )

        return storyboard
