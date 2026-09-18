import json
import re

import httpx
from pydantic import ValidationError

from app.config import settings
from app.providers.llm.base import LLMProvider
from app.schemas import CreateProjectRequest, Storyboard


SYSTEM_PROMPT = """You are the planning engine for an AI video production studio.
Return ONLY valid JSON. Never wrap the response in markdown.
Do not expose chain-of-thought or internal reasoning.

Create an original storyboard from the user's idea.
If the user references an existing literary work, use only high-level plot ideas and
character archetypes unless the user provides text they have rights to. Do not quote
or closely reproduce copyrighted prose.

JSON schema:
{
  "title": "string",
  "logline": "string",
  "visual_style": "string",
  "characters": ["string"],
  "scenes": [
    {
      "id": "scene-001",
      "title": "string",
      "duration_seconds": 8,
      "narration": "string",
      "dialogue": ["string"],
      "action": "string",
      "visual_prompt": "English visual-generation prompt",
      "media_search_query": "short English stock-media search query"
    }
  ]
}

Storyboard rules:
- Every scene MUST contain ALL schema keys exactly as written above.
- Never rename keys. In particular, scene identifier key is exactly "id".
- The SUM of all scene durations must be close to the requested target duration.
- Prefer practical scenes of about 8-12 seconds each.
- For a 180 second request, create roughly 15-20 scenes, not one long scene.
- Keep narration/dialogue concise enough to fit inside each scene duration.
- Keep scenes practical for later rendering with stock media, generated images/video,
  voice-over, subtitles and HyperFrames composition.
"""


REPAIR_SYSTEM_PROMPT = """You repair malformed JSON for a video storyboard.
Return ONLY corrected valid JSON, with no markdown and no explanation.
Do not rewrite the story unless necessary. Preserve scene content and durations.
Fix JSON syntax errors such as missing colons, commas, quotes, brackets or braces.
Every scene must contain these fields:
id, title, duration_seconds, narration, dialogue, action, visual_prompt, media_search_query.
If a scene identifier exists under a wrong key, move its value to "id".
If the input was truncated, close the current structure cleanly and preserve as many
complete scenes as possible. Never invent commentary outside the JSON object.
"""


