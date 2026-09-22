import asyncio
from pathlib import Path

from app.config import settings
from app.providers.media.openai_image import OpenAIImageProvider
from app.providers.media.pexels import PexelsProvider
from app.providers.media.pixabay import PixabayProvider
from app.providers.media.stable_diffusion_cpp import StableDiffusionCppProvider
from app.schemas import MediaAsset


class MediaRouter:
    def __init__(self) -> None:
        self.search_providers = [PexelsProvider(), PixabayProvider()]
        self.openai_image = OpenAIImageProvider()
        self.local_image = StableDiffusionCppProvider()

    def status(self) -> dict:
        result = {provider.name: provider.enabled for provider in self.search_providers}
        result[self.openai_image.name] = self.openai_image.enabled
        result[self.local_image.name] = self.local_image.enabled
        result["local_fast"] = self.local_image.enabled
        result["local_quality"] = self.local_image.quality_enabled
        result["local_next"] = self.local_image.next_enabled
        result["image_selected"] = settings.image_provider
        return result

    def search_enabled(self) -> bool:
        return any(provider.enabled for provider in self.search_providers)

    async def search(self, query: str, limit_per_provider: int = 4) -> list[MediaAsset]:
        enabled = [provider for provider in self.search_providers if provider.enabled]
        if not enabled:
            return []
        batches = await asyncio.gather(
            *(provider.search(query, limit=limit_per_provider) for provider in enabled),
            return_exceptions=True,
        )
        assets: list[MediaAsset] = []
        for batch in batches:
            if isinstance(batch, Exception):
                continue
            assets.extend(batch)
        return assets

    async def generate_image(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        project_id: str,
        scene_id: str,
        media_dir: Path,
        reference_path: Path | None = None,
        reference_paths: list[Path] | None = None,
        provider: str | None = None,
    ) -> MediaAsset:
        mode = (provider or settings.image_provider).strip().lower()
        refs: list[Path] = []
        for path in ([reference_path] if reference_path is not None else []) + list(reference_paths or []):
            if path is not None and path not in refs:
                refs.append(path)
        primary_ref = refs[0] if refs else None

        if mode in {"local", "local_fast"}:
            return await self.local_image.generate(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                project_id=project_id,
                scene_id=scene_id,
                media_dir=media_dir,
                reference_path=primary_ref,
                profile="fast",
            )
        if mode == "local_quality":
            return await self.local_image.generate(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                project_id=project_id,
                scene_id=scene_id,
                media_dir=media_dir,
                reference_path=primary_ref,
                profile="quality",
            )
        if mode == "local_next":
            return await self.local_image.generate(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                project_id=project_id,
                scene_id=scene_id,
                media_dir=media_dir,
                reference_path=primary_ref,
                profile="next",
            )
        if mode == "openai":
            return await self.openai_image.generate(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                project_id=project_id,
                scene_id=scene_id,
                media_dir=media_dir,
                reference_paths=refs,
            )
        if mode != "auto":
            raise ValueError(f"Unknown image provider mode: {mode}")

        errors: list[str] = []
        for name, enabled, fn in (
            (
                "local_next",
                self.local_image.next_enabled,
                lambda: self.local_image.generate(
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    project_id=project_id,
                    scene_id=scene_id,
                    media_dir=media_dir,
                    reference_path=primary_ref,
                    profile="next",
                ),
            ),
            (
                "local_quality",
                self.local_image.quality_enabled,
                lambda: self.local_image.generate(
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    project_id=project_id,
                    scene_id=scene_id,
                    media_dir=media_dir,
                    reference_path=primary_ref,
                    profile="quality",
                ),
            ),
            (
                "local_fast",
                self.local_image.enabled,
                lambda: self.local_image.generate(
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    project_id=project_id,
                    scene_id=scene_id,
                    media_dir=media_dir,
                    reference_path=primary_ref,
                    profile="fast",
                ),
            ),
            (
                "openai",
                self.openai_image.enabled,
                lambda: self.openai_image.generate(
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    project_id=project_id,
                    scene_id=scene_id,
                    media_dir=media_dir,
                    reference_paths=refs,
                ),
            ),
        ):
            try:
                if not enabled:
                    raise RuntimeError("disabled")
                return await fn()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        raise RuntimeError("No image provider succeeded: " + "; ".join(errors))


media_router = MediaRouter()
