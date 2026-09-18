import asyncio

from app.providers.media.pexels import PexelsProvider
from app.providers.media.pixabay import PixabayProvider
from app.providers.media.qwen_image import QwenImageProvider
from app.schemas import MediaAsset


class MediaRouter:
    def __init__(self) -> None:
        self.search_providers = [PexelsProvider(), PixabayProvider()]
        self.qwen_image = QwenImageProvider()

    def status(self) -> dict[str, bool]:
        result = {provider.name: provider.enabled for provider in self.search_providers}
        result[self.qwen_image.name] = self.qwen_image.enabled
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


media_router = MediaRouter()
