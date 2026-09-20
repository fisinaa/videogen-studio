from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.schemas import MediaAsset


class StableDiffusionCppProvider:
    name = "local_image"

    def _shared_file(self, preferred: Path, fallback: Path) -> Path:
        return preferred if preferred.is_file() else fallback

    def profile_enabled(self, profile: str) -> bool:
        profile = profile.strip().lower()
        if profile == "quality":
            model = settings.sd_cpp_quality_diffusion_model
            vae = self._shared_file(settings.sd_cpp_quality_vae, settings.sd_cpp_vae)
            llm = settings.sd_cpp_quality_llm
            clip_l = self._shared_file(settings.sd_cpp_quality_clip_l, settings.sd_cpp_clip_l)
            t5xxl = self._shared_file(settings.sd_cpp_quality_t5xxl, settings.sd_cpp_t5xxl)
            text_encoder_ok = llm.is_file() or (clip_l.is_file() and t5xxl.is_file())
            return settings.sd_cpp_bin.is_file() and model.is_file() and vae.is_file() and text_encoder_ok
        required = [
            settings.sd_cpp_bin,
            settings.sd_cpp_diffusion_model,
            settings.sd_cpp_vae,
            settings.sd_cpp_clip_l,
            settings.sd_cpp_t5xxl,
        ]
        return all(path.is_file() for path in required)

    @property
    def enabled(self) -> bool:
        return self.profile_enabled("fast")

    @property
    def quality_enabled(self) -> bool:
        return self.profile_enabled("quality")

    def _profile(self, profile: str):
        profile = profile.strip().lower()
        if profile == "quality":
            if not self.quality_enabled:
                raise RuntimeError(
                    "Local quality image profile is not configured. Set "
                    "SD_CPP_QUALITY_DIFFUSION_MODEL plus VAE and either "
                    "SD_CPP_QUALITY_LLM or CLIP/T5 encoders."
                )
            llm = settings.sd_cpp_quality_llm if settings.sd_cpp_quality_llm.is_file() else None
            return {
                "name": "quality",
                "model": settings.sd_cpp_quality_diffusion_model,
                "vae": self._shared_file(settings.sd_cpp_quality_vae, settings.sd_cpp_vae),
                "clip_l": self._shared_file(settings.sd_cpp_quality_clip_l, settings.sd_cpp_clip_l),
                "t5xxl": self._shared_file(settings.sd_cpp_quality_t5xxl, settings.sd_cpp_t5xxl),
                "llm": llm,
                "steps": settings.sd_cpp_quality_steps,
                "cfg_scale": settings.sd_cpp_quality_cfg_scale,
                "sampling_method": settings.sd_cpp_quality_sampling_method,
            }
        if not self.enabled:
            raise RuntimeError(
                "Local fast image profile is not configured. Set SD_CPP_BIN, "
                "SD_CPP_DIFFUSION_MODEL, SD_CPP_VAE, SD_CPP_CLIP_L and SD_CPP_T5XXL."
            )
        return {
            "name": "fast",
            "model": settings.sd_cpp_diffusion_model,
            "vae": settings.sd_cpp_vae,
            "clip_l": settings.sd_cpp_clip_l,
            "t5xxl": settings.sd_cpp_t5xxl,
            "llm": None,
            "steps": settings.sd_cpp_steps,
            "cfg_scale": settings.sd_cpp_cfg_scale,
            "sampling_method": settings.sd_cpp_sampling_method,
        }

    def _dimensions(self, aspect_ratio: str) -> tuple[int, int]:
        return {
            "16:9": (settings.sd_cpp_width_16_9, settings.sd_cpp_height_16_9),
            "9:16": (settings.sd_cpp_width_9_16, settings.sd_cpp_height_9_16),
            "1:1": (settings.sd_cpp_width_1_1, settings.sd_cpp_height_1_1),
        }.get(aspect_ratio, (settings.sd_cpp_width_16_9, settings.sd_cpp_height_16_9))

    async def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        project_id: str,
        scene_id: str,
        media_dir: Path,
        reference_path: Path | None = None,
        profile: str = "fast",
    ) -> MediaAsset:
        p = self._profile(profile)
        width, height = self._dimensions(aspect_ratio)
        media_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{scene_id}-local-{p['name']}-{uuid4().hex[:8]}.png"
        output_path = media_dir / filename

        cmd = [
            str(settings.sd_cpp_bin),
            "--diffusion-model", str(p["model"]),
            "--vae", str(p["vae"]),
        ]
        if p["llm"] is not None:
            cmd.extend(["--llm", str(p["llm"])])
        else:
            cmd.extend(["--clip_l", str(p["clip_l"]), "--t5xxl", str(p["t5xxl"])])

        cmd.extend([
            "-p", prompt,
            "-W", str(width),
            "-H", str(height),
            "--steps", str(p["steps"]),
            "--cfg-scale", str(p["cfg_scale"]),
            "--sampling-method", str(p["sampling_method"]),
        ])

        backend = settings.sd_cpp_backend.strip()
        if backend:
            cmd.extend(["--backend", backend])
        elif settings.sd_cpp_clip_on_cpu:
            cmd.append("--clip-on-cpu")
        if settings.sd_cpp_offload_to_cpu:
            cmd.append("--offload-to-cpu")
        max_vram = settings.sd_cpp_max_vram.strip()
        if max_vram:
            cmd.extend(["--max-vram", max_vram])
        if settings.sd_cpp_diffusion_fa:
            cmd.append("--diffusion-fa")
        if settings.sd_cpp_threads > 0:
            cmd.extend(["-t", str(settings.sd_cpp_threads)])
        if reference_path is not None and reference_path.is_file():
            cmd.extend(["-i", str(reference_path), "--strength", "0.45"])
        cmd.extend(["-o", str(output_path)])
        if settings.sd_cpp_verbose:
            cmd.append("-v")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=settings.sd_cpp_timeout_seconds
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeError("Local image generation timed out") from exc

        if process.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace")[-5000:]
            raise RuntimeError(
                f"stable-diffusion.cpp exited with code {process.returncode}: {detail}"
            )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("stable-diffusion.cpp did not create an image")

        local_url = f"/api/projects/{project_id}/media/{filename}"
        mode = "img2img reference" if reference_path is not None else "text-to-image"
        encoder = p["llm"].name if p["llm"] is not None else "clip_l+t5xxl"
        return MediaAsset(
            provider=self.name,
            asset_id=filename,
            media_type="image",
            preview_url=local_url,
            source_url=f"local://stable-diffusion.cpp/{p['model'].name}",
            download_url=local_url,
            width=width,
            height=height,
            duration_seconds=None,
            author="local",
            label=(
                f"stable-diffusion.cpp · {p['name']} · {p['model'].name} · "
                f"encoder={encoder} · {width}x{height} · {mode} · {scene_id}"
            ),
            local_path=f"media/{filename}",
        )


stable_diffusion_cpp_provider = StableDiffusionCppProvider()
