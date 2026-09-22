from __future__ import annotations

import asyncio
import json
import os
import shlex
import tempfile
import time
from contextlib import asynccontextmanager
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
    """Generate scene motion through OpenMontage or a legacy custom command."""

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
            "selected_provider": None,
            "selected_tool": None,
        }

    @property
    def provider(self) -> str:
        return settings.motion_provider.strip().lower()

    @property
    def openmontage_ready(self) -> bool:
        return (
            settings.openmontage_root.is_dir()
            and settings.openmontage_python.is_file()
            and settings.motion_openmontage_runner.is_file()
        )

    @property
    def enabled(self) -> bool:
        if self.provider == "openmontage":
            return self.openmontage_ready
        if self.provider == "command":
            return bool(settings.motion_command.strip())
        return False

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
        if self.provider == "openmontage":
            note = (
                "OpenMontage VideoSelector is configured. It will use an available image-to-video provider."
                if self.enabled
                else "OpenMontage motion bridge is not ready. Check OPENMONTAGE_ROOT, OPENMONTAGE_PYTHON and scripts/openmontage_motion.py."
            )
        elif self.provider == "command":
            note = (
                "Legacy custom image-to-video command provider is ready."
                if self.enabled
                else "MOTION_PROVIDER=command requires MOTION_COMMAND."
            )
        else:
            note = "AI motion is disabled. Set MOTION_PROVIDER=openmontage."

        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "command_configured": bool(settings.motion_command.strip()),
            "openmontage_ready": self.openmontage_ready,
            "openmontage_preferred_provider": settings.motion_openmontage_preferred_provider,
            "default_duration_seconds": settings.motion_default_duration_seconds,
            "max_duration_seconds": settings.motion_max_duration_seconds,
            "gpu_orchestration": model_orchestrator.enabled,
            "runtime": self.runtime_status(),
            "note": note,
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

    @staticmethod
    def _openmontage_env() -> dict[str, str]:
        env = os.environ.copy()
        env["OPENMONTAGE_ROOT"] = str(settings.openmontage_root.resolve())
        # Expose VideoGen-owned cloud credentials to the OpenMontage subprocess.
        # This avoids duplicating secrets in the OpenMontage checkout.
        if settings.openai_api_key.strip():
            env["OPENAI_API_KEY"] = settings.openai_api_key.strip()
        if settings.fal_key.strip():
            env["FAL_KEY"] = settings.fal_key.strip()
        return env

    @asynccontextmanager
    async def _maybe_gpu_slot(self, reserve: bool):
        if reserve:
            async with model_orchestrator.local_motion_slot():
                yield
        else:
            yield

    async def _run_openmontage(
        self,
        project: Project,
        source: Path,
        output: Path,
        prompt: str,
        duration: float,
    ) -> dict:
        # OpenMontage providers often use native clip durations. For the current
        # short-scene path use the closest practical whole-second request.
        requested = max(2, int(round(duration)))
        job = {
            "prompt": prompt,
            "reference_image_path": str(source),
            "output_path": str(output),
            "aspect_ratio": project.request.aspect_ratio,
            "duration": requested,
            "preferred_provider": settings.motion_openmontage_preferred_provider,
        }
        # Do not Path.resolve() the venv python executable. The venv entry point is
        # commonly a symlink to /usr/bin/python; resolving it bypasses pyvenv.cfg
        # discovery and silently launches the system interpreter without venv deps.
        openmontage_python = settings.openmontage_python.expanduser()
        cmd = [
            str(openmontage_python),
            str(settings.motion_openmontage_runner.resolve()),
            "generate",
            json.dumps(job, ensure_ascii=False),
        ]

        self._set_runtime(
            stage="provider_select",
            detail="OpenMontage VideoSelector is choosing an available image-to-video provider.",
        )
        async with self._maybe_gpu_slot(settings.motion_openmontage_reserve_gpu):
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._openmontage_env(),
            )
            self._set_runtime(
                stage="generating",
                detail="OpenMontage image-to-video provider is generating the scene clip.",
                pid=process.pid,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=settings.motion_timeout_seconds
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise MotionGenerationError("OpenMontage motion generation timed out") from exc

        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        payload: dict = {}
        if out_text:
            try:
                payload = json.loads(out_text.splitlines()[-1])
            except json.JSONDecodeError:
                payload = {}

        if process.returncode != 0 or not payload.get("success"):
            detail = payload.get("error") or err_text[-4000:] or out_text[-4000:] or f"OpenMontage exited {process.returncode}"
            raise MotionGenerationError(f"OpenMontage motion failed: {detail}")
        if not output.is_file() or output.stat().st_size == 0:
            raise MotionGenerationError("OpenMontage finished without creating the MP4 output")
        return payload

    async def _run_command(
        self,
        source: Path,
        output: Path,
        prompt: str,
        duration: float,
        width: int,
        height: int,
    ) -> dict:
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

            self._set_runtime(stage="gpu_prepare", detail="Stopping llama-server and freeing VRAM for legacy motion runner.")
            async with model_orchestrator.local_motion_slot():
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self._set_runtime(
                    stage="generating",
                    detail=f"Legacy image-to-video runner is generating {width}x{height} frames.",
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
                    raise MotionGenerationError(f"Motion provider exited {process.returncode}.\n{err or out}")
                if not output.is_file() or output.stat().st_size == 0:
                    raise MotionGenerationError("Motion provider finished without creating the MP4 output")
            return {"selected_provider": "legacy-command", "selected_tool": "MOTION_COMMAND"}
        finally:
            prompt_path.unlink(missing_ok=True)

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
            "detail": "Preparing image-to-video generation.",
            "project_id": project.id,
            "scene_id": scene.id,
            "pid": None,
            "started_monotonic": started,
            "elapsed_seconds": 0.0,
            "output": filename,
            "error": None,
            "selected_provider": None,
            "selected_tool": None,
        }

        try:
            if self.provider == "openmontage":
                payload = await self._run_openmontage(project, source, output, prompt, duration)
            elif self.provider == "command":
                payload = await self._run_command(source, output, prompt, duration, width, height)
            else:
                raise MotionGenerationError(f"Unsupported motion provider: {self.provider}")

            selected_provider = payload.get("selected_provider") or (payload.get("data") or {}).get("selected_provider")
            selected_tool = payload.get("selected_tool") or (payload.get("data") or {}).get("selected_tool")
            self._set_runtime(
                stage="finalizing",
                detail="MP4 created. Probing duration and preparing scene asset.",
                selected_provider=selected_provider,
                selected_tool=selected_tool,
                pid=None,
            )
        except Exception as exc:
            self._set_runtime(
                state="error",
                stage="error",
                detail="Motion generation failed.",
                error=str(exc),
                pid=None,
            )
            raise

        actual_duration = probe_duration(output) or duration
        local_url = f"/api/projects/{project.id}/media/{filename}"
        selected_provider = self._runtime.get("selected_provider") or self.provider
        asset = MediaAsset(
            provider=f"openmontage:{selected_provider}" if self.provider == "openmontage" else "local_motion",
            asset_id=filename,
            media_type="video",
            preview_url=local_url,
            source_url=f"motion://{selected_provider}/{filename}",
            download_url=local_url,
            width=width,
            height=height,
            duration_seconds=actual_duration,
            author=str(selected_provider),
            label=f"AI motion · {scene.id} · {actual_duration:.1f}s · {selected_provider}",
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
