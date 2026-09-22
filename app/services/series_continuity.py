from __future__ import annotations

from pathlib import Path

from app.schemas import Project
from app.storage import project_store


def _root_for(project: Project) -> Project | None:
    root_id = project.series_id
    if not root_id and project.request.project_type == "series":
        root_id = project.id
    if not root_id:
        return None
    return project_store.load(root_id)


def _active_references(project: Project):
    root = _root_for(project)
    if root is None:
        return []
    episode = int(project.episode_number or (1 if project.id == root.id else 1))
    result = []
    for ref in root.series_references:
        if episode < ref.from_episode:
            continue
        if ref.to_episode is not None and episode > ref.to_episode:
            continue
        result.append(ref)
    return result


def install_series_continuity() -> None:
    """Patch app.main helpers after app.main has created the FastAPI application.

    Existing API routes call these module globals at request time, so replacing the
    helpers here keeps the old endpoints compatible while making child episodes use
    the root series Character Reference and continuity descriptions.
    """
    import app.main as main_module

    if getattr(main_module, "_videogen_series_continuity_installed", False):
        return

    original_image_prompt = main_module._image_prompt

    def series_character_reference_path(project: Project) -> Path | None:
        root = _root_for(project)
        source = root or project
        reference = source.character_reference
        if reference is not None and reference.local_path:
            path = project_store.media_file(source.id, Path(reference.local_path).name)
            if path is not None and path.is_file():
                return path

        # A character-kind Series Reference can act as the canonical image when a
        # dedicated Character Reference has not been generated yet.
        for ref in _active_references(project):
            if ref.kind != "character" or not ref.asset.local_path or root is None:
                continue
            path = project_store.media_file(root.id, Path(ref.asset.local_path).name)
            if path is not None and path.is_file():
                return path
        return None

    def series_image_prompt(project: Project, scene, use_reference: bool):
        prompt, reference_path = original_image_prompt(project, scene, use_reference)
        refs = _active_references(project)
        if refs:
            lines = [
                "SERIES CONTINUITY REFERENCES. Keep these recurring designs stable and do not redesign them:",
            ]
            for ref in refs:
                scope = (
                    f"episodes {ref.from_episode}+"
                    if ref.to_episode is None
                    else f"episodes {ref.from_episode}-{ref.to_episode}"
                )
                lines.append(
                    f"- {ref.kind.upper()} — {ref.name} ({scope}): "
                    f"{ref.description or 'match the canonical series reference image'}"
                )
            prompt = prompt.rstrip() + "\n\n" + "\n".join(lines)
        return prompt, reference_path

    main_module._character_reference_path = series_character_reference_path
    main_module._image_prompt = series_image_prompt
    main_module._videogen_series_continuity_installed = True
