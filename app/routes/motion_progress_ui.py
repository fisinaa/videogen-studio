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
    provider_select: 'Выбираю video provider',
    model_start: 'Запускаю image-to-video',
    generating: 'Генерирую видео',
    finalizing: 'Собираю MP4',
    llm_restored: 'Возвращаю LLM',
    complete: 'Готово',
    error: 'Ошибка',
  };

  function sceneElementFromButton(button) {
    return button?.closest?.('.scene-editor') || null;
  }

  function ensureProviderSelectors() {
    document.querySelectorAll('.scene-editor').forEach(sceneEl => {
      const panel = sceneEl.querySelector('.videogen-motion-panel');
      if (!panel || panel.querySelector('.motion-provider-select')) return;

      const controls = document.createElement('div');
      controls.className = 'motion-provider-controls';
      controls.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:9px 0 4px;';

      const label = document.createElement('span');
      label.className = 'muted';
      label.textContent = 'Генератор:';

      const select = document.createElement('select');
      select.className = 'motion-provider-select';
      select.title = 'Выбор image-to-video provider';
      select.style.cssText = 'width:auto;min-width:210px;margin:0;padding:8px 30px 8px 10px;background:#0d1016;color:#fff;border:1px solid #4c3828;border-radius:9px;';
      select.innerHTML = `
        <option value="openai">Sora / OpenAI — облако</option>
        <option value="wan">Wan Local GPU — локально</option>
        <option value="auto">Auto — OpenMontage выбирает</option>
      `;
      select.value = 'openai';

      const hint = document.createElement('span');
      hint.className = 'muted motion-provider-hint';
      hint.style.fontSize = '12px';
      hint.textContent = 'Sora не использует локальную GPU.';

      select.addEventListener('change', () => {
        hint.textContent = select.value === 'wan'
          ? 'Wan генерирует локально и нагрузит GPU/CPU/RAM.'
          : select.value === 'openai'
            ? 'Sora не использует локальную GPU.'
            : 'OpenMontage выберет доступный provider автоматически.';
      });

      controls.append(label, select, hint);
      const state = panel.querySelector('.motion-state');
      if (state) state.insertAdjacentElement('beforebegin', controls);
      else panel.prepend(controls);
    });
  }

  function providerForGenerateUrl(url) {
    const text = typeof url === 'string' ? url : (url?.url || '');
    const match = text.match(/\/api\/motion\/projects\/[^/]+\/scenes\/([^/?]+)\/generate(?:\?|$)/);
    if (!match) return null;
    const sceneId = decodeURIComponent(match[1]);
    const sceneEl = [...document.querySelectorAll('.scene-editor')]
      .find(el => el.dataset.sceneId === sceneId);
    return sceneEl?.querySelector('.motion-provider-select')?.value || 'openai';
  }

  // Existing motion UI owns the generate request. Add the selected provider to
  // that request here so we do not duplicate the motion controls or remove Sora.
  const nativeFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    const provider = providerForGenerateUrl(input);
    if (!provider) return nativeFetch(input, init);

    const raw = typeof input === 'string' ? input : input.url;
    const url = new URL(raw, location.origin);
    url.searchParams.set('preferred_provider', provider);
    const rewritten = url.pathname + url.search + url.hash;
    return nativeFetch(rewritten, init);
  };

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
    const provider = runtime.selected_provider ? ` · ${runtime.selected_provider}` : '';
    const tool = runtime.selected_tool ? ` / ${runtime.selected_tool}` : '';
    const detail = runtime.detail ? `<div style="margin-top:4px">${runtime.detail}</div>` : '';
    const error = runtime.error ? `<div style="margin-top:5px;color:#ff8d8d;white-space:pre-wrap">${runtime.error}</div>` : '';

    state.innerHTML = `<b>${stage}</b> · ${elapsed} сек${provider}${tool}${pid}${detail}${error}`;
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

  const providerObserver = new MutationObserver(ensureProviderSelectors);
  providerObserver.observe(document.documentElement, {childList:true, subtree:true});
  ensureProviderSelectors();
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
