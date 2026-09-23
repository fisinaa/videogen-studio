from __future__ import annotations

import re
from pathlib import Path

from app.schemas import Project
from app.storage import project_store


_ALIAS_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_-]{1,63})")


def _root_for(project: Project) -> Project | None:
    root_id = project.series_id
    if not root_id and project.request.project_type == "series":
        root_id = project.id
    if not root_id:
        return None
    return project_store.load(root_id)


def _episode_number(project: Project, root: Project) -> int:
    return int(project.episode_number or (1 if project.id == root.id else 1))


def _active_references(project: Project):
    root = _root_for(project)
    if root is None:
        return []
    episode = _episode_number(project, root)
    result = []
    for ref in root.series_references:
        if episode < ref.from_episode:
            continue
        if ref.to_episode is not None and episode > ref.to_episode:
            continue
        result.append(ref)
    return result


def _active_text_references(project: Project):
    root = _root_for(project)
    if root is None:
        return []
    episode = _episode_number(project, root)
    result = []
    for ref in root.series_text_references:
        if episode < ref.from_episode:
            continue
        if ref.to_episode is not None and episode > ref.to_episode:
            continue
        result.append(ref)
    return result


def expand_visual_aliases(project: Project, text: str, language: str = "en") -> str:
    """Expand @keys recursively using active series text references."""
    refs = {ref.key.lower(): ref for ref in _active_text_references(project)}
    if not refs or "@" not in text:
        return text

    def expand_value(value: str, stack: tuple[str, ...]) -> str:
        def replace(match: re.Match) -> str:
            key = match.group(1).lower()
            ref = refs.get(key)
            if ref is None or key in stack:
                return match.group(0)
            preferred = ref.text_en if language == "en" else ref.text_ru
            fallback = ref.text_ru if language == "en" else ref.text_en
            replacement = (preferred or fallback).strip()
            if not replacement:
                return match.group(0)
            return expand_value(replacement, (*stack, key))

        return _ALIAS_RE.sub(replace, value)

    return expand_value(text, ())


def scene_reference_context(project: Project, scene, language: str = "en") -> str:
    """Build canonical text context from a scene's separately stored reference keys."""
    keys = [str(key).strip().lstrip("@").lower() for key in getattr(scene, "reference_keys", []) if str(key).strip()]
    if not keys:
        return ""
    active = {ref.key.lower(): ref for ref in _active_text_references(project)}
    lines: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        ref = active.get(key)
        if ref is None:
            continue
        preferred = ref.text_en if language == "en" else ref.text_ru
        fallback = ref.text_ru if language == "en" else ref.text_en
        raw = (preferred or fallback).strip()
        if not raw:
            continue
        expanded = expand_visual_aliases(project, raw, language=language)
        lines.append(f"@{ref.key} ({ref.name}): {expanded}")
    if not lines:
        return ""
    return "SCENE CANONICAL REFERENCES. Apply these exact recurring designs without rewriting the human scene text:\n" + "\n".join(f"- {line}" for line in lines)


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
        root = _root_for(project)
        refs = _active_references(project)
        effective_reference = use_reference or bool(root and (root.character_reference or refs))
        prompt, reference_path = original_image_prompt(project, scene, effective_reference)
        # Backward compatibility: explicit @aliases already present in old prompts still work.
        prompt = expand_visual_aliases(project, prompt, language="en")
        text_context = scene_reference_context(project, scene, language="en")
        if text_context:
            prompt = prompt.rstrip() + "\n\n" + text_context
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
        root = _root_for(project)
        refs = _active_references(project)
        effective_reference = use_reference or bool(root and (root.character_reference or refs))
        prompt, character_path = series_image_prompt(project, scene, effective_reference)
        reference_paths: list[Path] = []
        if effective_reference and character_path is not None:
            reference_paths.append(character_path)

        if effective_reference and root is not None:
            for ref in refs:
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
