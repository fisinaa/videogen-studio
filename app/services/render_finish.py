"""FFmpeg final audio mix and subtle video transitions.

Keeps the successful original MP4 intact unless the full pass succeeds.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


async def _run(command: list[str], timeout: int = 3600) -> tuple[bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        result = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise RuntimeError("FFmpeg timed out") from exc
    if process.returncode != 0:
        raise RuntimeError(
            "Media processing failed: " + result[1].decode("utf-8", errors="replace")[-2200:]
        )
    return result


async def media_streams(path: Path) -> set[str]:
    stdout, _ = await _run([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
        "-of", "json", str(path)
    ], timeout=30)
    info = json.loads(stdout)
    return {str(s.get("codec_type")) for s in info.get("streams", [])}


async def finish_render(
    output: Path,
    narration_tracks: list[tuple[str, float, float, str]],
    boundaries: list[float],
    fade_seconds: float = 0.34,
    music_file: Path | None = None,
    ambience_file: Path | None = None,
    music_volume: float = 0.16,
    ambience_volume: float = 0.13,
) -> dict:
    """Finish a render with narrated scenes, optional music and environmental bed."""
    if not narration_tracks and not music_file and not ambience_file and fade_seconds <= 0:
        return {"audio_added": False, "streams": sorted(await media_streams(output))}

    if not 0 <= music_volume <= 1 or not 0 <= ambience_volume <= 1:
        raise ValueError("Audio volumes must be in 0..1")

    # Use the actual output duration so looping ambience never truncates the movie.
    stdout, _ = await _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(output)
    ], timeout=30)
    total_duration = float(stdout.strip())
    if total_duration <= 0:
        raise ValueError("Cannot determine MP4 duration")

    temporary = output.with_name(output.stem + ".final-tmp.mp4")
    inputs = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(output)]
    for source, _, _, _ in narration_tracks:
        inputs += ["-i", source]
    for bed in (music_file, ambience_file):
        if bed:
            inputs += ["-stream_loop", "-1", "-i", str(bed)]

    filters: list[str] = []
    video_map = "0:v:0"
    if fade_seconds > 0 and boundaries:
        starts = [0.0] + [float(t) for t in boundaries if 0 < float(t) < total_duration]
        labels = []
        for index, begin in enumerate(starts):
            end_time = starts[index + 1] if index + 1 < len(starts) else None
            length = end_time - begin if end_time is not None else total_duration - begin
            chain = f"[0:v:0]trim=start={begin:.3f}"
            if end_time is not None:
                chain += f":end={end_time:.3f}"
            chain += ",setpts=PTS-STARTPTS"
            half = min(fade_seconds / 2, max(0.02, length / 3))
            if index > 0:
                chain += f",fade=t=in:st=0:d={half:.3f}"
            if end_time is not None:
                chain += f",fade=t=out:st={max(0, length-half):.3f}:d={half:.3f}"
            chain += f"[v{index}]"
            filters.append(chain)
            labels.append(f"[v{index}]")
        filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[vout]")
        video_map = "[vout]"

    tracks: list[str] = []
    for i, (_, start, length, _) in enumerate(narration_tracks, 1):
        delay_ms = round(max(0.0, float(start)) * 1000)
        name = f"speech{i}"
        filters.append(
            f"[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo,"
            f"atrim=duration={max(.1, length):.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d=0.06,adelay={delay_ms}:all=1[{name}]"
        )
        tracks.append(f"[{name}]")

    input_index = 1 + len(narration_tracks)
    for bed, volume, kind in ((music_file, music_volume, "music"),
                              (ambience_file, ambience_volume, "ambience")):
        if bed is not None and volume > 0:
            filters.append(
                f"[{input_index}:a:0]aresample=48000,"
                f"aformat=channel_layouts=stereo,"
                f"atrim=duration={total_duration:.3f},asetpts=PTS-STARTPTS,"
                f"volume={volume:.3f},afade=t=in:st=0:d=0.6,"
                f"afade=t=out:st={max(0.0, total_duration-1.0):.3f}:d={min(1.0, total_duration):.3f}"
                f"[{kind}]"
            )
            tracks.append(f"[{kind}]")
        if bed is not None:
            input_index += 1

    if tracks:
        filters.append(
            "".join(tracks) + f"amix=inputs={len(tracks)}:"
            f"duration=longest:dropout_transition=0:normalize=0,"
            f"atrim=duration={total_duration:.3f}[aout]"
        )

    command = inputs
    if filters:
        command += ["-filter_complex", ";".join(filters)]
    command += ["-map", video_map]
    if tracks:
        command += ["-map", "[aout]"]
    else:
        command += ["-an"]
    command += ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p"]
    if tracks:
        command += ["-c:a", "aac", "-b:a", "192k"]
    command += ["-movflags", "+faststart", "-t", f"{total_duration:.3f}", str(temporary)]
    try:
        await _run(command)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("FFmpeg produced no finalized video")
        found = await media_streams(temporary)
        if "video" not in found or (tracks and "audio" not in found):
            raise RuntimeError(f"Final output missing expected streams: {found}")
        temporary.replace(output)
        return {"audio_added": bool(tracks), "streams": sorted(found),
                "narration_tracks": len(narration_tracks),
                "music": bool(music_file), "ambience": bool(ambience_file)}
    finally:
        temporary.unlink(missing_ok=True)
