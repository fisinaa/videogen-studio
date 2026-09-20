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

    def _image_providers(self, provider: str | None = None):
        mode = (provider or settings.image_provider).strip().lower()
        if mode == "local":
            return [self.local_image]
        if mode == "openai":
            return [self.openai_image]
        if mode != "auto":
            raise ValueError(f"Unknown image provider mode: {mode}")
        return [self.local_image, self.openai_image]

    async def generate_image(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        project_id: str,
        scene_id: str,
        media_dir: Path,
        reference_path: Path | None = None,
        provider: str | None = None,
    ) -> MediaAsset:
        mode = (provider or settings.image_provider).strip().lower()
        errors: list[str] = []
        for image_provider in self._image_providers(mode):
            if not image_provider.enabled:
                errors.append(f"{image_provider.name}: disabled")
                continue
            try:
                return await image_provider.generate(
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    project_id=project_id,
                    scene_id=scene_id,
                    media_dir=media_dir,
                    reference_path=reference_path,
                )
            except Exception as exc:
                errors.append(f"{image_provider.name}: {exc}")
                if mode != "auto":
                    raise
        raise RuntimeError("No image provider succeeded: " + "; ".join(errors))


media_router = MediaRouter()
