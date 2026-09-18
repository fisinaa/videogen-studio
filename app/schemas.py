from typing import Literal

from pydantic import BaseModel, Field


ProjectType = Literal["reel", "video", "cartoon", "series_episode"]
AspectRatio = Literal["9:16", "16:9", "1:1"]


class CreateProjectRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=10000)
    project_type: ProjectType = "cartoon"
    aspect_ratio: AspectRatio = "16:9"
    duration_seconds: int = Field(default=60, ge=10, le=7200)
    language: str = Field(default="ru", min_length=2, max_length=16)


class Scene(BaseModel):
    id: str
    title: str
    duration_seconds: float = Field(gt=0)
    narration: str = ""
    dialogue: list[str] = Field(default_factory=list)
    action: str
    visual_prompt: str
    media_search_query: str = ""


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
