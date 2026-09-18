from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx

from app.config import settings
from app.schemas import MediaAsset


class QwenImageProvider:
    name = "qwen_image"

    @property
    def enabled(self) -> bool:
        return bool(settings.dashscope_api_key and settings.dashscope_base_url)

    def _size_for_aspect_ratio(self, aspect_ratio: str) -> str:
        return {
            "16:9": "2688*1536",
            "9:16": "1536*2688",
            "1:1": "2048*2048",
        }.get(aspect_ratio, "2688*1536")

    async def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        project_id: str,
        scene_id: str,
        media_dir: Path,
    ) -> MediaAsset:
        if not self.enabled:
            raise RuntimeError(
                "Qwen Image is not configured. Set DASHSCOPE_API_KEY and DASHSCOPE_BASE_URL."
            )

        endpoint = (
            settings.dashscope_base_url.rstrip("/")
            + "/services/aigc/multimodal-generation/generation"
        )
        payload = {
            "model": settings.qwen_image_model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": prompt}],
                    }
                ]
            },
            "parameters": {
                "negative_prompt": (
                    "low quality, blurry, distorted anatomy, malformed limbs, duplicate "
                    "subjects, text, watermark, logo, frame, UI elements"
                ),
                "prompt_extend": False,
                "watermark": False,
                "size": self._size_for_aspect_ratio(aspect_ratio),
                "n": 1,
            },
        }
        headers = {
            "Authorization": f"Bearer {settings.dashscope_api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(settings.qwen_image_timeout_seconds)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

            try:
                remote_url = data["output"]["choices"][0]["message"]["content"][0]["image"]
            except (KeyError, IndexError, TypeError) as exc:
                code = data.get("code") if isinstance(data, dict) else None
                message = data.get("message") if isinstance(data, dict) else None
                extra = f" ({code}: {message})" if code or message else ""
                raise ValueError(f"Qwen Image response did not contain an image URL{extra}") from exc

            media_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{scene_id}-qwen-{uuid4().hex[:8]}.png"
            output_path = media_dir / filename

            image_response = await client.get(remote_url)
            image_response.raise_for_status()
            output_path.write_bytes(image_response.content)

        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        width = usage.get("width")
        height = usage.get("height")

        local_url = f"/api/projects/{project_id}/media/{filename}"
        return MediaAsset(
            provider=self.name,
            asset_id=filename,
            media_type="image",
            preview_url=local_url,
            source_url=remote_url,
            download_url=local_url,
            width=width if isinstance(width, int) else None,
            height=height if isinstance(height, int) else None,
            duration_seconds=None,
            author="Qwen Image",
            label=f"{settings.qwen_image_model} · {scene_id}",
            local_path=f"media/{filename}",
        )


qwen_image_provider = QwenImageProvider()
