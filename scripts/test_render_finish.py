"""Offline smoke test for narration/transition finalization.

Run: .venv/bin/python scripts/test_render_finish.py
Uses synthetic video/tone only. Does not touch user projects.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.render_finish import finish_render


def run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True, text=True)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="videogen-render-smoke-") as root:
        path = Path(root)
        video = path / "test.mp4"
        voice1 = path / "voice1.wav"
        voice2 = path / "voice2.wav"
        run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video))
        for freq, output in ((440, voice1), (660, voice2)):
            run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=0.8",
                str(output))
        # Exercise narration, background music and ambience in the same mix.
        music = path / "music.wav"
        ambience = path / "ambience.wav"
        for frequency, destination in ((190, music), (95, ambience)):
            run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=1.0",
                str(destination))
        await finish_render(
            video,
            [(str(voice1), 0, 2, "scene-001"),
             (str(voice2), 2, 2, "scene-002")],
            boundaries=[2.0],
            fade_seconds=0.34,
            music_file=music,
            ambience_file=ambience,
            music_volume=0.15,
            ambience_volume=0.18,
        )
        probe = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_type",
            "-of", "json", str(video),
        ], text=True)
        info = json.loads(probe)
        types = {stream["codec_type"] for stream in info["streams"]}
        duration = float(info["format"]["duration"])
        if types != {"video", "audio"} or not 3.8 <= duration <= 4.2:
            raise AssertionError(f"Unexpected streams/duration: {info}")
        print(f"OK: video + audio, duration={duration:.2f}s, fades + narration + music + ambience passed")


if __name__ == "__main__":
    asyncio.run(main())
