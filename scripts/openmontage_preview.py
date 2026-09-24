from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


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


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


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


def _wait_for_port(port: int, timeout: float, host: str = "127.0.0.1") -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open(port, host):
            return True
        time.sleep(0.25)
    return False


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

    # HyperFrames itself binds only to localhost. Keep it on an internal loopback
    # port and expose a second public port through a tiny TCP proxy bound to
    # 0.0.0.0. This preserves HTTP/WebSocket traffic without modifying HyperFrames.
    public_port = int(job.get("preview_port") or 3002)
    internal_port = public_port + 10000
    preview_log = Path(f"/tmp/videogen-openmontage-preview-{public_port}.log")
    proxy_log = Path(f"/tmp/videogen-openmontage-proxy-{public_port}.log")
    _log(f"Workspace ready. Public port: {public_port}; HyperFrames loopback port: {internal_port}")

    # Start HyperFrames on the internal localhost-only port.
    if not _port_open(internal_port):
        npx = shutil.which("npx") or "npx"
        env = os.environ.copy()
        env.setdefault("BROWSER", "none")
        cmd = [
            npx,
            "--yes",
            HYPERFRAMES_NPX_PACKAGE,
            "preview",
            "--port",
            str(internal_port),
            "--force-new",
        ]
        _log("Starting HyperFrames: " + " ".join(cmd))
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

        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            if _port_open(internal_port):
                break
            code = proc.poll()
            if code is not None and code != 0:
                detail = _tail(preview_log) or f"hyperframes preview exited {code}"
                raise RuntimeError(f"HyperFrames Studio failed to start:\n{detail}")
            time.sleep(0.5)
        else:
            detail = _tail(preview_log)
            raise RuntimeError(
                "HyperFrames Studio did not open its internal port within 90 seconds"
                + (f". Log:\n{detail}" if detail else "")
            )
    else:
        _log(f"Internal HyperFrames port {internal_port} already open; reusing it")

    # A process left from an older VideoGen version may occupy the public port on
    # localhost. In that case fail explicitly so the user gets a useful message
    # instead of silently opening the wrong service.
    if _port_open(public_port):
        raise RuntimeError(
            f"Public Studio port {public_port} is already occupied. Stop the old preview first "
            f"(pkill -f 'hyperframes.*preview') and retry."
        )

    proxy_script = (Path(__file__).resolve().parent / "openmontage_lan_proxy.py").resolve()
    if not proxy_script.is_file():
        raise RuntimeError(f"LAN proxy helper not found: {proxy_script}")

    proxy_cmd = [
        sys.executable,
        str(proxy_script),
        "0.0.0.0",
        str(public_port),
        "127.0.0.1",
        str(internal_port),
    ]
    _log("Starting LAN proxy: " + " ".join(proxy_cmd))
    with proxy_log.open("w", encoding="utf-8") as log_file:
        proxy_proc = subprocess.Popen(
            proxy_cmd,
            cwd=str(Path.cwd()),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    if not _wait_for_port(public_port, 10.0):
        code = proxy_proc.poll()
        detail = _tail(proxy_log) or f"LAN proxy exit={code}"
        raise RuntimeError(f"OpenMontage LAN proxy failed to open port {public_port}:\n{detail}")

    project_name = workspace.name
    _json({
        "success": True,
        "workspace": str(workspace),
        "port": public_port,
        "internal_port": internal_port,
        "bind_host": "0.0.0.0",
        "studio_path": f"/#project/{project_name}",
        "preview_log": str(preview_log),
        "proxy_log": str(proxy_log),
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _json({"success": False, "error": str(exc)})
        raise SystemExit(1)
