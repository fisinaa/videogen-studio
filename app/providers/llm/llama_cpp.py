import json

import httpx

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

The total scene duration should approximately match the requested duration.
Keep scenes practical for later rendering with stock media, generated images/video,
voice-over, subtitles and HyperFrames composition.
"""


def _extract_json(text: str) -> dict:
    text = text.strip()

    if text.startswith("~~~"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM response does not contain a JSON object")
        return json.loads(text[start : end + 1])


class LlamaCppProvider(LLMProvider):
    async def create_storyboard(self, request: CreateProjectRequest) -> Storyboard:
        user_prompt = (
            "Create a storyboard.\n\n"
            f"User idea:\n{request.prompt}\n\n"
            f"Project type: {request.project_type}\n"
            f"Aspect ratio: {request.aspect_ratio}\n"
            f"Target duration: {request.duration_seconds} seconds\n"
            f"Language for narration/dialogue: {request.language}\n"
        )

        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
        }

        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"

        timeout = httpx.Timeout(settings.llm_timeout_seconds)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                settings.llm_base_url.rstrip("/") + "/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        return Storyboard.model_validate(parsed)
