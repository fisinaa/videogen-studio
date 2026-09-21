from __future__ import annotations

import asyncio
import shlex
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.schemas import MediaAsset, Project, Scene
from app.services.model_orchestrator import model_orchestrator
from app.services.production import probe_duration
from app.storage import project_store


class MotionGenerationError(RuntimeError):
    pass


class MotionService:
    """Generate image-to-video clips through a configurable local command."""

    def __init__(self) -> None:
        self._runtime: dict = {
            "state": "idle",
            "stage": "idle",
            "detail": "Motion worker is idle.",
            "project_id": None,
            "scene_id": None,
            "pid": None,
            "started_monotonic": None,
            "elapsed_seconds": 0.0,
            "output": None,
            "error": None,
        }

    @property
    def provider(self) -> str:
        return settings.motion_provider.strip().lower()

    @property
    def enabled(self) -> bool:
        return self.provider == "command" and bool(settings.motion_command.strip())

    def _set_runtime(self, **updates) -> None:
        self._runtime.update(updates)
        started = self._runtime.get("started_monotonic")
        self._runtime["elapsed_seconds"] = round(time.monotonic() - started, 1) if started else 0.0

    def runtime_status(self) -> dict:
        result = dict(self._runtime)
        started = result.pop("started_monotonic", None)
        result["elapsed_seconds"] = round(time.monotonic() - started, 1) if started else float(result.get("elapsed_seconds") or 0.0)
        return result

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "command_configured": bool(settings.motion_command.strip()),
            "default_duration_seconds": settings.motion_default_duration_seconds,
            "max_duration_seconds": settings.motion_max_duration_seconds,
            "gpu_orchestration": model_orchestrator.enabled,
            "runtime": self.runtime_status(),
            "note": (
                "Local image-to-video command provider is ready."
                if self.enabled
                else "AI motion is not configured yet. Set MOTION_PROVIDER=command and MOTION_COMMAND to a local image-to-video runner."
            ),
        }

    @staticmethod
    def _dimensions(aspect_ratio: str) -> tuple[int, int]:
        return {
            "16:9": (settings.motion_width_16_9, settings.motion_height_16_9),
            "9:16": (settings.motion_width_9_16, settings.motion_height_9_16),
            "1:1": (settings.motion_width_1_1, settings.motion_height_1_1),
        }.get(aspect_ratio, (settings.motion_width_16_9, settings.motion_height_16_9))

    @staticmethod
    def _source_image(project: Project, scene: Scene) -> Path:
        asset = scene.selected_media
        if asset is None:
            raise MotionGenerationError("Scene has no selected image")
        if asset.media_type != "image":
            raise MotionGenerationError("Selected scene media must be an image for image-to-video generation")
        if not asset.local_path:
            raise MotionGenerationError("Selected image is not stored locally")
        path = project_store.media_file(project.id, Path(asset.local_path).name)
        if path is None or not path.is_file():
            raise MotionGenerationError("Selected image file was not found")
        return path.resolve()

    @staticmethod
    def _prompt(project: Project, scene: Scene) -> str:
        visual = (scene.visual_prompt_en or scene.visual_prompt).strip()
        action = scene.action.strip()
        parts = [
            visual,
            f"Animate the visible action naturally: {action}" if action else "",
            "Preserve the character identity, clothing, proportions, colors, environment and composition from the source image.",
            "Natural cinematic motion, stable anatomy, coherent background motion, no captions, no text, no watermark.",
        ]
        return "\n".join(part for part in parts if part)

    async def generate(self, project: Project, scene: Scene, *, duration_seconds: float | None = None) -> MediaAsset:
        if not self.enabled:
            raise MotionGenerationError(self.status()["note"])

        source = self._source_image(project, scene)
        requested = duration_seconds or settings.motion_default_duration_seconds
        duration = max(2.0, min(float(requested), float(settings.motion_max_duration_seconds)))
        width, height = self._dimensions(project.request.aspect_ratio)

        token = uuid4().hex[:8]
        filename = f"{scene.id}-motion-{token}.mp4"
        output = (project_store.media_dir(project.id) / filename).resolve()
        prompt = self._prompt(project, scene)
        started = time.monotonic()
        self._runtime = {
            "state": "running",
            "stage": "queued",
            "detail": "Waiting for exclusive GPU access.",
            "project_id": project.id,
            "scene_id": scene.id,
            "pid": None,
            "started_monotonic": started,
            "elapsed_seconds": 0.0,
            "output": filename,
            "error": None,
        }

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            handle.write(prompt)
            prompt_path = Path(handle.name).resolve()

        try:
            formatted = settings.motion_command.format(
                input=str(source),
                output=str(output),
                prompt_file=str(prompt_path),
                duration=f"{duration:.3f}",
                width=str(width),
                height=str(height),
            )
            cmd = shlex.split(formatted)
            if not cmd:
                raise MotionGenerationError("MOTION_COMMAND is empty after formatting")

            self._set_runtime(stage="gpu_prepare", detail="Stopping llama-server and freeing VRAM.")
            async with model_orchestrator.local_motion_slot():
                self._set_runtime(
                    stage="model_start",
                    detail=f"Starting image-to-video runner ({width}x{height}, requested {duration:.1f}s).",
                )
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self._set_runtime(
                    stage="generating",
                    detail="Motion model is loading/generating frames. First run may also download model files.",
                    pid=process.pid,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(), timeout=settings.motion_timeout_seconds
                    )
                except TimeoutError as exc:
                    process.kill()
                    await process.wait()
                    raise MotionGenerationError("Motion generation timed out") from exc

                if process.returncode != 0:
                    out = stdout.decode("utf-8", errors="replace")[-1500:]
                    err = stderr.decode("utf-8", errors="replace")[-3000:]
                    raise MotionGenerationError(
                        f"Motion provider exited {process.returncode}.\n{err or out}"
                    )
                if not output.is_file() or output.stat().st_size == 0:
                    raise MotionGenerationError("Motion provider finished without creating the MP4 output")

                self._set_runtime(stage="finalizing", detail="MP4 created. Probing duration and preparing scene asset.")

            self._set_runtime(stage="llm_restored", detail="GPU job finished; llama-server has been restored.")
        except Exception as exc:
            self._set_runtime(
                state="error",
                stage="error",
                detail="Motion generation failed.",
                error=str(exc),
                pid=None,
            )
            raise
        finally:
            prompt_path.unlink(missing_ok=True)

        actual_duration = probe_duration(output) or duration
        local_url = f"/api/projects/{project.id}/media/{filename}"
        asset = MediaAsset(
            provider="local_motion",
            asset_id=filename,
            media_type="video",
            preview_url=local_url,
            source_url=f"local-motion://{filename}",
            download_url=local_url,
            width=width,
            height=height,
            duration_seconds=actual_duration,
            author="local",
            label=f"AI motion · {scene.id} · {actual_duration:.1f}s",
            local_path=f"media/{filename}",
        )
        self._set_runtime(
            state="complete",
            stage="complete",
            detail=f"Motion clip ready: {filename}",
            output=filename,
            pid=None,
            error=None,
        )
        return asset


motion_service = MotionService()
