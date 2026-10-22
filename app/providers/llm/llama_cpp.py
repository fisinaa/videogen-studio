import math
import re
import time
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError

from app.config import settings
from app.providers.llm.base import LLMProvider
from app.schemas import CreateProjectRequest, Scene, Storyboard
from app.services.model_orchestrator import model_orchestrator


STORY_SYSTEM_PROMPT = """You are a skilled writer of coherent short animated films.
Write a complete, causally connected story BEFORE any scene breakdown.
No markdown, no reasoning. Return only:
TITLE: short title
LOGLINE: one sentence
STYLE: concise visual style
CHARACTER: immutable protagonist appearance (fur, eyes, clothes, distinctive features)
STORY: a complete coherent story in 120-300 words, one line

Story must have a clear starting situation, inciting incident, escalating obstacle,
meaningful choice and resolution. Keep location and time changes plausible.
Every event must follow from previous events. No unexplained teleports,
random unrelated creatures or changes in protagonist appearance.
Use the user's language. Do not quote copyrighted stories.
"""

BEATS_SYSTEM_PROMPT = """You break an ALREADY WRITTEN story into concrete chronological events.
Do not invent a new plot. No markdown, no JSON, no reasoning.
Return exactly the requested number of lines of the form:
SCENE: short scene title :: unique physical event that changes the situation

Each line must show a different event of the provided story.
Follow the story's beginning-to-end chronology. Ensure causal progression.
Do not use the same action for multiple scene lines.
No repeating scene titles, no weather filler, no random animal encounters.
"""

NARRATIVE_SCENE_SYSTEM_PROMPT = """You write ONE scene's narration and visible action.
No markdown, no reasoning. Return ONLY:
NARRATION: one concise original Russian narration sentence
DIALOGUE: NONE, or spoken character dialogue
ACTION: one unique concrete visible action that enacts CURRENT BEAT

Never repeat an earlier scene's action or narration.
Preserve the complete story and the protagonist's immutable appearance.
Never replace current scene's event with a later event.
"""

OUTLINE_SYSTEM_PROMPT = """You are a planning engine for an AI video production studio.
Do NOT return JSON. Do NOT use markdown. Do NOT expose chain-of-thought.

Return a compact outline using ONLY these line prefixes:
TITLE: project title
LOGLINE: one short sentence
STYLE: short visual style description
CHARACTER: one character description
SCENE: short scene title :: one short sentence describing what happens

You may output multiple CHARACTER and SCENE lines.
Never use the token :: inside a title or description except as the separator shown above.
If the user references an existing literary work, use only high-level plot ideas and
character archetypes unless the user provides text they have rights to. Do not quote
or closely reproduce copyrighted prose.
"""


DETAIL_SYSTEM_PROMPT = """You expand a SMALL batch of scene outlines for video production.
Do NOT return JSON. Do NOT use markdown. Do NOT expose chain-of-thought.

For every requested scene, return exactly one block in this format:
[scene-001]
NARRATION: concise narration in the requested language
DIALOGUE: optional dialogue, or NONE
ACTION: concise visible action
VISUAL_RU: detailed concrete visual prompt in Russian
VISUAL_EN: detailed production-ready English prompt for an image-generation model
NEGATIVE: short English negative prompt describing unwanted visual errors
SEARCH: short English stock-media search query
[/scene-001]

Preserve the supplied scene ids. Always include ALL seven fields.

VISUAL prompt rules:
- Do NOT write a literary retelling. Describe what must be visible in the frame.
- Explicitly describe the main character, stable physical traits, visible action, environment and key props.
- Preserve supplied character traits across scenes. Do not silently change color, species, clothes or distinctive features.
- Include camera/framing/composition, lighting, mood and the supplied project visual style.
- State important spatial relationships when relevant: foreground/background, left/right, near/far, on the river/on the bank.
- Do not invent extra characters or major objects unless the scene needs them.
- Prefer concrete visual language over vague words such as beautiful, interesting or magical.
- VISUAL_EN should normally be 70-160 words and be directly usable by a local image model.
- VISUAL_RU should describe the same frame for human editing.
- NEGATIVE should suppress text, watermark, extra characters, anatomy errors and scene-specific mistakes.
- SEARCH must stay short and utilitarian.
"""


