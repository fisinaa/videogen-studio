from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx

from app.config import settings


class ModelOrchestrator:
    """Coordinate llama-server and local image generation on one GPU."""

    def __init__(self) -> None:
        self._gpu_lock = asyncio.Lock()
        self._current_job = "idle"

    @property
    def enabled(self) -> bool:
        return settings.model_orchestration_enabled

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

    async def llm_active(self) -> bool:
        if not self.enabled:
            return True
        code, _ = await self._systemctl("is-active")
        return code == 0

    async def _wait_llm_http(self, timeout_seconds: int) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        url = settings.llm_base_url.rstrip("/") + "/models"
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(url)
                if response.status_code < 500:
                    return
            except Exception:
                pass
            await asyncio.sleep(1.0)
        raise RuntimeError(f"llama-server did not become ready within {timeout_seconds}s")

    async def ensure_llm_running(self) -> None:
        if not self.enabled:
            return

        # If an image job currently owns the GPU, an incoming LLM request waits
        # until the image finishes and the orchestrator has restarted llama-server.
        if self._gpu_lock.locked() and self._current_job.startswith("image:"):
            async with self._gpu_lock:
                pass

        if await self.llm_active():
            try:
                await self._wait_llm_http(3)
                return
            except RuntimeError:
                pass
        code, detail = await self._systemctl("start")
        if code != 0:
            raise RuntimeError(
                f"Cannot start {settings.llm_systemd_unit}: {detail or 'systemctl failed'}"
            )
        await self._wait_llm_http(settings.llm_start_timeout_seconds)

    async def stop_llm(self) -> bool:
        if not self.enabled:
            return False
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
    async def llm_slot(self):
        if not self.enabled:
            yield
            return
        async with self._gpu_lock:
            self._current_job = "llm"
            self._write_lock_file(self._current_job)
            try:
                await self.ensure_llm_running()
                yield
            finally:
                self._current_job = "idle"
                self._remove_lock_file()

    @asynccontextmanager
    async def local_image_slot(self, profile: str):
        if not self.enabled:
            yield
            return
        async with self._gpu_lock:
            self._current_job = f"image:{profile}"
            self._write_lock_file(self._current_job)
            llm_was_active = False
            try:
                llm_was_active = await self.stop_llm()
                yield
            finally:
                try:
                    if settings.llm_restart_after_image and llm_was_active:
                        self._current_job = "llm:restart"
                        self._write_lock_file(self._current_job)
                        await self.ensure_llm_running()
                finally:
                    self._current_job = "idle"
                    self._remove_lock_file()

    async def status(self) -> dict:
        llm_active = await self.llm_active() if self.enabled else None
        return {
            "enabled": self.enabled,
            "llm_systemd_unit": settings.llm_systemd_unit,
            "llm_active": llm_active,
            "gpu_busy": self._gpu_lock.locked(),
            "current_job": self._current_job,
            "lock_file": str(settings.gpu_lock_file),
        }


model_orchestrator = ModelOrchestrator()
