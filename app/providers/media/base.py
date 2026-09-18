from abc import ABC, abstractmethod

from app.schemas import MediaAsset


class MediaProvider(ABC):
    name: str

    @property
    @abstractmethod
    def enabled(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def search(self, query: str, limit: int = 6) -> list[MediaAsset]:
        raise NotImplementedError
