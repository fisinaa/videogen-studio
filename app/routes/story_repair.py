from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.main import llm
from app.services.model_orchestrator import model_orchestrator
from app.storage import project_store


router = APIRouter(tags=["story-repair"])


TAIL_PLAN_SYSTEM_PROMPT = """You are a storyboard story editor.
Rebuild a BROKEN tail of a storyboard as one coherent causal sequence.
Do NOT preserve or paraphrase the broken future scenes. They are intentionally not shown to you.
Do NOT return JSON or markdown. Do NOT expose chain-of-thought.

Return exactly one line for every requested scene id in this format:
SCENE: scene-007 | Short title | One concise story beat

Rules:
- Copy every requested scene id exactly and return each id exactly once, in order.
- Continue only from AUTHORITATIVE PREVIOUS STATE and the original project premise.
- Every scene must cause the next one. No teleportation or unexplained state changes.
- Keep the protagonist's current vehicle/location/important props until a visible event changes them.
- Do not introduce a new recurring character, vehicle, magic element or major location unless the original premise clearly calls for it.
- Do not repeat the same event in multiple scenes.
- Each scene advances the story by one new visible event.
- Keep actions physically plausible and drawable.
- Build escalation, then a clear temporary resolution or cliffhanger by the final requested scene.
- Use the requested story language.
"""


