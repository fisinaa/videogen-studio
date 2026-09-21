from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-motion-progress.js")
async def videogen_motion_progress_js():
    script = r'''
(() => {
  const STAGE_LABELS = {
    idle: 'Ожидание',
    queued: 'Ожидание GPU',
    gpu_prepare: 'Освобождаю GPU',
    model_start: 'Запускаю image-to-video',
    generating: 'Генерирую кадры',
    finalizing: 'Собираю MP4',
    llm_restored: 'Возвращаю LLM',
    complete: 'Готово',
    error: 'Ошибка',
  };

  function sceneElementFromButton(button) {
    return button?.closest?.('.scene-editor') || null;
  }

  async function getStatus() {
    const response = await fetch('/api/motion/status', {cache: 'no-store'});
    if (!response.ok) throw new Error(`status HTTP ${response.status}`);
    return response.json();
  }

  function renderRuntime(sceneEl, runtime) {
    if (!sceneEl || !runtime) return;
    const state = sceneEl.querySelector('.videogen-motion-panel .motion-state');
    if (!state) return;

    const elapsed = Number(runtime.elapsed_seconds || 0).toFixed(1);
    const stage = STAGE_LABELS[runtime.stage] || runtime.stage || 'Motion';
    const pid = runtime.pid ? ` · PID ${runtime.pid}` : '';
    const detail = runtime.detail ? `<div style="margin-top:4px">${runtime.detail}</div>` : '';
    const error = runtime.error ? `<div style="margin-top:5px;color:#ff8d8d;white-space:pre-wrap">${runtime.error}</div>` : '';

    state.innerHTML = `<b>${stage}</b> · ${elapsed} сек${pid}${detail}${error}`;
    state.style.color = runtime.state === 'error' ? '#ff8d8d' : (runtime.state === 'complete' ? '#8ee59a' : '#d8cfff');
  }

  function startPolling(sceneEl) {
    let stopped = false;
    let timer = null;

    const stop = () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };

    const tick = async () => {
      if (stopped || !document.body.contains(sceneEl)) return;
      try {
        const status = await getStatus();
        const runtime = status.runtime || {};
        const sceneId = sceneEl.dataset.sceneId;
        if (!runtime.scene_id || runtime.scene_id === sceneId) {
          renderRuntime(sceneEl, runtime);
        }
        if (runtime.state === 'complete' || runtime.state === 'error') {
          setTimeout(stop, 2500);
          return;
        }
      } catch (_) {
        // Main request will surface the actual error; polling is only observability.
      }
      timer = setTimeout(tick, 750);
    };

    tick();
    return stop;
  }

  document.addEventListener('click', event => {
    const button = event.target.closest?.('.motion-generate');
    if (!button) return;
    const sceneEl = sceneElementFromButton(button);
    if (!sceneEl) return;
    startPolling(sceneEl);
  }, true);
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
