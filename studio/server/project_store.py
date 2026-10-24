"""Project persistence: one JSON file per studio project on disk.

Deliberately no database — a studio project is a small document (script text,
voice, settings, ordered sections) and files keep the app dependency-free and
inspectable. Sections carry per-section voice/settings overrides; None means
"inherit the project default".
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field


class Section(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    text: str = ""
    voice_id: str | None = None
    settings: dict[str, float] | None = None


class Project(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "Dự án mới"
    engine_id: str = "placeholder"
    voice_id: str
    settings: dict[str, float] = Field(default_factory=dict)
    sections: list[Section] = Field(default_factory=lambda: [Section()])
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, project_id: str) -> Path:
        if not project_id.replace("-", "").isalnum():
            raise ValueError("invalid project id")
        return self.root / f"{project_id}.json"

    def list(self) -> list[Project]:
        projects = []
        for path in sorted(self.root.glob("*.json")):
            try:
                projects.append(Project.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue  # skip corrupt/partial files rather than breaking the listing
        return sorted(projects, key=lambda p: -p.updated_at)

    def get(self, project_id: str) -> Project | None:
        path = self._path(project_id)
        if not path.exists():
            return None
        return Project.model_validate_json(path.read_text(encoding="utf-8"))

    def create(self, title: str, engine_id: str, voice_id: str, settings: dict[str, float]) -> Project:
        project = Project(
            title=title, engine_id=engine_id, voice_id=voice_id, settings=settings
        )
        self._save(project)
        return project

    def save(self, project: Project) -> Project:
        project.updated_at = time.time()
        self._save(project)
        return project

    def delete(self, project_id: str) -> bool:
        with self._lock:
            path = self._path(project_id)
            if path.exists():
                path.unlink()
                return True
            return False

    def _save(self, project: Project) -> None:
        with self._lock:
            tmp = self._path(project.id).with_suffix(".tmp")
            tmp.write_text(project.model_dump_json(), encoding="utf-8")
            tmp.replace(self._path(project.id))
