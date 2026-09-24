from __future__ import annotations

from pathlib import Path


def install_project_reference_support() -> None:
    """Extend app.main image prompt building with uploaded reference metadata.

    app.main route handlers resolve _image_prompt through module globals at call
    time, so replacing that function here updates both single-scene and batch
    image generation without duplicating the core route implementation.
    """
    from app import main as main_module

    def image_prompt(project, scene, use_reference: bool) -> tuple[str, Path | None]:
        reference_path = main_module._character_reference_path(project) if use_reference else None
        base_prompt = (scene.visual_prompt_en or scene.visual_prompt).strip()
        if not base_prompt:
            raise ValueError("Scene visual prompt is empty")

        prompt_parts = [base_prompt]
        if project.storyboard.visual_bible.strip():
            prompt_parts.append("Continuity context:\n" + project.storyboard.visual_bible.strip())

        if reference_path is not None:
            reference_name = (getattr(project, "character_reference_name", "") or "canonical character").strip()
            reference_prompt = (getattr(project, "character_reference_prompt", "") or "").strip()
            prompt_parts.append(
                "The attached image is the canonical visual reference for "
                f"{reference_name}. Preserve identity, face, body proportions, colors, hair/fur, clothing and "
                "distinctive features while changing only pose, camera, environment and action required by the scene."
            )
            if reference_prompt:
                prompt_parts.append("Reference-specific instruction:\n" + reference_prompt)

        if scene.negative_prompt_en.strip():
            prompt_parts.append(f"Avoid: {scene.negative_prompt_en.strip()}")
        prompt_parts.append("No captions, no text, no watermark.")
        return "\n".join(part for part in prompt_parts if part), reference_path

    main_module._image_prompt = image_prompt
