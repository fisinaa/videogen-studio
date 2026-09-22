from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path

import httpx

from app.config import settings


class ModelOrchestrator:
    """Coordinate llama-server profiles and local GPU generation on one GPU."""

    def __init__(self) -> None:
        self._gpu_lock = asyncio.Lock()
        self._current_job = "idle"
        self._image_batch_owner: asyncio.Task | None = None
        self._image_batch_profile: str | None = None
        self._llm_idle_task: asyncio.Task | None = None
        self._requested_llm_profile: ContextVar[str | None] = ContextVar(
            "videogen_llm_profile", default=None
        )

    @property
    def enabled(self) -> bool:
        return settings.model_orchestration_enabled

    def normalize_profile(self, profile: str | None) -> str:
        value = (profile or self._requested_llm_profile.get() or settings.llm_profile_default).strip().lower()
        return value if value in {"fast", "quality"} else settings.llm_profile_default

    def profile_config(self, profile: str | None) -> dict:
        name = self.normalize_profile(profile)
        if name == "quality":
            return {
                "profile": "quality",
                "model_name": settings.llm_quality_model_name,
                "model_path": settings.llm_quality_model_path,
                "args": settings.llm_quality_args,
            }
        return {
            "profile": "fast",
            "model_name": settings.llm_fast_model_name,
            "model_path": settings.llm_fast_model_path,
            "args": settings.llm_fast_args,
        }

    @contextmanager
    def use_llm_profile(self, profile: str | None):
        normalized = self.normalize_profile(profile)
        token = self._requested_llm_profile.set(normalized)
        try:
            yield normalized
        finally:
            self._requested_llm_profile.reset(token)

    async def _systemctl(self, action: str) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            "systemctl",
            "--user",
            action,
            settings.llm_systemd_unit,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        text = (stdout or stderr).decode("utf-8", errors="replace").strip()
        return process.returncode, text

    def _read_state(self) -> dict:
        try:
            return json.loads(settings.llm_state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_state(self, payload: dict) -> None:
        path = settings.llm_state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _clear_state(self) -> None:
        try:
            settings.llm_state_file.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def active_llm_profile(self) -> str | None:
        if settings.llm_launch_mode.strip().lower() != "direct":
            return None
        state = self._read_state()
        pid = int(state.get("pid") or 0)
        if not self._pid_alive(pid):
            self._clear_state()
            return None
        profile = str(state.get("profile") or "").strip().lower()
        return profile if profile in {"fast", "quality"} else None

    async def llm_active(self, profile: str | None = None) -> bool:
        if not self.enabled:
            return True
        if settings.llm_launch_mode.strip().lower() == "direct":
            active = self.active_llm_profile()
            if active is None:
                return False
            return profile is None or active == self.normalize_profile(profile)
        code, _ = await self._systemctl("is-active")
        return code == 0

    async def _wait_llm_http(self, timeout_seconds: int) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        url = settings.llm_base_url.rstrip("/") + "/models"
        last_error = ""
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(url)
                if response.status_code < 500:
                    return
                last_error = f"HTTP {response.status_code}"
            except Exception as exc:
                last_error = str(exc)
            await asyncio.sleep(1.0)
        suffix = f": {last_error}" if last_error else ""
        raise RuntimeError(f"llama-server did not become ready within {timeout_seconds}s{suffix}")

    async def _start_direct_llm(self, profile: str) -> None:
        config = self.profile_config(profile)
        model_path: Path = config["model_path"]
        if not settings.llm_server_bin.is_file():
            raise RuntimeError(f"llama-server not found: {settings.llm_server_bin}")
        if not model_path.is_file():
            raise RuntimeError(f"LLM model not found: {model_path}")

        # The old fixed-model service may still be installed. Stop it before using
        # the direct multi-profile launcher so port 8081 is never contested.
        try:
            await self._systemctl("stop")
        except Exception:
            pass

        command = [
            str(settings.llm_server_bin),
            "-m",
            str(model_path),
            *shlex.split(str(config["args"])),
            "--host",
            settings.llm_host,
            "--port",
            str(settings.llm_port),
        ]
        settings.llm_log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = settings.llm_log_file.open("ab", buffering=0)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=log_handle,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log_handle.close()

        self._write_state(
            {
                "pid": process.pid,
                "profile": config["profile"],
                "model_name": config["model_name"],
                "model_path": str(model_path),
                "command": command,
            }
        )
        try:
            await self._wait_llm_http(settings.llm_start_timeout_seconds)
        except Exception:
            await self._stop_direct_llm()
            raise

    async def _stop_direct_llm(self) -> bool:
        state = self._read_state()
        pid = int(state.get("pid") or 0)
        if not self._pid_alive(pid):
            self._clear_state()
            return False

        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                self._clear_state()
                return False

        deadline = asyncio.get_running_loop().time() + settings.llm_stop_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if not self._pid_alive(pid):
                self._clear_state()
                return True
            await asyncio.sleep(0.25)

        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        self._clear_state()
        return True

    def _cancel_llm_idle_shutdown(self) -> None:
        task = self._llm_idle_task
        self._llm_idle_task = None
        if task is not None and not task.done():
            task.cancel()

    def _schedule_llm_idle_shutdown(self) -> None:
        if not self.enabled or not settings.llm_on_demand:
            return
        self._cancel_llm_idle_shutdown()
        timeout = max(1, int(settings.llm_idle_timeout_seconds))
        self._llm_idle_task = asyncio.create_task(self._idle_shutdown_worker(timeout))

    async def _idle_shutdown_worker(self, timeout_seconds: int) -> None:
        try:
            await asyncio.sleep(timeout_seconds)
            async with self._gpu_lock:
                if self._current_job != "idle":
                    return
                if await self.llm_active():
                    self._current_job = "llm:idle-stop"
                    self._write_lock_file(self._current_job)
                    try:
                        await self.stop_llm()
                    finally:
                        self._current_job = "idle"
                        self._remove_lock_file()
        except asyncio.CancelledError:
            return
        finally:
            if self._llm_idle_task is asyncio.current_task():
                self._llm_idle_task = None

    async def ensure_llm_running(self, profile: str | None = None) -> str:
        requested = self.normalize_profile(profile)
        if not self.enabled:
            return requested

        self._cancel_llm_idle_shutdown()
        current = asyncio.current_task()
        if (
            self._gpu_lock.locked()
            and self._current_job.startswith("image:")
            and current is not self._image_batch_owner
        ):
            async with self._gpu_lock:
                pass

        if await self.llm_active(requested):
            try:
                await self._wait_llm_http(3)
                return requested
            except RuntimeError:
                pass

        if await self.llm_active():
            await self.stop_llm()

        if settings.llm_launch_mode.strip().lower() == "direct":
            await self._start_direct_llm(requested)
        else:
            if requested != settings.llm_profile_default:
                raise RuntimeError(
                    "LLM_LAUNCH_MODE=systemd cannot switch profiles; use LLM_LAUNCH_MODE=direct"
                )
            code, detail = await self._systemctl("start")
            if code != 0:
                raise RuntimeError(
                    f"Cannot start {settings.llm_systemd_unit}: {detail or 'systemctl failed'}"
                )
            await self._wait_llm_http(settings.llm_start_timeout_seconds)
        return requested

    async def stop_llm(self) -> bool:
        if not self.enabled:
            return False
        if settings.llm_launch_mode.strip().lower() == "direct":
            stopped = await self._stop_direct_llm()
            # Also stop the legacy service if it happens to be active.
            try:
                code, _ = await self._systemctl("is-active")
                if code == 0:
                    await self._systemctl("stop")
                    stopped = True
            except Exception:
                pass
            return stopped

        was_active = await self.llm_active()
        if not was_active:
            return False
        code, detail = await self._systemctl("stop")
        if code != 0:
            raise RuntimeError(
                f"Cannot stop {settings.llm_systemd_unit}: {detail or 'systemctl failed'}"
            )
        deadline = asyncio.get_running_loop().time() + settings.llm_stop_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if not await self.llm_active():
                return True
            await asyncio.sleep(0.5)
        raise RuntimeError(
            f"{settings.llm_systemd_unit} did not stop within {settings.llm_stop_timeout_seconds}s"
        )

    def _write_lock_file(self, value: str) -> None:
        path = settings.gpu_lock_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")

    def _remove_lock_file(self) -> None:
        try:
            settings.gpu_lock_file.unlink(missing_ok=True)
        except OSError:
            pass

    @asynccontextmanager
    async def llm_slot(self, profile: str | None = None):
        requested = self.normalize_profile(profile)
        if not self.enabled:
            yield requested
            return
        self._cancel_llm_idle_shutdown()
        async with self._gpu_lock:
            self._current_job = f"llm:{requested}"
            self._write_lock_file(self._current_job)
            try:
                await self.ensure_llm_running(requested)
                yield requested
            finally:
                self._current_job = "idle"
                self._remove_lock_file()
                self._schedule_llm_idle_shutdown()

    @asynccontextmanager
    async def _local_gpu_slot(self, job_name: str):
        """Reserve the GPU for a local generation task and temporarily stop llama-server."""
        if not self.enabled:
            yield
            return

        self._cancel_llm_idle_shutdown()
        async with self._gpu_lock:
            self._current_job = job_name
            self._write_lock_file(self._current_job)
            llm_was_active = False
            active_profile = self.active_llm_profile()
            try:
                llm_was_active = await self.stop_llm()
                yield
            finally:
                try:
                    if (
                        not settings.llm_on_demand
                        and settings.llm_restart_after_image
                        and llm_was_active
                    ):
                        self._current_job = "llm:restart"
                        self._write_lock_file(self._current_job)
                        await self.ensure_llm_running(active_profile)
                finally:
                    self._current_job = "idle"
                    self._remove_lock_file()

    @asynccontextmanager
    async def local_image_slot(self, profile: str):
        if not self.enabled:
            yield
            return

        current = asyncio.current_task()
        if self._image_batch_owner is current:
            yield
            return

        async with self._local_gpu_slot(f"image:{profile}"):
            yield

    @asynccontextmanager
    async def local_motion_slot(self):
        async with self._local_gpu_slot("motion:image-to-video"):
            yield

    @asynccontextmanager
    async def local_image_batch(self, profile: str):
        if not self.enabled:
            yield
            return

        self._cancel_llm_idle_shutdown()
        async with self._gpu_lock:
            self._image_batch_owner = asyncio.current_task()
            self._image_batch_profile = profile
            self._current_job = f"image-batch:{profile}"
            self._write_lock_file(self._current_job)
            llm_was_active = False
            active_profile = self.active_llm_profile()
            try:
                llm_was_active = await self.stop_llm()
                yield
            finally:
                try:
                    if (
                        not settings.llm_on_demand
                        and settings.llm_restart_after_image
                        and llm_was_active
                    ):
                        self._current_job = "llm:restart"
                        self._write_lock_file(self._current_job)
                        await self.ensure_llm_running(active_profile)
                finally:
                    self._image_batch_owner = None
                    self._image_batch_profile = None
                    self._current_job = "idle"
                    self._remove_lock_file()

    async def status(self) -> dict:
        active_profile = self.active_llm_profile()
        llm_active = await self.llm_active() if self.enabled else None
        idle_task_pending = bool(self._llm_idle_task and not self._llm_idle_task.done())
        active_config = self.profile_config(active_profile) if active_profile else None
        return {
            "enabled": self.enabled,
            "launch_mode": settings.llm_launch_mode,
            "llm_active": llm_active,
            "active_profile": active_profile,
            "active_model": active_config["model_name"] if active_config else None,
            "profiles": {
                "fast": {
                    "model_name": settings.llm_fast_model_name,
                    "model_path": str(settings.llm_fast_model_path),
                },
                "quality": {
                    "model_name": settings.llm_quality_model_name,
                    "model_path": str(settings.llm_quality_model_path),
                },
            },
            "defaults": {
                "storyboard": settings.llm_profile_storyboard,
                "visual_prompt": settings.llm_profile_visual_prompt,
                "rewrite": settings.llm_profile_rewrite,
            },
            "llm_on_demand": settings.llm_on_demand,
            "llm_idle_timeout_seconds": settings.llm_idle_timeout_seconds,
            "llm_idle_shutdown_pending": idle_task_pending,
            "gpu_busy": self._gpu_lock.locked(),
            "current_job": self._current_job,
            "image_batch_profile": self._image_batch_profile,
            "state_file": str(settings.llm_state_file),
            "log_file": str(settings.llm_log_file),
            "lock_file": str(settings.gpu_lock_file),
        }


model_orchestrator = ModelOrchestrator()