SINGLE_SCENE_SYSTEM_PROMPT = """You rewrite ONE existing video scene.
Do NOT return JSON. Do NOT use markdown. Do NOT expose chain-of-thought.

Return exactly one block using the supplied scene id:
[scene-001]
NARRATION: concise narration in the requested language
DIALOGUE: optional dialogue, or NONE
ACTION: concise visible action
VISUAL_RU: detailed concrete visual prompt in Russian
VISUAL_EN: detailed production-ready English prompt for an image-generation model
NEGATIVE: short English negative prompt
SEARCH: short English stock-media search query
[/scene-001]

Always include ALL seven fields. Keep the scene consistent with the project title,
logline, visual style, character descriptions and neighboring context. Do not change
the scene id or duration. VISUAL_EN must describe subject, action, environment, props,
camera/composition, lighting, mood and style using concrete visual language.
"""


VISUAL_ONLY_SYSTEM_PROMPT = """You are the visual prompt builder for an AI animation studio.
Do NOT return JSON. Do NOT use markdown. Do NOT expose chain-of-thought.

Return exactly this block for the supplied scene id:
[scene-001]
VISUAL_RU: detailed concrete visual prompt in Russian
VISUAL_EN: detailed production-ready English prompt for an image-generation model
NEGATIVE: short English negative prompt
SEARCH: short English stock-media search query
[/scene-001]

This is a production prompt, not prose. VISUAL_EN should normally be 70-160 words.
Describe the exact visible frame: main character and stable traits, action, environment,
key objects, spatial relationships, camera/framing/composition, lighting, mood and the
project visual style. Preserve all supplied character traits. Do not invent extra
characters. Make important story objects unmistakable. NEGATIVE should suppress text,
watermarks, extra characters, anatomy errors and scene-specific mistakes.
"""


VISUAL_REPAIR_SYSTEM_PROMPT = """You repair one visual prompt for an image-generation pipeline.
Do NOT use JSON or markdown. Output EXACTLY four lines and nothing else:
VISUAL_RU: <detailed Russian description of the exact visible frame>
VISUAL_EN: <70-160 word detailed English production prompt>
NEGATIVE: <short English negative prompt>
SEARCH: <short English stock-media search query>

VISUAL_EN must explicitly cover subject and stable traits, visible action, environment,
important props and their spatial relationship, camera/framing/composition, lighting,
mood and visual style. Do not invent extra characters.
"""


PLAIN_VISUAL_SYSTEM_PROMPT = """Write ONE production-ready English image-generation prompt only.
No label, no JSON, no markdown, no explanation. Use 70-160 words. Explicitly describe
the subject and stable traits, visible action, environment, key props and spatial
relationships, camera/framing/composition, lighting, mood and visual style. Do not
invent extra characters or text inside the image.
"""


def _storyboard_debug(stage: str, message: str, response: str | None = None) -> None:
    """Opt-in console and file diagnostics; never log credentials or request headers."""
    if not settings.llm_debug_log:
        return
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"[{stamp}] [{stage}] {message}"
    print("[VideoGen LLM] " + line, flush=True)
    path = settings.llm_debug_log_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
            if response is not None:
                file.write("----- MODEL RESPONSE -----\n")
                file.write(response[:12000] + "\n")
                file.write("----- END RESPONSE -----\n")
    except OSError as exc:
        print(f"[VideoGen LLM] Could not write diagnostic file: {exc}", flush=True)


def _llm_stage(messages: list[dict]) -> str:
    system = str(messages[0].get("content", "")) if messages else ""
    if system == STORY_SYSTEM_PROMPT:
        return "STORY"
    if system == BEATS_SYSTEM_PROMPT:
        return "BEATS"
    if system == NARRATIVE_SCENE_SYSTEM_PROMPT:
        return "NARRATIVE"
    if system == VISUAL_ONLY_SYSTEM_PROMPT:
        return "VISUAL"
    if system == SINGLE_SCENE_SYSTEM_PROMPT:
        return "REWRITE"
    return "LLM"


def _scene_durations(total_seconds: int, scene_count: int) -> list[float]:
    scene_count = max(1, scene_count)
    base = total_seconds // scene_count
    remainder = total_seconds % scene_count
    return [float(base + (1 if index < remainder else 0)) for index in range(scene_count)]


