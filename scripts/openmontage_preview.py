from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


HYPERFRAMES_NPX_PACKAGE = os.environ.get("VIDEOGEN_HYPERFRAMES_NPX_PACKAGE", "hyperframes@0.8.58")


def _json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


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


def main() -> int:
    if len(sys.argv) < 2:
        raise RuntimeError("usage: openmontage_preview.py <job-json>")

    _load_openmontage()
    _patch_hyperframes_package()
    job = json.loads(sys.argv[1])

    from tools.video.hyperframes_compose import HyperFramesCompose

    workspace = Path(job["workspace_path"]).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

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
    if not _port_open(port):
        npx = shutil.which("npx") or "npx"
        env = os.environ.copy()
        # Browser opening is handled by VideoGen's client UI; keep the server process headless.
        env.setdefault("BROWSER", "none")
        subprocess.Popen(
            [npx, "--yes", HYPERFRAMES_NPX_PACKAGE, "preview", "--port", str(port)],
            cwd=str(workspace),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    project_name = workspace.name
    _json({
        "success": True,
        "workspace": str(workspace),
        "port": port,
        "studio_path": f"/#project/{project_name}",
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _json({"success": False, "error": str(exc)})
        raise SystemExit(1)
