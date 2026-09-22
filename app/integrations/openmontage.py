from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from app.config import settings
from app.schemas import Project
from app.services.production import sync_project
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
            source_asset = scene.selected_media
            source_path: Path | None = None
            source_type = "image"

            if scene.motion_mode == "image_to_video" and scene.selected_motion_media is not None:
                candidate = scene.selected_motion_media
                candidate_path = self._resolve_media_path(project, candidate.local_path, "media")
                if candidate.media_type == "video" and candidate_path is not None and candidate_path.is_file():
                    source_asset = candidate
                    source_path = candidate_path
                    source_type = "video"

            if source_path is None:
                if source_asset is None:
                    raise ValueError(f"{scene.id} has no selected image or motion clip")
                source_path = self._resolve_media_path(project, source_asset.local_path, "media")
                if source_path is None or not source_path.is_file():
                    raise ValueError(f"{scene.id} selected media is not a local file")
                source_type = source_asset.media_type

            duration = max(float(scene.final_duration_seconds or scene.duration_seconds), 0.5)
            end = cursor + duration
            visual_id = f"visual-{scene.id}"
            assets.append({
                "id": visual_id,
                "type": source_type,
                "path": str(source_path.resolve()),
                "source": source_asset.source_url if source_asset is not None else "",
            })
            cut = {
                "id": scene.id,
                "source": visual_id,
                "in_seconds": cursor,
                "out_seconds": end,
                "type": source_type,
                "animation": "static" if scene.motion_mode == "static" else animations[index % len(animations)],
                "title": scene.title,
                "reason": scene.action,
                "transition_in": "crossfade" if index else "none",
                "transition_out": "crossfade" if index < len(project.storyboard.scenes) - 1 else "none",
            }
            if scene.subtitle_enabled and scene.subtitle_text.strip():
                cut["subtitle"] = scene.subtitle_text.strip()
            cuts.append(cut)

            if scene.selected_audio is not None:
                audio_path = self._resolve_media_path(project, scene.selected_audio.local_path, "audio")
                if audio_path is not None and audio_path.is_file():
                    audio_id = f"audio-{scene.id}"
                    assets.append({
                        "id": audio_id,
                        "type": "audio",
                        "path": str(audio_path.resolve()),
                    })
                    audio_length = float(scene.audio_duration_seconds or duration)
                    narration_segments.append({
                        "asset_id": audio_id,
                        "start_seconds": cursor,
                        "end_seconds": min(end, cursor + max(audio_length, 0.1)),
                    })
            cursor = end

        project_slug = f"videogen-{project.id}"
        om_project_dir = (settings.openmontage_root / "projects" / project_slug).resolve()
        workspace = om_project_dir / "hyperframes"
        output_path = (project_store.render_dir(project.id) / f"final-{runtime}.mp4").resolve()

        edit_decisions = {
            "render_runtime": runtime,
            "renderer_family": "animation-first",
            "composition_mode": "templated",
            "cuts": cuts,
            "audio": {"narration": {"segments": narration_segments}, "music": {}},
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

    @staticmethod
    def _parse_helper_json(text: str) -> dict | None:
        if not text:
            return None
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

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
        err = stderr.decode("utf-8", errors="replace").strip()
        parsed = self._parse_helper_json(text)

        if process.returncode != 0:
            if parsed is not None:
                detail = str(parsed.get("error") or f"OpenMontage helper exited {process.returncode}")
                data = parsed.get("data") or {}
                if data:
                    detail += "\nOpenMontage data: " + json.dumps(data, ensure_ascii=False)[:12000]
                if err:
                    detail += "\nOpenMontage log: " + err[-3000:]
                raise RuntimeError(detail)
            raise RuntimeError(err or text or f"OpenMontage helper exited {process.returncode}")

        if parsed is not None:
            return parsed
        if not text:
            raise RuntimeError("OpenMontage helper returned empty output")
        raise RuntimeError(f"OpenMontage helper returned invalid JSON: {text[-1500:]}")

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

    async def preview(self, project: Project) -> dict:
        sync_project(project, save=True)
        job = self.build_job(project, "hyperframes")
        # Stable per-project preview port in the 3200-4199 range.
        job["preview_port"] = 3200 + (int(project.id[:6], 16) % 1000)
        runner = Path("./scripts/openmontage_preview.py").resolve()
        if not runner.is_file():
            raise RuntimeError(f"OpenMontage preview runner not found: {runner}")

        env = os.environ.copy()
        env["OPENMONTAGE_ROOT"] = str(settings.openmontage_root.resolve())
        process = await asyncio.create_subprocess_exec(
            str(self._python()),
            str(runner),
            json.dumps(job, ensure_ascii=False),
            cwd=str(Path.cwd()),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
        text = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        payload = self._parse_helper_json(text) or {}
        if process.returncode != 0 or not payload.get("success"):
            raise RuntimeError(payload.get("error") or err or text or "OpenMontage Studio failed to start")
        return payload

    async def render(self, project: Project, runtime: str) -> dict:
        sync_project(project, save=True)
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
            "download_url": f"/api/openmontage/projects/{project.id}/renders/{filename}",
            "filename": filename,
        }


openmontage = OpenMontageIntegration()