def _clean_line_value(value: str) -> str:
    return value.strip().strip("` ")


def _parse_outline_text(content: str, target_scene_count: int, target_duration: int) -> dict:
    title = "Untitled project"
    logline = ""
    visual_style = "cinematic animation"
    characters: list[str] = []
    scene_ideas: list[tuple[str, str]] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("TITLE:"):
            title = _clean_line_value(line.split(":", 1)[1]) or title
        elif upper.startswith("LOGLINE:"):
            logline = _clean_line_value(line.split(":", 1)[1])
        elif upper.startswith("STYLE:"):
            visual_style = _clean_line_value(line.split(":", 1)[1]) or visual_style
        elif upper.startswith("CHARACTER:"):
            value = _clean_line_value(line.split(":", 1)[1])
            if value:
                characters.append(value)
        elif upper.startswith("SCENE:"):
            value = _clean_line_value(line.split(":", 1)[1])
            if "::" in value:
                scene_title, beat = value.split("::", 1)
            else:
                scene_title, beat = value, value
            scene_title = scene_title.strip() or f"Scene {len(scene_ideas) + 1}"
            beat = beat.strip() or "Continue the story."
            scene_ideas.append((scene_title, beat))

    minimum = max(1, math.floor(target_scene_count * 0.70))
    if len(scene_ideas) < minimum:
        raise ValueError(
            f"Outline too short: expected about {target_scene_count} scenes, parsed {len(scene_ideas)} SCENE lines"
        )

    scene_ideas = scene_ideas[:target_scene_count]
    if len(scene_ideas) != target_scene_count:
        raise ValueError(f"Expected {target_scene_count} distinct scene beats, got {len(scene_ideas)}")

    durations = _scene_durations(target_duration, target_scene_count)
    scenes: list[dict] = []
    for index, ((scene_title, beat), duration) in enumerate(zip(scene_ideas, durations), start=1):
        scenes.append({
            "id": f"scene-{index:03d}",
            "title": scene_title,
            "duration_seconds": duration,
            "beat": beat,
        })

    return {
        "title": title,
        "logline": logline,
        "visual_style": visual_style,
        "characters": characters,
        "scenes": scenes,
    }


