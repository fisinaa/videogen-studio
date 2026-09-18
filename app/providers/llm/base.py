from abc import ABC, abstractmethod

from app.schemas import CreateProjectRequest, Storyboard


class LLMProvider(ABC):
    @abstractmethod
    async def create_storyboard(self, request: CreateProjectRequest) -> Storyboard:
        raise NotImplementedError
