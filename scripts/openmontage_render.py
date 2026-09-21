from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _json_result(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _load_openmontage() -> Path:
    root = Path(os.environ.get("OPENMONTAGE_ROOT", "/home/faa/OpenMontage")).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"OpenMontage root not found: {root}")
    sys.path.insert(0, str(root))
    return root


def _tool_result(result) -> dict:
    return {
        "success": bool(result.success),
        "error": result.error,
        "data": result.data or {},
        "artifacts": list(result.artifacts or []),
    }


def _hyperframes_compat_probe() -> dict:
    """Work around HyperFrames doctor returning non-zero for optional tools.

    HyperFrames 0.8.58 reports ok=false when whisper/Kokoro/MusicGen are absent,
    even though those checks are explicitly optional and the render runtime itself
    is healthy. OpenMontage currently treats any non-zero doctor exit as a hard
    runtime failure. Accept the doctor result when every failed check is optional,
    and prime OpenMontage's process-local CLI probe cache accordingly.
    """
    from tools.video.hyperframes_compose import HyperFramesCompose

    npx = shutil.which("npx")
    if not npx:
        return {"accepted": False, "reason": "npx not found"}

    try:
        proc = subprocess.run(
            [npx, "--yes", "hyperframes", "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        return {"accepted": False, "reason": f"doctor execution failed: {exc}"}

    raw = (proc.stdout or "").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "accepted": False,
            "reason": (proc.stderr or raw or f"doctor exit {proc.returncode}")[-1000:],
        }

    checks = payload.get("checks") or []
    failed = [c for c in checks if not c.get("ok", False)]
    required_failed = [
        c for c in failed
        if "optional" not in str(c.get("detail", "")).lower()
    ]

    accepted = not required_failed
    if accepted:
        # HyperFramesCompose._runtime_check() consults this cache before invoking
        # its stricter probe. Mark the CLI usable for this helper process only.
        HyperFramesCompose._cli_probe_cache = {"status": "ok"}

    return {
        "accepted": accepted,
        "doctor_exit_code": proc.returncode,
        "doctor_ok": bool(payload.get("ok")),
        "optional_failures": [c.get("name") for c in failed if c not in required_failed],
        "required_failures": [c.get("name") for c in required_failed],
        "version": (payload.get("_meta") or {}).get("version"),
    }


def status() -> int:
    _load_openmontage()
    compat = _hyperframes_compat_probe()
    from tools.video.video_compose import VideoCompose

    info = VideoCompose().get_info()
    _json_result({
        "render_engines": info.get("render_engines", {}),
        "render_runtimes": info.get("render_runtimes", {}),
        "hyperframes_note": info.get("hyperframes_note"),
        "remotion_note": info.get("remotion_note"),
        "hyperframes_compat": compat,
    })
    return 0


def render(job: dict) -> int:
    _load_openmontage()
    runtime = str(job.get("runtime", "")).lower()
    if runtime == "hyperframes":
        compat = _hyperframes_compat_probe()
        if not compat.get("accepted"):
            raise RuntimeError(
                "HyperFrames doctor has required failures: "
                + ", ".join(compat.get("required_failures") or [])
                + (f" ({compat.get('reason')})" if compat.get("reason") else "")
            )

        from tools.video.hyperframes_compose import HyperFramesCompose

        tool = HyperFramesCompose()
        result = tool.execute({
            "operation": "render",
            "workspace_path": job["workspace_path"],
            "output_path": job["output_path"],
            "edit_decisions": job["edit_decisions"],
            "asset_manifest": job["asset_manifest"],
            "profile": job.get("profile"),
            "fps": int(job.get("fps", 30)),
            "quality": "standard",
            "strict": False,
            "skip_contrast": False,
        })
    elif runtime == "remotion":
        from tools.video.video_compose import VideoCompose

        tool = VideoCompose()
        result = tool.execute({
            "operation": "render",
            "output_path": job["output_path"],
            "edit_decisions": job["edit_decisions"],
            "asset_manifest": job["asset_manifest"],
            "proposal_packet": job.get("proposal_packet"),
            "script_text": job.get("script_text", ""),
            "profile": job.get("profile"),
            "options": {"subtitle_burn": False, "two_pass_encode": False},
        })
    else:
        raise RuntimeError("runtime must be hyperframes or remotion")

    payload = _tool_result(result)
    data = payload.get("data") or {}
    payload["output"] = data.get("output") or job["output_path"]
    _json_result(payload)
    return 0 if result.success else 2


def main() -> int:
    if len(sys.argv) < 2:
        raise RuntimeError("usage: openmontage_render.py status|render [job-json]")
    action = sys.argv[1]
    if action == "status":
        return status()
    if action == "render":
        if len(sys.argv) < 3:
            raise RuntimeError("render requires job JSON")
        return render(json.loads(sys.argv[2]))
    raise RuntimeError(f"unknown action: {action}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
