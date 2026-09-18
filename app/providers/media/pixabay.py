import httpx

from app.config import settings
from app.providers.media.base import MediaProvider
from app.schemas import MediaAsset


class PixabayProvider(MediaProvider):
    name = "pixabay"

    @property
    def enabled(self) -> bool:
        return bool(settings.pixabay_api_key.strip())

    async def search(self, query: str, limit: int = 6) -> list[MediaAsset]:
        if not self.enabled or not query.strip():
            return []

        params = {
            "key": settings.pixabay_api_key.strip(),
            "q": query.strip(),
            "per_page": min(max(limit, 3), 20),
            "safesearch": "true",
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://pixabay.com/api/videos/",
                params=params,
            )
            response.raise_for_status()
            data = response.json()

        assets: list[MediaAsset] = []
        for hit in data.get("hits", []):
            variants = hit.get("videos") or {}
            chosen = variants.get("medium") or variants.get("small") or variants.get("large") or variants.get("tiny")
            if not chosen or not chosen.get("url"):
                continue

            assets.append(
                MediaAsset(
                    provider=self.name,
                    asset_id=str(hit.get("id", "")),
                    media_type="video",
                    preview_url=str(hit.get("picture_id") and f"https://i.vimeocdn.com/video/{hit['picture_id']}_640x360.jpg" or ""),
                    source_url=str(hit.get("pageURL") or ""),
                    download_url=str(chosen.get("url") or ""),
                    width=chosen.get("width"),
                    height=chosen.get("height"),
                    duration_seconds=hit.get("duration"),
                    author=str(hit.get("user") or ""),
                    label=str(hit.get("tags") or query.strip()),
                )
            )

        return assets
