from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-enhancements.js")
async def videogen_enhancements_js():
    script = r'''
(() => {
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

  function projectId() {
    const fromUrl = new URL(location.href).searchParams.get('project');
    if (fromUrl) return fromUrl;
    const active = document.querySelector('.recent-project.active');
    return active?.dataset?.projectId || null;
  }

  function ensurePanel() {
    const actions = document.querySelector('.project-actions');
    if (!actions || actions.querySelector('.render-hyperframes')) return;

    const hf = document.createElement('button');
    hf.type = 'button';
    hf.className = 'render-hyperframes';
    hf.textContent = 'Рендер HyperFrames';
    hf.style.background = '#7b3f98';

    const remotion = document.createElement('button');
    remotion.type = 'button';
    remotion.className = 'render-remotion';
    remotion.textContent = 'Рендер Remotion';
    remotion.style.background = '#3b5f9b';

    const wrap = document.createElement('div');
    wrap.className = 'videogen-render-panel';
    wrap.style.cssText = 'margin:14px 0;padding:14px;border:1px solid #303646;border-radius:12px;background:#0d1016;';
    wrap.innerHTML = '<b>Финальный рендер</b><div class="render-state muted" style="margin-top:8px">Готов к запуску.</div><div class="render-output" style="margin-top:10px"></div>';

    actions.append(hf, remotion);
    actions.parentElement?.appendChild(wrap);

    hf.addEventListener('click', () => runRender('hyperframes', hf, remotion, wrap));
    remotion.addEventListener('click', () => runRender('remotion', hf, remotion, wrap));
    loadExisting(wrap);
  }

  async function loadExisting(panel) {
    const id = projectId();
    if (!id) return;
    try {
      const response = await fetch(`/api/openmontage/projects/${encodeURIComponent(id)}/renders`, {cache:'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      const first = data.renders?.[0];
      if (first) showVideo(panel, first.download_url, first.filename);
    } catch (_) {}
  }

  function showVideo(panel, url, filename) {
    const output = panel.querySelector('.render-output');
    const stamp = `${url}${url.includes('?') ? '&' : '?'}_=${Date.now()}`;
    output.innerHTML = `<video controls preload="metadata" src="${esc(stamp)}" style="width:100%;max-width:760px;border-radius:10px;background:#000"></video><div style="margin-top:8px"><a href="${esc(url)}" target="_blank" style="color:#bdafff">${esc(filename || 'Открыть MP4')}</a></div>`;
  }

  async function runRender(runtime, button, otherButton, panel) {
    const id = projectId();
    const state = panel.querySelector('.render-state');
    if (!id) {
      state.textContent = 'Не удалось определить ID проекта.';
      state.style.color = '#ff8d8d';
      return;
    }

    button.disabled = true;
    otherButton.disabled = true;
    state.style.color = '';
    state.textContent = 'Синхронизация таймингов по озвучке...';
    try {
      const sync = await fetch(`/api/production/projects/${encodeURIComponent(id)}/sync`, {method:'POST'});
      if (!sync.ok) {
        const err = await sync.json().catch(() => ({}));
        throw new Error(err.detail || `sync HTTP ${sync.status}`);
      }

      state.textContent = `Рендер ${runtime} запущен. Это может занять несколько минут...`;
      const response = await fetch(`/api/openmontage/projects/${encodeURIComponent(id)}/render?runtime=${encodeURIComponent(runtime)}`, {method:'POST'});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `render HTTP ${response.status}`);

      state.textContent = `Готово: ${data.filename || 'MP4'}`;
      state.style.color = '#8ee59a';
      if (data.download_url) showVideo(panel, data.download_url, data.filename);
    } catch (error) {
      state.textContent = `Ошибка рендера: ${error.message || error}`;
      state.style.color = '#ff8d8d';
    } finally {
      button.disabled = false;
      otherButton.disabled = false;
    }
  }

  const observer = new MutationObserver(ensurePanel);
  observer.observe(document.documentElement, {childList:true, subtree:true});
  ensurePanel();
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
