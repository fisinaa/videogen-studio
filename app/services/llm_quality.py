from __future__ import annotations

import re
import time
from contextvars import ContextVar
from types import MethodType

from app.config import settings
from app.schemas import LLMRunInfo, Storyboard
from app.services.model_orchestrator import model_orchestrator


_call_counter: ContextVar[int] = ContextVar("videogen_llm_call_counter", default=0)


def _fingerprint(scene) -> str:
    parts = [
        scene.narration or "",
        scene.action or "",
        scene.visual_prompt_en or scene.visual_prompt or "",
    ]
    text = " | ".join(parts).lower()
    return re.sub(r"\s+", " ", text).strip()


def _run_info(profile: str, operation: str, started: float, calls: int) -> LLMRunInfo:
    config = model_orchestrator.profile_config(profile)
    return LLMRunInfo(
        profile=config["profile"],
        model_name=config["model_name"],
        model_path=str(config["model_path"]),
        operation=operation,
        duration_seconds=round(time.perf_counter() - started, 2),
        calls=max(0, int(calls)),
    )


def install_llm_quality() -> None:
    """Install scene-quality guards, multi-profile routing and LLM timing metadata."""
    import app.main as main_module

    llm = main_module.llm
    if getattr(llm, "_videogen_quality_installed", False):
        return

    # Safe baseline for the Fast 8B profile. Quality 14B switches to two scenes
    # per detail call inside create_storyboard: our A/B test showed 14B can keep
    # distinct scene events while this reduces total web generation time.
    llm.batch_size = 1

    original_chat = llm._chat
    original_create_storyboard = llm.create_storyboard
    original_regenerate_scene = llm.regenerate_scene
    original_rebuild_visual_prompt = llm.rebuild_visual_prompt

    async def counted_chat(self, *args, **kwargs):
        _call_counter.set(_call_counter.get() + 1)
        return await original_chat(*args, **kwargs)

    async def quality_create_storyboard(self, request):
        profile = request.llm_profile or settings.llm_profile_storyboard
        token = _call_counter.set(0)
        started = time.perf_counter()
        previous_batch_size = self.batch_size
        try:
            with model_orchestrator.use_llm_profile(profile) as active_profile:
                # Qwen3-14B quality mode: two scenes per expansion call. Fast 8B
                # remains one scene per call because four-scene batching previously
                # produced repeated narration/action/visual blocks.
                self.batch_size = 2 if active_profile == "quality" else 1
                storyboard: Storyboard = await original_create_storyboard(request)
                seen: set[str] = set()
                updated = list(storyboard.scenes)

                for index, scene in enumerate(storyboard.scenes):
                    fingerprint = _fingerprint(scene)
                    if not fingerprint or fingerprint not in seen:
                        seen.add(fingerprint)
                        continue

                    # Exact repetition across different scene titles is almost
                    # certainly a generation failure. Retry the duplicate once.
                    try:
                        repaired = await original_regenerate_scene(request, storyboard, scene)
                    except Exception:
                        repaired = scene
                    updated[index] = repaired
                    seen.add(_fingerprint(repaired))

                if updated != list(storyboard.scenes):
                    storyboard = storyboard.model_copy(update={"scenes": updated})
                meta = _run_info(active_profile, "storyboard", started, _call_counter.get())
                return storyboard.model_copy(update={"llm_generation": meta})
        finally:
            self.batch_size = previous_batch_size
            _call_counter.reset(token)

    async def quality_rebuild_visual_prompt(self, storyboard, scene):
        profile = settings.llm_profile_visual_prompt
        token = _call_counter.set(0)
        started = time.perf_counter()
        try:
            with model_orchestrator.use_llm_profile(profile) as active_profile:
                rebuilt = await original_rebuild_visual_prompt(storyboard, scene)
                meta = _run_info(active_profile, "visual_prompt", started, _call_counter.get())
                return rebuilt.model_copy(update={"llm_generation": meta})
        finally:
            _call_counter.reset(token)

    async def quality_regenerate_scene(self, request, storyboard, scene):
        profile = request.llm_profile or settings.llm_profile_rewrite
        token = _call_counter.set(0)
        started = time.perf_counter()
        try:
            with model_orchestrator.use_llm_profile(profile) as active_profile:
                rebuilt = await original_regenerate_scene(request, storyboard, scene)
                meta = _run_info(active_profile, "scene_rewrite", started, _call_counter.get())
                return rebuilt.model_copy(update={"llm_generation": meta})
        finally:
            _call_counter.reset(token)

    llm._chat = MethodType(counted_chat, llm)
    llm.create_storyboard = MethodType(quality_create_storyboard, llm)
    llm.rebuild_visual_prompt = MethodType(quality_rebuild_visual_prompt, llm)
    llm.regenerate_scene = MethodType(quality_regenerate_scene, llm)
    llm._videogen_quality_installed = True