def _extract_json(text: str) -> dict:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    elif text.startswith("~~~"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM response does not contain a complete JSON object")
        return json.loads(text[start : end + 1])


def _normalize_storyboard_data(data: dict) -> dict:
    """Repair small/local-LLM schema slips before strict Pydantic validation."""
    if not isinstance(data, dict):
        return data

    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        return data

    normalized_scenes: list[dict] = []

    for index, raw_scene in enumerate(scenes, start=1):
        if not isinstance(raw_scene, dict):
            normalized_scenes.append(raw_scene)
            continue

        scene = dict(raw_scene)

        # Small models occasionally invent a key like "Live": "scene-003".
        # If id is absent, recover a scene-* value from any unexpected key.
        if not scene.get("id"):
            for key, value in list(scene.items()):
                if key == "id":
                    continue
                if isinstance(value, str) and re.fullmatch(
                    r"scene[-_ ]?\d+", value.strip(), re.IGNORECASE
                ):
                    scene["id"] = value.strip().replace("_", "-").replace(" ", "-")
                    if key not in {
                        "title",
                        "narration",
                        "action",
                        "visual_prompt",
                        "media_search_query",
                    }:
                        scene.pop(key, None)
                    break

        if not scene.get("id"):
            scene["id"] = f"scene-{index:03d}"

        scene.setdefault("title", f"Scene {index}")
        scene.setdefault("narration", "")
        scene.setdefault("dialogue", [])
        scene.setdefault("media_search_query", "")

        if isinstance(scene.get("dialogue"), str):
            scene["dialogue"] = [scene["dialogue"]] if scene["dialogue"].strip() else []

        normalized_scenes.append(scene)

    result = dict(data)
    result["scenes"] = normalized_scenes
    return result


def _duration_seconds(storyboard: Storyboard) -> float:
    return sum(scene.duration_seconds for scene in storyboard.scenes)


def _duration_is_acceptable(storyboard: Storyboard, target: int) -> bool:
    if not storyboard.scenes:
        return False
    actual = _duration_seconds(storyboard)
    lower = target * 0.85
    upper = target * 1.15
    return lower <= actual <= upper


class LlamaCppProvider(LLMProvider):
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

            # Older llama.cpp builds may not understand response_format.
            # Retry once without it instead of failing the whole request.
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

    async def _repair_content(
        self,
        content: str,
        error_text: str,
        max_tokens: int,
    ) -> Storyboard:
        repair_prompt = (
            "Repair the malformed storyboard below. Return the COMPLETE corrected "
            "JSON object only.\n\n"
            "Detected error:\n"
            f"{error_text}\n\n"
            "Malformed storyboard text:\n"
            f"{content}"
        )

        repaired_content = await self._chat(
            messages=[
                {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
                {"role": "user", "content": repair_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
            json_mode=True,
        )

        try:
            repaired_data = _extract_json(repaired_content)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                "Storyboard JSON remained malformed after automatic repair: "
                f"{exc}"
            ) from exc

        repaired_data = _normalize_storyboard_data(repaired_data)
        try:
            return Storyboard.model_validate(repaired_data)
        except ValidationError as exc:
            raise ValueError(
                "Storyboard schema invalid after automatic repair: "
                f"{exc}"
            ) from exc

    async def _validate_or_repair(self, content: str, max_tokens: int) -> Storyboard:
        # Stage 1: JSON syntax. If json.loads fails, repair the RAW model output.
        try:
            parsed = _extract_json(content)
        except (json.JSONDecodeError, ValueError) as exc:
            return await self._repair_content(
                content=content,
                error_text=f"JSON parse error: {exc}",
                max_tokens=max_tokens,
            )

        # Stage 2: known harmless schema slips.
        parsed = _normalize_storyboard_data(parsed)

        # Stage 3: strict schema validation. If this fails, repair the parsed JSON.
        try:
            return Storyboard.model_validate(parsed)
        except ValidationError as exc:
            return await self._repair_content(
                content=json.dumps(parsed, ensure_ascii=False),
                error_text=f"Schema validation error: {exc}",
                max_tokens=max_tokens,
            )

    async def _request_storyboard(
        self,
        request: CreateProjectRequest,
        correction: str = "",
    ) -> Storyboard:
        target_scene_count = max(1, round(request.duration_seconds / 10))
        user_prompt = (
            "Create a storyboard.\n\n"
            f"User idea:\n{request.prompt}\n\n"
            f"Project type: {request.project_type}\n"
            f"Aspect ratio: {request.aspect_ratio}\n"
            f"Target duration: {request.duration_seconds} seconds\n"
            f"Target scene count: about {target_scene_count}\n"
            f"Language for narration/dialogue: {request.language}\n"
            "Keep every field concise. visual_prompt should be one compact sentence.\n"
            f"{correction}"
        )

        dynamic_max_tokens = min(
            3200,
            max(settings.llm_max_tokens, target_scene_count * 130),
        )

        content = await self._chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=dynamic_max_tokens,
            temperature=settings.llm_temperature,
            json_mode=True,
        )

        return await self._validate_or_repair(content, max_tokens=dynamic_max_tokens)

    async def create_storyboard(self, request: CreateProjectRequest) -> Storyboard:
        storyboard = await self._request_storyboard(request)

        if _duration_is_acceptable(storyboard, request.duration_seconds):
            return storyboard

        actual = _duration_seconds(storyboard)
        correction = (
            "\nIMPORTANT CORRECTION:\n"
            f"A previous attempt totaled only {actual:.1f} seconds, but the requested "
            f"duration is {request.duration_seconds} seconds. Regenerate the COMPLETE "
            "storyboard with enough distinct scenes so that the sum of duration_seconds "
            "is within +/-15% of the requested duration. Do not return a summary or a "
            "single sample scene.\n"
        )
        storyboard = await self._request_storyboard(request, correction=correction)

        if not _duration_is_acceptable(storyboard, request.duration_seconds):
            actual = _duration_seconds(storyboard)
            raise ValueError(
                "Storyboard duration mismatch after retry: "
                f"requested={request.duration_seconds}s, generated={actual:.1f}s, "
                f"scenes={len(storyboard.scenes)}"
            )

        return storyboard
