from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.schemas import Project, Scene
from app.storage import project_store


AUDIO_TAIL_SECONDS = 0.6


def _speech_text(scene: Scene) -> str:
    narration = scene.narration.strip()
    if narration:
        return narration
    return "\n".join(line.strip() for line in scene.dialogue if line.strip()).strip()


def probe_duration(path: Path) -> float | None:
    if not path.is_file():
        return None
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
        value = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return round(value, 3) if value > 0 else None


def sync_scene(project: Project, scene: Scene) -> Scene:
    audio_duration = None
    if scene.selected_audio is not None and scene.selected_audio.local_path:
        audio_path = project_store.audio_file(project.id, Path(scene.selected_audio.local_path).name)
        if audio_path is not None:
            audio_duration = probe_duration(audio_path)

    final_duration = max(
        float(scene.duration_seconds),
        (audio_duration + AUDIO_TAIL_SECONDS) if audio_duration else 0.0,
        0.5,
    )
    subtitle = scene.subtitle_text.strip() or _speech_text(scene)
    return scene.model_copy(
        update={
            "audio_duration_seconds": audio_duration,
            "final_duration_seconds": round(final_duration, 3),
            "subtitle_text": subtitle,
        }
    )


def sync_project(project: Project, *, save: bool = True) -> Project:
    changed = False
    for index, scene in enumerate(project.storyboard.scenes):
        synced = sync_scene(project, scene)
        if synced != scene:
            project.storyboard.scenes[index] = synced
            changed = True
    if save and changed:
        project_store.save(project)
    return project
