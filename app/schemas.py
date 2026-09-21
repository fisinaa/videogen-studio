from typing import Literal

from pydantic import BaseModel, Field


ProjectType = Literal["reel", "video", "cartoon", "series_episode"]
AspectRatio = Literal["9:16", "16:9", "1:1"]
MediaType = Literal["video", "image"]


class CreateProjectRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=10000)
    project_type: ProjectType = "cartoon"
    aspect_ratio: AspectRatio = "16:9"
    duration_seconds: int = Field(default=60, ge=10, le=7200)
    language: str = Field(default="ru", min_length=2, max_length=16)


class MediaAsset(BaseModel):
    provider: str
    asset_id: str
    media_type: MediaType
    preview_url: str
    source_url: str
    download_url: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    author: str = ""
    label: str = ""
    local_path: str | None = None


class AudioAsset(BaseModel):
    provider: str
    asset_id: str
    audio_url: str
    download_url: str
    format: str = "mp3"
    voice: str = ""
    model: str = ""
    label: str = ""
    local_path: str | None = None


class Scene(BaseModel):
    id: str
    title: str
    duration_seconds: float = Field(gt=0)
    narration: str = ""
    dialogue: list[str] = Field(default_factory=list)
    action: str
    visual_prompt: str
    visual_prompt_ru: str = ""
    visual_prompt_en: str = ""
    negative_prompt_en: str = ""
    media_search_query: str = ""
    selected_media: MediaAsset | None = None
    media_candidates: list[MediaAsset] = Field(default_factory=list)
    selected_audio: AudioAsset | None = None


class SceneUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    duration_seconds: float = Field(gt=0, le=3600)
    narration: str = Field(default="", max_length=10000)
    dialogue: list[str] = Field(default_factory=list)
    action: str = Field(min_length=1, max_length=10000)
    visual_prompt: str = Field(min_length=1, max_length=10000)
    visual_prompt_ru: str | None = Field(default=None, max_length=10000)
    visual_prompt_en: str | None = Field(default=None, max_length=10000)
    negative_prompt_en: str | None = Field(default=None, max_length=5000)
    media_search_query: str = Field(default="", max_length=1000)


class Storyboard(BaseModel):
    title: str
    logline: str
    visual_style: str
    characters: list[str] = Field(default_factory=list)
    scenes: list[Scene]


class Project(BaseModel):
    id: str
    request: CreateProjectRequest
    storyboard: Storyboard
    character_reference: MediaAsset | None = None
