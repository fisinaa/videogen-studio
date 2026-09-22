from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import sys
from pathlib import Path


def _json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _load_openmontage() -> Path:
    root = Path(os.environ.get("OPENMONTAGE_ROOT", "/home/faa/OpenMontage")).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"OpenMontage root not found: {root}")
    sys.path.insert(0, str(root))
    return root


def _status_value(tool) -> str:
    try:
        status = tool.get_status()
        return getattr(status, "value", str(status))
    except Exception as exc:
        return f"error:{exc}"


def _install_openai_video_reference_compat() -> None:
    """Adapt OpenMontage's Sora data-URI payload to current openai-python.

    OpenMontage's sora_video currently passes input_reference as
    {"image_url": "data:..."}. Recent openai-python video uploads validate the
    field as an upload value before request serialization, so that dict is
    rejected. Convert only that exact data-URI shape into a normal multipart
    file tuple. All other payloads are left untouched.
    """
    try:
        from openai.resources.videos import Videos
    except Exception:
        return

    if getattr(Videos.create_and_poll, "_videogen_reference_compat", False):
        return

    original = Videos.create_and_poll

    def create_and_poll_compat(self, *args, **kwargs):
        reference = kwargs.get("input_reference")
        if isinstance(reference, dict):
            data_uri = reference.get("image_url")
            if isinstance(data_uri, str) and data_uri.startswith("data:") and ";base64," in data_uri:
                header, encoded = data_uri.split(",", 1)
                mime_type = header[5:].split(";", 1)[0] or "application/octet-stream"
                extension = mimetypes.guess_extension(mime_type) or ".bin"
                try:
                    contents = base64.b64decode(encoded, validate=True)
                except Exception as exc:
                    raise ValueError(f"Invalid Sora input_reference data URI: {exc}") from exc
                kwargs["input_reference"] = (f"reference{extension}", contents, mime_type)
        return original(self, *args, **kwargs)

    create_and_poll_compat._videogen_reference_compat = True
    Videos.create_and_poll = create_and_poll_compat


def status() -> int:
    _load_openmontage()
    from tools.tool_registry import registry

    registry.ensure_discovered()
    providers = []
    for tool in registry.get_by_capability("video_generation"):
        if tool.name == "video_selector":
            continue
        capabilities = list(getattr(tool, "capabilities", []) or [])
        if "image_to_video" not in capabilities:
            continue
        providers.append({
            "tool": tool.name,
            "provider": getattr(tool, "provider", "unknown"),
            "status": _status_value(tool),
            "capabilities": capabilities,
        })

    available = [item for item in providers if item["status"] == "available"]
    _json({
        "success": True,
        "available": available,
        "providers": providers,
    })
    return 0


def generate(job: dict) -> int:
    _load_openmontage()
    _install_openai_video_reference_compat()
    from tools.video.video_selector import VideoSelector

    reference = Path(job["reference_image_path"]).expanduser().resolve()
    output = Path(job["output_path"]).expanduser().resolve()
    if not reference.is_file():
        raise RuntimeError(f"Reference image not found: {reference}")
    output.parent.mkdir(parents=True, exist_ok=True)

    inputs = {
        "operation": "image_to_video",
        "prompt": str(job["prompt"]),
        "reference_image_path": str(reference),
        "aspect_ratio": str(job.get("aspect_ratio") or "16:9"),
        "duration": str(job.get("duration") or "4"),
        "output_path": str(output),
        "preferred_provider": str(job.get("preferred_provider") or "auto"),
    }

    result = VideoSelector().execute(inputs)
    payload = {
        "success": bool(result.success),
        "error": result.error,
        "data": result.data or {},
        "artifacts": list(result.artifacts or []),
    }
    if not result.success:
        _json(payload)
        return 2

    produced = None
    data = result.data or {}
    candidate = data.get("output")
    if candidate:
        candidate_path = Path(str(candidate)).expanduser()
        if candidate_path.is_file():
            produced = candidate_path.resolve()
    if produced is None:
        for item in result.artifacts or []:
            candidate_path = Path(str(item)).expanduser()
            if candidate_path.is_file():
                produced = candidate_path.resolve()
                break

    if produced is not None and produced != output:
        shutil.copy2(produced, output)
    if not output.is_file() or output.stat().st_size == 0:
        payload["success"] = False
        payload["error"] = "OpenMontage provider succeeded but no MP4 output was produced"
        _json(payload)
        return 2

    payload["output"] = str(output)
    payload["selected_provider"] = data.get("selected_provider") or data.get("provider")
    payload["selected_tool"] = data.get("selected_tool")
    _json(payload)
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        raise RuntimeError("usage: openmontage_motion.py status|generate [job-json]")
    action = sys.argv[1]
    if action == "status":
        return status()
    if action == "generate":
        if len(sys.argv) < 3:
            raise RuntimeError("generate requires job JSON")
        return generate(json.loads(sys.argv[2]))
    raise RuntimeError(f"unknown action: {action}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _json({"success": False, "error": str(exc)})
        raise SystemExit(1)
