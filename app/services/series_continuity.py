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
    """Install series-aware image generation without changing existing API paths."""
    import app.main as main_module
    from app.providers.media.router import media_router

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
                    f"{ref.description or 'match the canonical series reference image exactly'}"
                )
            prompt = prompt.rstrip() + "\n\n" + "\n".join(lines)
        return prompt, reference_path

    async def series_generate_scene_image(project: Project, scene, provider: str, use_reference: bool):
        prompt, character_path = series_image_prompt(project, scene, use_reference)
        reference_paths: list[Path] = []
        if use_reference and character_path is not None:
            reference_paths.append(character_path)

        root = _root_for(project)
        if use_reference and root is not None:
            for ref in _active_references(project):
                if not ref.asset.local_path:
                    continue
                path = project_store.media_file(root.id, Path(ref.asset.local_path).name)
                if path is not None and path.is_file() and path not in reference_paths:
                    reference_paths.append(path)

        return await media_router.generate_image(
            prompt=prompt,
            aspect_ratio=project.request.aspect_ratio,
            project_id=project.id,
            scene_id=scene.id,
            media_dir=project_store.media_dir(project.id),
            reference_path=reference_paths[0] if reference_paths else None,
            reference_paths=reference_paths,
            provider=provider,
        )

    main_module._character_reference_path = series_character_reference_path
    main_module._image_prompt = series_image_prompt
    main_module._generate_scene_image = series_generate_scene_image
    main_module._videogen_series_continuity_installed = True
