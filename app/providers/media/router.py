import asyncio

from app.providers.media.pexels import PexelsProvider
from app.providers.media.pixabay import PixabayProvider
from app.schemas import MediaAsset


class MediaRouter:
    def __init__(self) -> None:
        self.providers = [PexelsProvider(), PixabayProvider()]

    def status(self) -> dict[str, bool]:
        return {provider.name: provider.enabled for provider in self.providers}

    async def search(self, query: str, limit_per_provider: int = 4) -> list[MediaAsset]:
        enabled = [provider for provider in self.providers if provider.enabled]
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
