from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.providers.tts.openai_tts import OpenAITTSProvider
from app.providers.tts.piper import PiperTTSProvider
from app.schemas import AudioAsset


class TTSRouter:
    def __init__(self) -> None:
        self.piper = PiperTTSProvider()
        self.openai = OpenAITTSProvider()

    def status(self) -> dict:
        return {
            "selected": settings.tts_provider,
            "piper": self.piper.enabled,
            "openai": self.openai.enabled,
        }

    def _providers(self):
        mode = settings.tts_provider.strip().lower()
        if mode == "piper":
            return [self.piper]
        if mode == "openai":
            return [self.openai]
        return [self.piper, self.openai]

    async def generate(
        self,
        *,
        text: str,
        project_id: str,
        scene_id: str,
        audio_dir: Path,
    ) -> AudioAsset:
        errors: list[str] = []
        for provider in self._providers():
            if not provider.enabled:
                errors.append(f"{provider.name}: disabled")
                continue
            try:
                return await provider.generate(
                    text=text,
                    project_id=project_id,
                    scene_id=scene_id,
                    audio_dir=audio_dir,
                )
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
                if settings.tts_provider.strip().lower() != "auto":
                    raise
        raise RuntimeError("No TTS provider succeeded: " + "; ".join(errors))


tts_router = TTSRouter()