def _normalize_field_key(key: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", key.strip().upper()).strip("_")
    aliases = {
        "VISUAL_PROMPT": "VISUAL",
        "VISUAL_PROMPT_RU": "VISUAL_RU",
        "RUSSIAN_VISUAL": "VISUAL_RU",
        "RUSSIAN_PROMPT": "VISUAL_RU",
        "PROMPT_RU": "VISUAL_RU",
        "VISUAL_PROMPT_EN": "VISUAL_EN",
        "ENGLISH_VISUAL": "VISUAL_EN",
        "ENGLISH_PROMPT": "VISUAL_EN",
        "PROMPT_EN": "VISUAL_EN",
        "IMAGE_PROMPT": "VISUAL_EN",
        "IMAGE_PROMPT_EN": "VISUAL_EN",
        "NEGATIVE_PROMPT": "NEGATIVE",
        "NEGATIVE_PROMPT_EN": "NEGATIVE",
        "MEDIA_SEARCH": "SEARCH",
        "SEARCH_QUERY": "SEARCH",
        "MEDIA_SEARCH_QUERY": "SEARCH",
    }
    return aliases.get(normalized, normalized)


def _extract_block_fields(content: str, scene_id: str) -> dict[str, str]:
    """Parse the line DSL tolerantly.

    Small local models sometimes omit the scene wrapper, spell labels as
    `VISUAL EN`, or wrap long prompt values onto following lines. We accept all of
    those while still returning the canonical field names used by VideoGen.
    """
    pattern = re.compile(
        rf"\[{re.escape(scene_id)}\](.*?)\[/{re.escape(scene_id)}\]",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(content)
    # Never reuse the entire multi-scene response as a missing scene's fields.
    # A single unwrapped scene is permitted only for single-scene calls.
    if not match and re.search(r"\[/?scene-\d+\]", content, re.IGNORECASE):
        return {}
    block = match.group(1) if match else content
    fields: dict[str, str] = {}
    valid = {
        "NARRATION", "DIALOGUE", "ACTION", "VISUAL", "VISUAL_RU",
        "VISUAL_EN", "NEGATIVE", "SEARCH",
    }
    current_key: str | None = None

    for raw_line in block.splitlines():
        line = raw_line.strip().strip("` ")
        if not line or re.fullmatch(r"\[/?[^\]]+\]", line):
            continue

        if ":" in line:
            raw_key, value = line.split(":", 1)
            key = _normalize_field_key(raw_key)
            if key in valid:
                current_key = key
                value = value.strip()
                if value:
                    fields[key] = value
                elif key not in fields:
                    fields[key] = ""
                continue

        if current_key:
            fields[current_key] = (fields.get(current_key, "") + " " + line).strip()

    return fields


def _parse_detail_blocks(content: str, batch: list[dict]) -> list[dict]:
    result: list[dict] = []
    for source in batch:
        scene_id = source["id"]
        fields = _extract_block_fields(content, scene_id)
        dialogue_value = fields.get("DIALOGUE", "")
        dialogue = []
        if dialogue_value and dialogue_value.upper() not in {"NONE", "NO", "N/A", "-"}:
            dialogue = [dialogue_value]

        visual_en = fields.get("VISUAL_EN") or fields.get("VISUAL") or source.get("beat") or "cinematic animation"
        visual_ru = fields.get("VISUAL_RU", "")
        result.append({
            "id": scene_id,
            "title": source["title"],
            "duration_seconds": source["duration_seconds"],
            "narration": fields.get("NARRATION", ""),
            "dialogue": dialogue,
            "action": fields.get("ACTION") or source.get("beat") or "Continue the story.",
            "visual_prompt": visual_en,
            "visual_prompt_ru": visual_ru,
            "visual_prompt_en": visual_en,
            "negative_prompt_en": fields.get("NEGATIVE", ""),
            "media_search_query": fields.get("SEARCH", ""),
            "_raw_fields": fields,
        })
    return result


def _duration_seconds(storyboard: Storyboard) -> float:
    return sum(scene.duration_seconds for scene in storyboard.scenes)


class LlamaCppProvider(LLMProvider):
    batch_size = 4

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        return headers

    async def _chat(self, messages: list[dict], max_tokens: int, temperature: float) -> str:
        stage = _llm_stage(messages)
        started = time.monotonic()
        _storyboard_debug(stage, f"request start max_tokens={max_tokens} temp={temperature}")
        try:
            async with model_orchestrator.llm_slot():
                await model_orchestrator.ensure_llm_running()
                payload = {
                    "model": settings.llm_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if settings.llm_disable_thinking:
                    payload["chat_template_kwargs"] = {"enable_thinking": False}
                timeout = httpx.Timeout(settings.llm_timeout_seconds)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        settings.llm_base_url.rstrip("/") + "/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    )
                    response.raise_for_status()
                data = response.json()
                message = data["choices"][0]["message"]
                content = message.get("content") or ""
                if not content.strip():
                    raise ValueError("LLM returned empty content; check thinking mode and max_tokens")
                finish_reason = data["choices"][0].get("finish_reason", "?")
                usage = data.get("usage") or {}
                _storyboard_debug(
                    stage,
                    f"success elapsed={time.monotonic()-started:.1f}s "
                    f"finish_reason={finish_reason} "
                    f"output_chars={len(content)} "
                    f"completion_tokens={usage.get('completion_tokens', '?')}",
                    response=content,
                )
                return content
        except Exception as exc:
            _storyboard_debug(
                stage,
                f"ERROR elapsed={time.monotonic()-started:.1f}s "
                f"exception={type(exc).__name__}: {str(exc)[:500]}",
            )
            raise

    async def _create_outline(self, request: CreateProjectRequest) -> dict:
        target_count = max(1, round(request.duration_seconds / 10))
        story_prompt = (
            f"User idea:\n{request.prompt}\n"
            f"Project type: {request.project_type}\n"
            f"Language: {request.language}\n"
            f"Target length: {request.duration_seconds} seconds.\n"
            "Write the coherent full story BEFORE splitting into shots."
        )
        story_text = await self._chat(
            [{"role": "system", "content": STORY_SYSTEM_PROMPT},
             {"role": "user", "content": story_prompt}],
            max_tokens=1100, temperature=0.65,
        )
        story_fields = {}
        for line in story_text.splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip().upper() in {"TITLE", "LOGLINE", "STYLE", "CHARACTER", "STORY"}:
                story_fields[key.strip().upper()] = value.strip()
        story = story_fields.get("STORY", "")
        if len(story.split()) < 55 or not story_fields.get("CHARACTER"):
            raise ValueError("LLM failed to produce a complete story and immutable character description")
        beats_prompt = (
            f"FULL STORY (follow exactly):\n{story}\n"
            f"Protagonist: {story_fields['CHARACTER']}\n"
            f"Required events: {target_count}.\n"
            f"Return exactly {target_count} SCENE lines, earliest to latest."
        )
        def extract_beats(content: str) -> list[tuple[str, str]]:
            result = []
            for line in content.splitlines():
                if not line.strip().upper().startswith("SCENE:"):
                    continue
                parts = line.split(":", 1)[1].split("::", 1)
                if len(parts) != 2 or not all(part.strip() for part in parts):
                    continue
                result.append((parts[0].strip(), parts[1].strip()))
            return result
        beats = []
        for attempt in range(3):
            response = await self._chat(
                [{"role": "system", "content": BEATS_SYSTEM_PROMPT},
                 {"role": "user", "content": beats_prompt}],
                max_tokens=max(600, target_count * 65),
                temperature=0.3 + 0.1 * attempt,
            )
            beats = extract_beats(response)
            normalized = [re.sub(r"\W+", "", event.casefold()) for _, event in beats]
            if len(beats) == target_count and len(set(normalized)) == target_count:
                break
        else:
            raise ValueError(
                f"Could not generate {target_count} distinct plot beats; received {len(beats)}"
            )
        durations = _scene_durations(request.duration_seconds, target_count)
        return {
            "title": story_fields.get("TITLE") or "Без названия",
            "logline": story_fields.get("LOGLINE") or story[:150],
            "visual_style": story_fields.get("STYLE") or "cinematic animation",
            "characters": [story_fields["CHARACTER"]],
            "story_arc": story,
            "scenes": [
                {"id": f"scene-{i:03d}", "title": title,
                 "duration_seconds": duration, "beat": beat}
                for i, ((title, beat), duration) in enumerate(zip(beats, durations), 1)
            ],
        }

    async def _expand_scene(self, request: CreateProjectRequest, outline: dict,
                            index: int, completed: list[dict]) -> dict:
        source = outline["scenes"][index]
        prev = outline["scenes"][index - 1]["beat"] if index else "NONE"
        nxt = (outline["scenes"][index + 1]["beat"]
               if index + 1 < len(outline["scenes"]) else "NONE")
        used = "\n".join(
            f"{s['id']}: {s['action']} | {s['narration']}" for s in completed
        ) or "NONE"
        context = (
            f"Full story:\n{outline['story_arc']}\n"
            f"Character (immutable): {'; '.join(outline['characters'])}\n"
            f"Visual style: {outline['visual_style']}\n"
            f"Previous beat: {prev}\n"
            f"CURRENT SCENE: {source['id']} | {source['title']}\n"
            f"CURRENT BEAT (must depict this): {source['beat']}\n"
            f"Next beat: {nxt}\n"
            f"Already used scenes:\n{used}\n"
            f"Language: {request.language}\n"
        )
        def normal(value: str) -> str:
            return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", value.casefold())).strip()
        used_actions = {normal(s["action"]) for s in completed}
        used_narrations = {normal(s["narration"]) for s in completed}
        narrative = None
        for attempt in range(3):
            answer = await self._chat(
                [{"role": "system", "content": NARRATIVE_SCENE_SYSTEM_PROMPT},
                 {"role": "user", "content": context + "Write ONLY this scene."}],
                max_tokens=400, temperature=0.4 + 0.1 * attempt,
            )
            fields = _extract_block_fields(answer, source["id"])
            action, narration = fields.get("ACTION", ""), fields.get("NARRATION", "")
            if (action.strip() and narration.strip()
                    and normal(action) not in used_actions
                    and normal(narration) not in used_narrations):
                narrative = fields
                break
        if narrative is None:
            raise ValueError(f"{source['id']}: repeated or incomplete narrative after retries")
        visual_context = (
            context +
            f"FINAL ACTION (do not change): {narrative['ACTION']}\n"
            f"FINAL NARRATION: {narrative['NARRATION']}\n"
            "Write image prompts that SHOW this action, not a different plot event. "
            "Use the character bible verbatim where relevant."
        )
        visual_fields = {}
        for attempt in range(2):
            visual_answer = await self._chat(
                [{"role": "system", "content": VISUAL_ONLY_SYSTEM_PROMPT},
                 {"role": "user", "content": visual_context}],
                max_tokens=1050, temperature=0.3 + 0.1 * attempt,
            )
            visual_fields = _extract_block_fields(visual_answer, source["id"])
            if visual_fields.get("VISUAL_EN", "").strip() and visual_fields.get("VISUAL_RU", "").strip():
                break
        else:
            raise ValueError(f"{source['id']}: image prompt generation incomplete")
        dialogue_raw = narrative.get("DIALOGUE", "")
        return {
            "id": source["id"],
            "title": source["title"],
            "duration_seconds": source["duration_seconds"],
            "narration": narrative["NARRATION"],
            "dialogue": ([] if dialogue_raw.upper() in {"NONE", "-", ""} else [dialogue_raw]),
            "action": narrative["ACTION"],
            "visual_prompt": visual_fields["VISUAL_EN"],
            "visual_prompt_ru": visual_fields["VISUAL_RU"],
            "visual_prompt_en": visual_fields["VISUAL_EN"],
            "negative_prompt_en": visual_fields.get("NEGATIVE", ""),
            "media_search_query": visual_fields.get("SEARCH", ""),
        }

    async def _expand_batch(self, request: CreateProjectRequest, outline: dict, batch: list[dict]) -> list[dict]:
        # Supply the complete plot to every batch; otherwise the model loses continuity.
        plot = "\n".join(
            f"{scene['id']} | {scene['title']} | {scene['beat']}" for scene in outline["scenes"]
        )
        scene_lines = "\n".join(
            f"{scene['id']} | {scene['duration_seconds']:.0f}s | {scene['title']} | {scene['beat']}"
            for scene in batch
        )
        context = (
            f"Project: {outline['title']}\n"
            f"Logline: {outline['logline']}\n"
            f"Visual style: {outline['visual_style']}\n"
            f"Canonical character traits (immutable): {'; '.join(outline['characters'])}\n"
            f"Narration language: {request.language}\n"
            f"COMPLETE plot with scene order:\n{plot}\n"
        )
        prompt = (
            context + "\nExpand ONLY these scenes:\n" + scene_lines +
            "\nEvery scene must enact its OWN beat, not reuse an adjacent scene. "
            "The protagonist's physical traits cannot change. "
            "Return separately tagged blocks for each scene."
        )
        content = await self._chat(
            messages=[
                {"role": "system", "content": DETAIL_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1800,
            temperature=settings.llm_temperature,
        )
        parsed = _parse_detail_blocks(content, batch)
        previous_actions: set[str] = set()
        previous_narrations: set[str] = set()

        def normalized(value: str) -> str:
            return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", value.lower())).strip()

        for index, source in enumerate(batch):
            item = parsed[index]
            fields = item.pop("_raw_fields", {})
            wrapped = bool(re.search(
                rf"\[{re.escape(source['id'])}\].*?\[/{re.escape(source['id'])}\]",
                content, re.IGNORECASE | re.DOTALL,
            ))
            action = normalized(item["action"])
            narration = normalized(item["narration"])
            valid = (
                wrapped
                and all(fields.get(k, "").strip() for k in ("NARRATION", "ACTION", "VISUAL_EN"))
                and action not in previous_actions
                and narration not in previous_narrations
            )
            if not valid:
                # One scene per retry. No copying an unwrapped response to all scenes.
                for attempt in range(2):
                    single_prompt = (
                        context +
                        f"\nWRITE ONLY {source['id']}: {source['title']}\n"
                        f"Unique event for this scene: {source['beat']}\n"
                        f"Scene duration: {source['duration_seconds']} seconds.\n"
                        "Do not repeat earlier events or their narration. "
                        "Maintain the protagonist's canonical physical appearance.\n"
                        "Previously used actions:\n" + "\n".join(previous_actions) +
                        "\nPreviously used narration:\n" + "\n".join(previous_narrations) +
                        f"\nReturn one complete [{source['id']}] ... [/{source['id']}] block."
                    )
                    retry = await self._chat(
                        messages=[
                            {"role": "system", "content": SINGLE_SCENE_SYSTEM_PROMPT},
                            {"role": "user", "content": single_prompt},
                        ],
                        max_tokens=1100,
                        temperature=0.35 + attempt * 0.1,
                    )
                    candidate = _parse_detail_blocks(retry, [source])[0]
                    candidate_fields = candidate.pop("_raw_fields", {})
                    candidate_action = normalized(candidate["action"])
                    candidate_narration = normalized(candidate["narration"])
                    if (
                        all(candidate_fields.get(k, "").strip() for k in ("NARRATION", "ACTION", "VISUAL_EN"))
                        and candidate_action not in previous_actions
                        and candidate_narration not in previous_narrations
                    ):
                        item = candidate
                        action, narration = candidate_action, candidate_narration
                        break
                else:
                    raise ValueError(
                        f"Storyline validation failed for {source['id']}: "
                        "incomplete or duplicated scene after retries"
                    )
            previous_actions.add(action)
            previous_narrations.add(narration)
            parsed[index] = item
        return parsed

    def _visual_context(self, storyboard: Storyboard, scene: Scene) -> str:
        return (
            f"Project title: {storyboard.title}\n"
            f"Logline: {storyboard.logline}\n"
            f"Visual style: {storyboard.visual_style}\n"
            f"Canonical character descriptions: {'; '.join(storyboard.characters)}\n\n"
            f"Scene id: {scene.id}\n"
            f"Scene title: {scene.title}\n"
            f"Visible action: {scene.action}\n"
            f"Narration: {scene.narration}\n"
            f"Dialogue: {' | '.join(scene.dialogue) if scene.dialogue else 'NONE'}\n"
            f"Current visual prompt: {scene.visual_prompt_en or scene.visual_prompt}\n"
        )

    async def rebuild_visual_prompt(self, storyboard: Storyboard, scene: Scene) -> Scene:
        context = self._visual_context(storyboard, scene)
        prompt = context + (
            "\nRebuild only the visual-generation prompt. Keep the story facts and canonical character traits. "
            "Be specific enough that a local image model does not need to invent the composition."
        )
        content = await self._chat(
            messages=[
                {"role": "system", "content": VISUAL_ONLY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
            temperature=0.35,
        )
        fields = _extract_block_fields(content, scene.id)
        visual_en = fields.get("VISUAL_EN") or fields.get("VISUAL")

        # Qwen 8B is good enough for prompt writing but occasionally ignores the
        # requested wrapper/label format. Retry once with a much simpler 4-line DSL.
        if not visual_en:
            repair_content = await self._chat(
                messages=[
                    {"role": "system", "content": VISUAL_REPAIR_SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                max_tokens=850,
                temperature=0.2,
            )
            repair_fields = _extract_block_fields(repair_content, scene.id)
            if repair_fields:
                fields.update({key: value for key, value in repair_fields.items() if value})
            visual_en = fields.get("VISUAL_EN") or fields.get("VISUAL")

        # Last-resort LLM fallback: ask for raw English prompt only. This keeps the
        # UI usable even when the small model refuses every requested line label.
        if not visual_en:
            plain = await self._chat(
                messages=[
                    {"role": "system", "content": PLAIN_VISUAL_SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                max_tokens=650,
                temperature=0.2,
            )
            visual_en = plain.strip().strip("` ").strip()

        if not visual_en:
            # Never destroy the existing prompt merely because formatting failed.
            visual_en = scene.visual_prompt_en or scene.visual_prompt
        if not visual_en:
            raise ValueError("Prompt builder returned an empty visual prompt after retries")

        return scene.model_copy(update={
            "visual_prompt": visual_en,
            "visual_prompt_ru": fields.get("VISUAL_RU", scene.visual_prompt_ru),
            "visual_prompt_en": visual_en,
            "negative_prompt_en": fields.get("NEGATIVE", scene.negative_prompt_en),
            "media_search_query": fields.get("SEARCH", scene.media_search_query),
        })

    async def regenerate_scene(self, request: CreateProjectRequest, storyboard: Storyboard, scene: Scene) -> Scene:
        source = {
            "id": scene.id,
            "title": scene.title,
            "duration_seconds": scene.duration_seconds,
            "beat": scene.action,
        }
        prompt = (
            f"Project title: {storyboard.title}\n"
            f"Logline: {storyboard.logline}\n"
            f"Visual style: {storyboard.visual_style}\n"
            f"Canonical character descriptions: {'; '.join(storyboard.characters)}\n"
            f"Narration/dialogue language: {request.language}\n"
            f"Scene duration: {scene.duration_seconds:.0f}s\n"
            f"Existing title: {scene.title}\n"
            f"Existing action: {scene.action}\n"
            f"Existing narration: {scene.narration}\n"
            f"Existing dialogue: {' | '.join(scene.dialogue) if scene.dialogue else 'NONE'}\n"
            f"Existing visual prompt: {scene.visual_prompt_en or scene.visual_prompt}\n"
            f"Existing search query: {scene.media_search_query}\n\n"
            f"Rewrite only {scene.id}. Keep the same story purpose but improve the scene."
        )
        content = await self._chat(
            messages=[
                {"role": "system", "content": SINGLE_SCENE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
            temperature=settings.llm_temperature,
        )
        parsed = _parse_detail_blocks(content, [source])[0]
        raw_fields = parsed.pop("_raw_fields", {})
        merged = {
            "id": scene.id,
            "title": scene.title,
            "duration_seconds": scene.duration_seconds,
            "narration": parsed["narration"] if raw_fields.get("NARRATION", "").strip() else scene.narration,
            "dialogue": parsed["dialogue"] if "DIALOGUE" in raw_fields else scene.dialogue,
            "action": parsed["action"] if raw_fields.get("ACTION", "").strip() else scene.action,
            "visual_prompt": parsed["visual_prompt"] if raw_fields.get("VISUAL_EN", raw_fields.get("VISUAL", "")).strip() else scene.visual_prompt,
            "visual_prompt_ru": parsed["visual_prompt_ru"] if raw_fields.get("VISUAL_RU", "").strip() else scene.visual_prompt_ru,
            "visual_prompt_en": parsed["visual_prompt_en"] if raw_fields.get("VISUAL_EN", raw_fields.get("VISUAL", "")).strip() else (scene.visual_prompt_en or scene.visual_prompt),
            "negative_prompt_en": parsed["negative_prompt_en"] if raw_fields.get("NEGATIVE", "").strip() else scene.negative_prompt_en,
            "media_search_query": parsed["media_search_query"] if raw_fields.get("SEARCH", "").strip() else scene.media_search_query,
            "selected_media": scene.selected_media,
            "media_candidates": scene.media_candidates,
            "selected_audio": None,
        }
        return Scene.model_validate(merged)

    async def create_storyboard(self, request: CreateProjectRequest) -> Storyboard:
        outline = await self._create_outline(request)
        outline_scenes = outline["scenes"]
        detailed_scenes: list[dict] = []
        for index in range(len(outline_scenes)):
            detailed_scenes.append(await self._expand_scene(request, outline, index, detailed_scenes))

        payload = {
            "title": outline["title"],
            "logline": outline["logline"],
            "visual_style": outline["visual_style"],
            "story_arc": outline["story_arc"],
            "story_beats": [scene["beat"] for scene in outline_scenes],
            "characters": outline["characters"],
            "scenes": detailed_scenes,
        }
        try:
            storyboard = Storyboard.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"Assembled storyboard schema invalid: {exc}") from exc

        # Validate across batches as well as within each batch.
        def canonical(value: str) -> str:
            return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", value.casefold())).strip()

        seen_actions: dict[str, str] = {}
        seen_narrations: dict[str, str] = {}
        for scene in storyboard.scenes:
            for value, seen, label in (
                (scene.action, seen_actions, "action"),
                (scene.narration, seen_narrations, "narration"),
            ):
                key = canonical(value)
                if not key:
                    raise ValueError(f"{scene.id}: missing {label}")
                if key in seen:
                    raise ValueError(
                        f"Storyboard contains identical {label} in "
                        f"{seen[key]} and {scene.id}; regenerate with a more specific story idea"
                    )
                seen[key] = scene.id

        actual = _duration_seconds(storyboard)
        if abs(actual - request.duration_seconds) > 0.01:
            raise ValueError(
                "Internal duration allocation error: "
                f"requested={request.duration_seconds}s, assembled={actual:.1f}s"
            )
        return storyboard
