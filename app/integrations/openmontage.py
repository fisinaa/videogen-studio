from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from app.config import settings
from app.schemas import Project
from app.storage import project_store


class OpenMontageIntegration:
    def _python(self) -> Path:
        configured = settings.openmontage_python
        if configured and configured.is_file():
            return configured
        fallback = settings.openmontage_root / ".venv" / "bin" / "python"
        return fallback

    @property
    def enabled(self) -> bool:
        return settings.openmontage_root.is_dir() and self._python().is_file()

    def _profile(self, aspect_ratio: str) -> str:
        return {
            "16:9": "youtube_landscape",
            "9:16": "tiktok",
            "1:1": "instagram_feed",
        }.get(aspect_ratio, "generic_hd")

    def _resolve_media_path(self, project: Project, local_path: str | None, kind: str) -> Path | None:
        if not local_path:
            return None
        name = Path(local_path).name
        if kind == "media":
            return project_store.media_file(project.id, name)
        return project_store.audio_file(project.id, name)

    def build_job(self, project: Project, runtime: str) -> dict:
        runtime = runtime.strip().lower()
        if runtime not in {"hyperframes", "remotion"}:
            raise ValueError("render runtime must be hyperframes or remotion")

        cuts: list[dict] = []
        assets: list[dict] = []
        narration_segments: list[dict] = []
        cursor = 0.0
        animations = ["ken-burns", "pan-left", "pan-right", "zoom-in", "drift-up"]

        for index, scene in enumerate(project.storyboard.scenes):
            if scene.selected_media is None:
                raise ValueError(f"{scene.id} has no selected image")
            image_path = self._resolve_media_path(project, scene.selected_media.local_path, "media")
            if image_path is None or not image_path.is_file():
                raise ValueError(f"{scene.id} selected image is not a local file")

            duration = max(float(scene.duration_seconds), 0.5)
            end = cursor + duration
            image_id = f"image-{scene.id}"
            assets.append({
                "id": image_id,
                "type": "image",
                "path": str(image_path.resolve()),
                "source": scene.selected_media.source_url,
            })
            cuts.append({
                "id": scene.id,
                "source": image_id,
                "in_seconds": cursor,
                "out_seconds": end,
                "type": "image",
                "animation": animations[index % len(animations)],
                "title": scene.title,
                "reason": scene.action,
                "transition_in": "crossfade" if index else "none",
                "transition_out": "crossfade" if index < len(project.storyboard.scenes) - 1 else "none",
            })

            if scene.selected_audio is not None:
                audio_path = self._resolve_media_path(project, scene.selected_audio.local_path, "audio")
                if audio_path is not None and audio_path.is_file():
                    audio_id = f"audio-{scene.id}"
                    assets.append({
                        "id": audio_id,
                        "type": "audio",
                        "path": str(audio_path.resolve()),
                    })
                    narration_segments.append({
                        "asset_id": audio_id,
                        "start_seconds": cursor,
                        "end_seconds": end,
                    })
            cursor = end

        project_slug = f"videogen-{project.id}"
        om_project_dir = (settings.openmontage_root / "projects" / project_slug).resolve()
        workspace = om_project_dir / "hyperframes"
        output_path = project_store.render_dir(project.id) / f"final-{runtime}.mp4"
        output_path = output_path.resolve()

        edit_decisions = {
            "render_runtime": runtime,
            "renderer_family": "animation-first",
            "composition_mode": "templated",
            "cuts": cuts,
            "audio": {"narration": {"segments": narration_segments}, "music": None},
            "metadata": {
                "title": project.storyboard.title,
                "visual_style": project.storyboard.visual_style,
                "project_id": project.id,
                "proposal_render_runtime": runtime,
            },
        }
        asset_manifest = {"assets": assets}
        proposal_packet = {
            "production_plan": {
                "render_runtime": runtime,
                "composition_mode": "templated",
                "renderer_family": "animation-first",
            }
        }

        return {
            "project_id": project.id,
            "project_slug": project_slug,
            "runtime": runtime,
            "openmontage_root": str(settings.openmontage_root.resolve()),
            "workspace_path": str(workspace),
            "output_path": str(output_path),
            "profile": self._profile(project.request.aspect_ratio),
            "fps": 30,
            "edit_decisions": edit_decisions,
            "asset_manifest": asset_manifest,
            "proposal_packet": proposal_packet,
            "script_text": "\n".join(
                part for scene in project.storyboard.scenes
                for part in ([scene.narration] if scene.narration.strip() else scene.dialogue)
                if part.strip()
            ),
        }

    async def _run_helper(self, action: str, payload: dict | None = None) -> dict:
        if not self.enabled:
            raise RuntimeError(
                f"OpenMontage is not available. Expected root={settings.openmontage_root} "
                f"and python={self._python()}"
            )
        env = os.environ.copy()
        env["OPENMONTAGE_ROOT"] = str(settings.openmontage_root.resolve())
        cmd = [
            str(self._python()),
            str(settings.videogen_openmontage_runner.resolve()),
            action,
        ]
        if payload is not None:
            cmd.append(json.dumps(payload, ensure_ascii=False))

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(settings.openmontage_root.resolve()),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=settings.openmontage_timeout_seconds
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeError("OpenMontage render timed out") from exc

        text = stdout.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(err or text or f"OpenMontage helper exited {process.returncode}")
        if not text:
            raise RuntimeError("OpenMontage helper returned empty output")
        try:
            return json.loads(text.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenMontage helper returned invalid JSON: {text[-1500:]}") from exc

    async def status(self) -> dict:
        base = {
            "enabled": self.enabled,
            "root": str(settings.openmontage_root),
            "python": str(self._python()),
        }
        if not self.enabled:
            base["render_engines"] = {"hyperframes": False, "remotion": False, "ffmpeg": False}
            return base
        try:
            result = await self._run_helper("status")
            base.update(result)
        except Exception as exc:
            base["error"] = str(exc)
            base["render_engines"] = {"hyperframes": False, "remotion": False, "ffmpeg": False}
        return base

    async def render(self, project: Project, runtime: str) -> dict:
        job = self.build_job(project, runtime)
        result = await self._run_helper("render", job)
        output = Path(result.get("output") or job["output_path"])
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"OpenMontage did not create output video: {output}")
        filename = output.name
        return {
            **result,
            "runtime": runtime,
            "output": str(output),
            "download_url": f"/api/projects/{project.id}/renders/{filename}",
            "filename": filename,
        }


openmontage = OpenMontageIntegration()
