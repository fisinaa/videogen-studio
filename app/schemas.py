from typing import Literal

from pydantic import BaseModel, Field


ProjectType = Literal["reel", "video", "cartoon", "series", "series_episode"]
AspectRatio = Literal["9:16", "16:9", "1:1"]
MediaType = Literal["video", "image"]
MotionMode = Literal["static", "camera_motion", "image_to_video"]
ReferenceKind = Literal["character", "object", "location", "style"]
LLMProfile = Literal["fast", "quality"]


class LLMRunInfo(BaseModel):
    profile: LLMProfile
    model_name: str
    model_path: str = ""
    operation: str = ""
    duration_seconds: float = 0.0
    calls: int = 0


class CreateProjectRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=10000)
    project_type: ProjectType = "cartoon"
    aspect_ratio: AspectRatio = "16:9"
    duration_seconds: int = Field(default=60, ge=10, le=7200)
    language: str = Field(default="ru", min_length=2, max_length=16)
    llm_profile: LLMProfile | None = None


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


class SeriesReference(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    kind: ReferenceKind = "object"
    asset: MediaAsset
    from_episode: int = Field(default=1, ge=1)
    to_episode: int | None = Field(default=None, ge=1)


class SeriesTextReference(BaseModel):
    """Reusable textual canon entry or composite visual alias.

    Keys are referenced from scene visual prompts as ``@char_tim`` or
    ``@visual_tim_boat``. ``text_ru`` / ``text_en`` may themselves contain other
    @keys, which are expanded recursively immediately before image generation.
    """

    id: str
    key: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=200)
    kind: ReferenceKind = "character"
    text_ru: str = Field(default="", max_length=6000)
    text_en: str = Field(default="", max_length=6000)
    is_block: bool = False
    from_episode: int = Field(default=1, ge=1)
    to_episode: int | None = Field(default=None, ge=1)


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
    audio_duration_seconds: float | None = None
    final_duration_seconds: float | None = None
    subtitle_text: str = ""
    subtitle_enabled: bool = True
    motion_mode: MotionMode = "camera_motion"
    motion_candidates: list[MediaAsset] = Field(default_factory=list)
    selected_motion_media: MediaAsset | None = None
    llm_generation: LLMRunInfo | None = None


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
    visual_bible: str = ""
    characters: list[str] = Field(default_factory=list)
    scenes: list[Scene]
    llm_generation: LLMRunInfo | None = None


class Project(BaseModel):
    id: str
    request: CreateProjectRequest
    storyboard: Storyboard
    character_reference: MediaAsset | None = None
    series_id: str | None = None
    series_title: str = ""
    episode_number: int | None = None
    series_references: list[SeriesReference] = Field(default_factory=list)
    series_text_references: list[SeriesTextReference] = Field(default_factory=list)
