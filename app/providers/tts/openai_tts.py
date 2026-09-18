from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx

from app.config import settings
from app.schemas import AudioAsset


class OpenAITTSProvider:
    name = "openai_tts"

    @property
    def enabled(self) -> bool:
        return bool(settings.openai_api_key)

    async def generate(
        self,
        *,
        text: str,
        project_id: str,
        scene_id: str,
        audio_dir: Path,
    ) -> AudioAsset:
        if not self.enabled:
            raise RuntimeError("OpenAI TTS is not configured. Set OPENAI_API_KEY in .env.")

        clean_text = text.strip()
        if not clean_text:
            raise ValueError("TTS text is empty")
        if len(clean_text) > 4096:
            raise ValueError("TTS text is too long for one scene (max 4096 characters)")

        audio_format = settings.openai_tts_format.strip().lower() or "mp3"
        if audio_format not in {"mp3", "opus", "aac", "flac", "wav", "pcm"}:
            raise ValueError(f"Unsupported TTS format: {audio_format}")

        endpoint = settings.openai_base_url.rstrip("/") + "/audio/speech"
        payload = {
            "model": settings.openai_tts_model,
            "voice": settings.openai_tts_voice,
            "input": clean_text,
            "instructions": settings.openai_tts_instructions,
            "response_format": audio_format,
        }
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(settings.openai_tts_timeout_seconds)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            audio_bytes = response.content

        if not audio_bytes:
            raise ValueError("OpenAI TTS returned an empty audio response")

        audio_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{scene_id}-openai-tts-{uuid4().hex[:8]}.{audio_format}"
        output_path = audio_dir / filename
        output_path.write_bytes(audio_bytes)

        local_url = f"/api/projects/{project_id}/audio/{filename}"
        return AudioAsset(
            provider=self.name,
            asset_id=filename,
            audio_url=local_url,
            download_url=local_url,
            format=audio_format,
            voice=settings.openai_tts_voice,
            model=settings.openai_tts_model,
            label=f"{settings.openai_tts_model} · {settings.openai_tts_voice} · {scene_id}",
            local_path=f"audio/{filename}",
        )


openai_tts_provider = OpenAITTSProvider()
