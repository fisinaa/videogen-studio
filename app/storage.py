import json
import re
from pathlib import Path

from app.config import settings
from app.schemas import Project


PROJECT_ID_RE = re.compile(r"^[a-f0-9]{12}$")
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class ProjectStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.videogen_projects_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("Invalid project id")
        return self.root / project_id

    def _project_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def media_dir(self, project_id: str) -> Path:
        path = self._project_dir(project_id) / "media"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def media_file(self, project_id: str, filename: str) -> Path | None:
        if not SAFE_FILENAME_RE.fullmatch(filename):
            return None
        path = self.media_dir(project_id) / filename
        return path if path.is_file() else None

    def audio_dir(self, project_id: str) -> Path:
        path = self._project_dir(project_id) / "audio"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def audio_file(self, project_id: str, filename: str) -> Path | None:
        if not SAFE_FILENAME_RE.fullmatch(filename):
            return None
        path = self.audio_dir(project_id) / filename
        return path if path.is_file() else None

    def save(self, project: Project) -> Path:
        project_dir = self._project_dir(project.id)
        project_dir.mkdir(parents=True, exist_ok=True)

        output = self._project_path(project.id)
        output.write_text(
            json.dumps(project.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output

    def load(self, project_id: str) -> Project | None:
        try:
            path = self._project_path(project_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Project.model_validate(data)
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def list_projects(self) -> list[dict]:
        result: list[dict] = []
        for path in sorted(self.root.glob("*/project.json"), reverse=True):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return result


project_store = ProjectStore()
