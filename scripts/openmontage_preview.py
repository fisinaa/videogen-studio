from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


# Use the published HyperFrames package name. Pinning an old version here can
# force an unnecessary cold download or leave the preview command incompatible
# with the OpenMontage checkout on disk.
HYPERFRAMES_NPX_PACKAGE = os.environ.get("VIDEOGEN_HYPERFRAMES_NPX_PACKAGE", "hyperframes")


def _json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _log(message: str) -> None:
    print(f"[videogen-openmontage] {message}", file=sys.stderr, flush=True)


def _load_openmontage() -> Path:
    root = Path(os.environ.get("OPENMONTAGE_ROOT", "/home/faa/OpenMontage")).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"OpenMontage root not found: {root}")
    sys.path.insert(0, str(root))
    return root


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _patch_hyperframes_package() -> None:
    from tools.video.hyperframes_compose import HyperFramesCompose

    HyperFramesCompose._NPM_PACKAGE = HYPERFRAMES_NPX_PACKAGE
    HyperFramesCompose._npm_resolve_cache = None
    HyperFramesCompose._cli_probe_cache = {"status": "ok"}


def _tail(path: Path, limit: int = 5000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


def main() -> int:
    if len(sys.argv) < 2:
        raise RuntimeError("usage: openmontage_preview.py <job-json>")

    root = _load_openmontage()
    _log(f"OpenMontage root: {root}")
    _patch_hyperframes_package()
    job = json.loads(sys.argv[1])

    from tools.video.hyperframes_compose import HyperFramesCompose

    workspace = Path(job["workspace_path"]).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    _log(f"Scaffolding HyperFrames workspace: {workspace}")

    result = HyperFramesCompose().execute({
        "operation": "scaffold_workspace",
        "workspace_path": str(workspace),
        "edit_decisions": job["edit_decisions"],
        "asset_manifest": job["asset_manifest"],
        "profile": job.get("profile"),
        "fps": int(job.get("fps", 30)),
    })
    if not result.success:
        raise RuntimeError(result.error or "HyperFrames workspace scaffold failed")

    port = int(job.get("preview_port") or 3002)
    preview_log = Path(f"/tmp/videogen-openmontage-preview-{port}.log")
    _log(f"Workspace ready. Preview port: {port}")

    if not _port_open(port):
        npx = shutil.which("npx") or "npx"
        env = os.environ.copy()
        env.setdefault("BROWSER", "none")
        cmd = [
            npx,
            "--yes",
            HYPERFRAMES_NPX_PACKAGE,
            "preview",
            "--port",
            str(port),
            "--force-new",
        ]
        _log("Starting: " + " ".join(cmd))
        with preview_log.open("w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                cmd,
                cwd=str(workspace),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        # Do not report success until the Studio is actually listening. This
        # prevents VideoGen from navigating the popup to a dead port while npx is
        # still downloading/booting HyperFrames.
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            if _port_open(port):
                break
            code = proc.poll()
            if code is not None and code != 0:
                detail = _tail(preview_log) or f"hyperframes preview exited {code}"
                raise RuntimeError(f"HyperFrames Studio failed to start:\n{detail}")
            time.sleep(0.5)
        else:
            detail = _tail(preview_log)
            raise RuntimeError(
                "HyperFrames Studio did not open its port within 90 seconds"
                + (f". Log:\n{detail}" if detail else "")
            )
    else:
        _log(f"Preview port {port} is already open; reusing existing Studio")

    project_name = workspace.name
    _json({
        "success": True,
        "workspace": str(workspace),
        "port": port,
        "studio_path": f"/#project/{project_name}",
        "preview_log": str(preview_log),
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _json({"success": False, "error": str(exc)})
        raise SystemExit(1)
