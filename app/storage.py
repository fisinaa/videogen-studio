import json
from pathlib import Path

from app.config import settings
from app.schemas import Project


class ProjectStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.videogen_projects_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, project: Project) -> Path:
        project_dir = self.root / project.id
        project_dir.mkdir(parents=True, exist_ok=True)

        output = project_dir / "project.json"
        output.write_text(
            json.dumps(project.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output

    def list_projects(self) -> list[dict]:
        result: list[dict] = []
        for path in sorted(self.root.glob("*/project.json"), reverse=True):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return result


project_store = ProjectStore()
