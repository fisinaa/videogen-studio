from __future__ import annotations

import os

import httpx

from app.config import settings
from app.providers.llm.llama_cpp import LlamaCppProvider


class OpenAIStoryboardProvider(LlamaCppProvider):
    """Use OpenAI for storyboard text generation while reusing VideoGen parsing logic."""

    batch_size = 4

    def __init__(self) -> None:
        self.model_name = os.getenv("OPENAI_STORYBOARD_MODEL", "gpt-5-mini")
        self.calls = 0

    def reset_calls(self) -> None:
        self.calls = 0

    async def _chat(self, messages: list[dict], max_tokens: int, temperature: float) -> str:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not configured")

        self.calls += 1
        payload: dict = {
            "model": self.model_name,
            "messages": messages,
            "max_completion_tokens": max(2400, int(max_tokens)),
        }
        # GPT-5 family models are safest with their default sampling settings.
        if not self.model_name.lower().startswith("gpt-5"):
            payload["temperature"] = temperature

        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(max(settings.llm_timeout_seconds, 300))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                settings.openai_base_url.rstrip("/") + "/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"OpenAI returned an unexpected chat response: {data}") from exc
        if not content or not str(content).strip():
            raise ValueError("OpenAI returned an empty storyboard response")
        return str(content)
