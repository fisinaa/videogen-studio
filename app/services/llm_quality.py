from __future__ import annotations

import re
from types import MethodType

from app.schemas import Storyboard


def _fingerprint(scene) -> str:
    parts = [
        scene.narration or "",
        scene.action or "",
        scene.visual_prompt_en or scene.visual_prompt or "",
    ]
    text = " | ".join(parts).lower()
    return re.sub(r"\s+", " ", text).strip()


def install_llm_quality() -> None:
    """Tune the local 8B LLM for scene-by-scene storyboard generation.

    Qwen 8B can collapse a four-scene expansion batch into one answer and our
    tolerant parser then has too little scene-specific material to work with.
    Generating detail one scene at a time costs more LLM calls but gives much more
    reliable narration/action/visual alignment. A final duplicate guard retries
    exact repeated scene payloads once using the existing single-scene rewrite path.
    """
    import app.main as main_module

    llm = main_module.llm
    if getattr(llm, "_videogen_quality_installed", False):
        return

    # The outline is still generated in one compact call. Only detailed scene
    # expansion becomes one-scene-per-call.
    llm.batch_size = 1

    original_create_storyboard = llm.create_storyboard
    original_regenerate_scene = llm.regenerate_scene

    async def quality_create_storyboard(self, request):
        storyboard: Storyboard = await original_create_storyboard(request)
        seen: set[str] = set()
        updated = list(storyboard.scenes)

        for index, scene in enumerate(storyboard.scenes):
            fingerprint = _fingerprint(scene)
            if not fingerprint or fingerprint not in seen:
                seen.add(fingerprint)
                continue

            # Exact repetition across different scene titles is almost always a
            # small-model failure. Retry that scene once with the dedicated
            # single-scene prompt, which includes its own title and action.
            try:
                repaired = await original_regenerate_scene(request, storyboard, scene)
            except Exception:
                repaired = scene
            updated[index] = repaired
            seen.add(_fingerprint(repaired))

        if updated != list(storyboard.scenes):
            storyboard = storyboard.model_copy(update={"scenes": updated})
        return storyboard

    llm.create_storyboard = MethodType(quality_create_storyboard, llm)
    llm._videogen_quality_installed = True