def _previous_state(project, start_index: int) -> str:
    previous = project.storyboard.scenes[max(0, start_index - 3):start_index]
    if not previous:
        return "No previous scene. Start directly from the original premise."
    blocks: list[str] = []
    for scene in previous:
        blocks.append(
            "\n".join(
                [
                    f"{scene.id}: {scene.title}",
                    f"ACTION: {scene.action[:700]}",
                    f"NARRATION: {scene.narration[:500]}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _parse_plan(content: str, expected_ids: list[str]) -> list[dict]:
    found: dict[str, tuple[str, str]] = {}
    expected_set = set(expected_ids)
    for raw in content.splitlines():
        line = raw.strip().strip("` ")
        if not line.upper().startswith("SCENE:"):
            continue
        payload = line.split(":", 1)[1].strip()
        parts = [part.strip() for part in payload.split("|")]
        if len(parts) < 3:
            continue
        scene_id = parts[0]
        if scene_id not in expected_set or scene_id in found:
            continue
        title = parts[1][:240].strip()
        beat = " | ".join(parts[2:])[:1200].strip()
        if title and beat:
            found[scene_id] = (title, beat)

    if any(scene_id not in found for scene_id in expected_ids):
        missing = [scene_id for scene_id in expected_ids if scene_id not in found]
        raise ValueError(f"planner omitted scenes: {', '.join(missing)}")

    return [
        {"id": scene_id, "title": found[scene_id][0], "beat": found[scene_id][1]}
        for scene_id in expected_ids
    ]


def _looks_repetitive(plan: list[dict]) -> bool:
    normalized: list[str] = []
    for item in plan:
        text = re.sub(r"[^a-zа-яё0-9 ]+", " ", (item["title"] + " " + item["beat"]).lower())
        words = [w for w in text.split() if len(w) > 3]
        normalized.append(" ".join(words))
    for i in range(1, len(normalized)):
        a = set(normalized[i - 1].split())
        b = set(normalized[i].split())
        if len(a) >= 4 and len(b) >= 4:
            overlap = len(a & b) / max(1, min(len(a), len(b)))
            if overlap > 0.78:
                return True
    return False


@router.post("/api/projects/{project_id}/storyboard/rebuild-tail")
async def rebuild_storyboard_tail(
    project_id: str,
    start_scene_id: str = Query(..., min_length=1, max_length=80),
):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    scenes = list(project.storyboard.scenes)
    try:
        start_index = next(i for i, scene in enumerate(scenes) if scene.id == start_scene_id)
    except StopIteration:
        raise HTTPException(status_code=404, detail="Start scene not found")

    tail = scenes[start_index:]
    if not tail:
        raise HTTPException(status_code=400, detail="No scenes to rebuild")

    expected_ids = [scene.id for scene in tail]
    id_lines = "\n".join(expected_ids)
    previous_state = _previous_state(project, start_index)
    profile = settings.llm_profile_storyboard or "quality"

    base_prompt = (
        f"Original project idea:\n{project.request.prompt}\n\n"
        f"Project title: {project.storyboard.title}\n"
        f"Logline: {project.storyboard.logline}\n"
        f"Visual style: {project.storyboard.visual_style}\n"
        f"Canonical characters: {'; '.join(project.storyboard.characters)}\n"
        f"Story language: {project.request.language}\n"
        f"Total storyboard scenes: {len(scenes)}\n"
        f"Rebuild scenes {start_index + 1} through {len(scenes)}.\n\n"
        f"AUTHORITATIVE PREVIOUS STATE:\n{previous_state}\n\n"
        f"REQUIRED SCENE IDS, in order:\n{id_lines}\n\n"
        "Create a new coherent tail from scratch. Do not assume anything from the old broken tail. /no_think"
    )

    plan = None
    last_error = ""
    try:
        with model_orchestrator.use_llm_profile(profile):
            for attempt in range(2):
                prompt = base_prompt
                if attempt:
                    prompt += (
                        "\nYour previous plan was invalid or repetitive. Return every required SCENE id exactly once, "
                        "with distinct causal events and no repeated beat."
                    )
                content = await llm._chat(
                    messages=[
                        {"role": "system", "content": TAIL_PLAN_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max(900, min(1800, 85 * len(tail))),
                    temperature=0.3 if attempt == 0 else 0.2,
                )
                try:
                    candidate = _parse_plan(content, expected_ids)
                    if _looks_repetitive(candidate):
                        raise ValueError("planner returned repetitive adjacent beats")
                    plan = candidate
                    break
                except ValueError as exc:
                    last_error = str(exc)

            if plan is None:
                raise ValueError(last_error or "planner did not return a usable tail")

            outline = {
                "title": project.storyboard.title,
                "logline": project.storyboard.logline,
                "visual_style": project.storyboard.visual_style,
                "characters": list(project.storyboard.characters),
            }

            rebuilt: list = []
            for start in range(0, len(plan), 2):
                plan_batch = []
                for item in plan[start:start + 2]:
                    old_scene = next(scene for scene in tail if scene.id == item["id"])
                    plan_batch.append(
                        {
                            "id": item["id"],
                            "title": item["title"],
                            "beat": item["beat"],
                            "duration_seconds": old_scene.duration_seconds,
                        }
                    )
                detailed = await llm._expand_batch(project.request, outline, plan_batch)
                rebuilt.extend(detailed)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Storyboard tail rebuild failed: {exc}") from exc

    rebuilt_by_id = {item["id"]: item for item in rebuilt}
    for index in range(start_index, len(scenes)):
        old = project.storyboard.scenes[index]
        item = rebuilt_by_id.get(old.id)
        plan_item = next((x for x in plan if x["id"] == old.id), None)
        if item is None or plan_item is None:
            raise HTTPException(status_code=502, detail=f"Missing rebuilt data for {old.id}")
        project.storyboard.scenes[index] = old.model_copy(
            update={
                "title": plan_item["title"],
                "narration": item.get("narration", ""),
                "dialogue": item.get("dialogue", []),
                "action": item.get("action", plan_item["beat"]),
                "visual_prompt": item.get("visual_prompt_en") or item.get("visual_prompt") or "",
                "visual_prompt_ru": item.get("visual_prompt_ru", ""),
                "visual_prompt_en": item.get("visual_prompt_en") or item.get("visual_prompt") or "",
                "negative_prompt_en": item.get("negative_prompt_en", ""),
                "media_search_query": item.get("media_search_query", ""),
                "reference_keys": [],
                "selected_media": None,
                "media_candidates": [],
                "selected_audio": None,
                "audio_duration_seconds": None,
                "final_duration_seconds": None,
                "motion_candidates": [],
                "selected_motion_media": None,
                "llm_generation": None,
            }
        )

    project_store.save(project)
    return {
        "project": project,
        "start_scene_id": start_scene_id,
        "rebuilt_scenes": len(tail),
        "profile": model_orchestrator.normalize_profile(profile),
        "plan": plan,
    }
