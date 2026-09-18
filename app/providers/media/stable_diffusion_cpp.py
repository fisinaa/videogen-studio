from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.schemas import MediaAsset


class StableDiffusionCppProvider:
    name = "local_image"

    @property
    def enabled(self) -> bool:
        required = [
            settings.sd_cpp_bin,
            settings.sd_cpp_diffusion_model,
            settings.sd_cpp_vae,
            settings.sd_cpp_clip_l,
            settings.sd_cpp_t5xxl,
        ]
        return all(path.is_file() for path in required)

    def _dimensions(self, aspect_ratio: str) -> tuple[int, int]:
        return {
            "16:9": (1024, 576),
            "9:16": (576, 1024),
            "1:1": (768, 768),
        }.get(aspect_ratio, (1024, 576))

    async def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        project_id: str,
        scene_id: str,
        media_dir: Path,
        reference_path: Path | None = None,
    ) -> MediaAsset:
        if not self.enabled:
            raise RuntimeError(
                "stable-diffusion.cpp is not configured. Set SD_CPP_BIN, "
                "SD_CPP_DIFFUSION_MODEL, SD_CPP_VAE, SD_CPP_CLIP_L and SD_CPP_T5XXL."
            )

        width, height = self._dimensions(aspect_ratio)
        media_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{scene_id}-local-{uuid4().hex[:8]}.png"
        output_path = media_dir / filename

        cmd = [
            str(settings.sd_cpp_bin),
            "--diffusion-model",
            str(settings.sd_cpp_diffusion_model),
            "--vae",
            str(settings.sd_cpp_vae),
            "--clip_l",
            str(settings.sd_cpp_clip_l),
            "--t5xxl",
            str(settings.sd_cpp_t5xxl),
            "-p",
            prompt,
            "--cfg-scale",
            str(settings.sd_cpp_cfg_scale),
            "--sampling-method",
            "euler",
            "--steps",
            str(settings.sd_cpp_steps),
            "-W",
            str(width),
            "-H",
            str(height),
            "-o",
            str(output_path),
        ]
        if settings.sd_cpp_clip_on_cpu:
            cmd.append("--clip-on-cpu")
        if settings.sd_cpp_offload_to_cpu:
            cmd.append("--offload-to-cpu")
        if reference_path is not None and reference_path.is_file():
            cmd.extend(["-i", str(reference_path), "--strength", "0.45"])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.sd_cpp_timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeError("Local image generation timed out") from exc

        if process.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace")[-3000:]
            raise RuntimeError(
                f"stable-diffusion.cpp exited with code {process.returncode}: {detail}"
            )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("stable-diffusion.cpp did not create an image")

        local_url = f"/api/projects/{project_id}/media/{filename}"
        return MediaAsset(
            provider=self.name,
            asset_id=filename,
            media_type="image",
            preview_url=local_url,
            source_url=f"local://stable-diffusion.cpp/{settings.sd_cpp_diffusion_model.name}",
            download_url=local_url,
            width=width,
            height=height,
            duration_seconds=None,
            author="local",
            label=f"stable-diffusion.cpp · {scene_id}",
            local_path=f"media/{filename}",
        )


stable_diffusion_cpp_provider = StableDiffusionCppProvider()
