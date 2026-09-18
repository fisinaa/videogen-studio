import httpx

from app.config import settings
from app.providers.media.base import MediaProvider
from app.schemas import MediaAsset


class PexelsProvider(MediaProvider):
    name = "pexels"

    @property
    def enabled(self) -> bool:
        return bool(settings.pexels_api_key.strip())

    async def search(self, query: str, limit: int = 6) -> list[MediaAsset]:
        if not self.enabled or not query.strip():
            return []

        headers = {"Authorization": settings.pexels_api_key.strip()}
        params = {"query": query.strip(), "per_page": min(max(limit, 1), 20)}

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            data = response.json()

        assets: list[MediaAsset] = []
        for video in data.get("videos", []):
            files = [item for item in video.get("video_files", []) if item.get("link")]
            if not files:
                continue

            files.sort(
                key=lambda item: (
                    0 if item.get("file_type") == "video/mp4" else 1,
                    abs((item.get("width") or 1280) - 1280),
                )
            )
            chosen = files[0]
            user = video.get("user") or {}
            assets.append(
                MediaAsset(
                    provider=self.name,
                    asset_id=str(video.get("id", "")),
                    media_type="video",
                    preview_url=str(video.get("image") or ""),
                    source_url=str(video.get("url") or ""),
                    download_url=str(chosen.get("link") or ""),
                    width=chosen.get("width"),
                    height=chosen.get("height"),
                    duration_seconds=video.get("duration"),
                    author=str(user.get("name") or ""),
                    label=query.strip(),
                )
            )

        return assets
