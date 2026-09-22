from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from uuid import uuid4

import httpx

from app.config import settings
from app.schemas import MediaAsset


class OpenAIImageProvider:
    name = "openai_image"

    @property
    def enabled(self) -> bool:
        return bool(settings.openai_api_key)

    def _size_for_aspect_ratio(self, aspect_ratio: str) -> tuple[str, int, int]:
        return {
            "16:9": ("1536x1024", 1536, 1024),
            "9:16": ("1024x1536", 1024, 1536),
            "1:1": ("1024x1024", 1024, 1024),
        }.get(aspect_ratio, ("1536x1024", 1536, 1024))

    def _decode_image_response(self, data: dict) -> tuple[bytes | None, str]:
        try:
            first = data["data"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("OpenAI Image response did not contain image data") from exc

        source_url = f"openai://{settings.openai_image_model}"
        encoded = first.get("b64_json") if isinstance(first, dict) else None
        if encoded:
            try:
                return base64.b64decode(encoded), source_url
            except (ValueError, TypeError) as exc:
                raise ValueError("OpenAI Image returned invalid base64 image data") from exc

        remote_url = first.get("url") if isinstance(first, dict) else None
        return None, remote_url or source_url

    async def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        project_id: str,
        scene_id: str,
        media_dir: Path,
        reference_path: Path | None = None,
        reference_paths: list[Path] | None = None,
    ) -> MediaAsset:
        if not self.enabled:
            raise RuntimeError("OpenAI Image is not configured. Set OPENAI_API_KEY in .env.")

        refs: list[Path] = []
        for path in ([reference_path] if reference_path is not None else []) + list(reference_paths or []):
            if path is None:
                continue
            resolved = Path(path)
            if resolved not in refs:
                refs.append(resolved)
        for path in refs:
            if not path.is_file():
                raise ValueError(f"Reference image not found: {path}")

        size, width, height = self._size_for_aspect_ratio(aspect_ratio)
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        timeout = httpx.Timeout(settings.openai_image_timeout_seconds)

        async with httpx.AsyncClient(timeout=timeout) as client:
            if refs:
                endpoint = settings.openai_base_url.rstrip("/") + "/images/edits"
                data_fields = {
                    "model": settings.openai_image_model,
                    "prompt": prompt,
                    "size": size,
                    "quality": settings.openai_image_quality,
                    "output_format": "png",
                }
                files = [
                    (
                        "image[]",
                        (
                            path.name,
                            path.read_bytes(),
                            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        ),
                    )
                    for path in refs
                ]
                response = await client.post(
                    endpoint,
                    headers=headers,
                    data=data_fields,
                    files=files,
                )
            else:
                endpoint = settings.openai_base_url.rstrip("/") + "/images/generations"
                payload = {
                    "model": settings.openai_image_model,
                    "prompt": prompt,
                    "size": size,
                    "quality": settings.openai_image_quality,
                    "n": 1,
                }
                response = await client.post(
                    endpoint,
                    headers={**headers, "Content-Type": "application/json"},
                    json=payload,
                )

            response.raise_for_status()
            data = response.json()
            image_bytes, source_url = self._decode_image_response(data)

            if image_bytes is None and source_url.startswith("http"):
                image_response = await client.get(source_url)
                image_response.raise_for_status()
                image_bytes = image_response.content

            if not image_bytes:
                raise ValueError("OpenAI Image response contained neither b64_json nor image URL")

        media_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{scene_id}-openai-{uuid4().hex[:8]}.png"
        output_path = media_dir / filename
        output_path.write_bytes(image_bytes)

        local_url = f"/api/projects/{project_id}/media/{filename}"
        mode = f"reference edit ({len(refs)} refs)" if refs else "generation"
        return MediaAsset(
            provider=self.name,
            asset_id=filename,
            media_type="image",
            preview_url=local_url,
            source_url=source_url,
            download_url=local_url,
            width=width,
            height=height,
            duration_seconds=None,
            author="OpenAI",
            label=(
                f"{settings.openai_image_model} · {settings.openai_image_quality} · "
                f"{mode} · {scene_id}"
            ),
            local_path=f"media/{filename}",
        )


openai_image_provider = OpenAIImageProvider()
