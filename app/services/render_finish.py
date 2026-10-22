"""Post-process an OpenMontage MP4: add narration and soft scene-boundary fades.

Never overwrites the successful original render unless ffmpeg finishes correctly.
No scene images or intermediate models are regenerated.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


async def finish_render(
    output: Path,
    narration_tracks: list[tuple[str, float, float, str]],
    boundaries: list[float],
    fade_seconds: float = 0.34,
) -> None:
    """Mix timed speech and fade video through black around scene cuts."""
    if not narration_tracks and fade_seconds <= 0:
        return

    temporary = output.with_name(output.stem + ".final-tmp.mp4")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(output)]
    for source, _, _, _ in narration_tracks:
        command += ["-i", source]

    video_filters: list[str] = []
    video_map = "0:v:0"
    if fade_seconds > 0 and boundaries:
        starts = [0.0, *[float(t) for t in boundaries]]
        labels = []
        for index, begin in enumerate(starts):
            end_time = starts[index + 1] if index + 1 < len(starts) else None
            segment_duration = end_time - begin if end_time is not None else None
            chain = f"[0:v:0]trim=start={begin:.3f}"
            if end_time is not None:
                chain += f":end={end_time:.3f}"
            chain += ",setpts=PTS-STARTPTS"
            if index > 0:
                chain += f",fade=t=in:st=0:d={fade_seconds / 2:.3f}"
            if segment_duration is not None:
                half = min(fade_seconds / 2, max(0.02, segment_duration / 3))
                chain += f",fade=t=out:st={max(0, segment_duration - half):.3f}:d={half:.3f}"
            chain += f"[v{index}]"
            video_filters.append(chain)
            labels.append(f"[v{index}]")
        video_filters.append(
            "".join(labels) + f"concat=n={len(labels)}:v=1:a=0[vout]"
        )
        video_map = "[vout]"

    if narration_tracks:
        audio_filters: list[str] = []
        for i, (_, start, scene_length, _) in enumerate(narration_tracks, 1):
            offset_ms = round(max(0.0, float(start)) * 1000)
            # Clamp the speech to its scene, avoiding overlap into the next.
            length = max(0.1, float(scene_length))
            audio_filters.append(
                f"[{i}:a:0]aresample=48000,"
                f"aformat=channel_layouts=stereo,"
                f"atrim=duration={length:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d=0.10,"
                f"adelay={offset_ms}:all=1[n{i}]"
            )
        feeds = "".join(f"[n{i}]" for i in range(1, len(narration_tracks) + 1))
        audio_filters.append(
            f"{feeds}amix=inputs={len(narration_tracks)}:"
            f"duration=longest:dropout_transition=0:normalize=0[aout]"
        )
        video_filters.extend(audio_filters)
    if video_filters:
        command += ["-filter_complex", ";".join(video_filters)]
    command += ["-map", video_map]
    if narration_tracks:
        command += ["-map", "[aout]"]
    else:
        command += ["-an"]

    command += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p",
    ]
    if narration_tracks:
        command += ["-c:a", "aac", "-b:a", "192k"]
    command += [
        "-movflags", "+faststart", str(temporary),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=3600)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError("FFmpeg finalization exceeded one hour")
        if process.returncode != 0:
            raise RuntimeError(
                f"FFmpeg narration/transition pass failed: "
                f"{stderr.decode('utf-8', errors='replace')[-1800:]}"
            )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("FFmpeg produced an empty finalized video")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
