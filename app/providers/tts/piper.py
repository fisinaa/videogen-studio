from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.schemas import AudioAsset


class PiperTTSProvider:
    name = "piper"

    @property
    def enabled(self) -> bool:
        return (
            settings.piper_bin.is_file()
            and settings.piper_model.is_file()
        )

    async def generate(
        self,
        *,
        text: str,
        project_id: str,
        scene_id: str,
        audio_dir: Path,
    ) -> AudioAsset:
        if not self.enabled:
            raise RuntimeError(
                "Piper is not configured. Set PIPER_BIN and PIPER_MODEL to existing files."
            )

        clean_text = text.strip()
        if not clean_text:
            raise ValueError("TTS text is empty")

        audio_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{scene_id}-piper-{uuid4().hex[:8]}.wav"
        output_path = audio_dir / filename

        cmd = [
            str(settings.piper_bin),
            "--model",
            str(settings.piper_model),
            "--output_file",
            str(output_path),
        ]
        if settings.piper_speaker is not None:
            cmd.extend(["--speaker", str(settings.piper_speaker)])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate((clean_text + "\n").encode("utf-8")),
                timeout=settings.piper_timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeError("Piper TTS timed out") from exc

        if process.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"Piper exited with code {process.returncode}: {detail}")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("Piper did not create an audio file")

        local_url = f"/api/projects/{project_id}/audio/{filename}"
        return AudioAsset(
            provider=self.name,
            asset_id=filename,
            audio_url=local_url,
            download_url=local_url,
            format="wav",
            voice=settings.piper_model.stem,
            model=settings.piper_model.name,
            label=f"Piper local · {settings.piper_model.stem} · {scene_id}",
            local_path=f"audio/{filename}",
        )


piper_tts_provider = PiperTTSProvider()
