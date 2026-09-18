import json
from pathlib import Path

from app.config import settings
from app.schemas import Project


class ProjectStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.videogen_projects_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def _project_path(self, project_id: str) -> Path:
        return self.root / project_id / "project.json"

    def save(self, project: Project) -> Path:
        project_dir = self.root / project.id
        project_dir.mkdir(parents=True, exist_ok=True)

        output = self._project_path(project.id)
        output.write_text(
            json.dumps(project.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output

    def load(self, project_id: str) -> Project | None:
        path = self._project_path(project_id)
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
