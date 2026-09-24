from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-openmontage-popup-fix.js")
async def videogen_openmontage_popup_fix_js():
    script = r'''
(() => {
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

  function projectId() {
    const fromUrl = new URL(location.href).searchParams.get('project');
    if (fromUrl) return fromUrl;
    const active = document.querySelector('.recent-project.active');
    return active?.dataset?.projectId || null;
  }

  function renderStateFor(button) {
    return button.closest('.videogen-render-controls')?.parentElement?.querySelector('.render-state')
      || document.querySelector('.render-state');
  }

  document.addEventListener('click', async (event) => {
    const button = event.target.closest?.('.videogen-open-studio');
    if (!button) return;

    // The old handler called window.open() only after awaiting the backend request.
    // Browsers treat that as a non-user-initiated popup and may block it silently.
    // Open an empty tab synchronously while we are still inside the click event,
    // then navigate that tab once OpenMontage has finished starting.
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const id = projectId();
    const state = renderStateFor(button);
    if (!id) {
      if (state) {
        state.textContent = 'Ошибка OpenMontage Studio: не удалось определить ID проекта';
        state.style.color = '#ff8d8d';
      }
      return;
    }

    let tab = null;
    try {
      tab = window.open('about:blank', '_blank');
      if (tab) {
        tab.document.title = 'OpenMontage запускается...';
        tab.document.body.innerHTML = '<div style="font-family:system-ui;padding:24px">Запускаю OpenMontage Studio...</div>';
      }
    } catch (_) {
      tab = null;
    }

    button.disabled = true;
    if (state) {
      state.textContent = 'Подготавливаю OpenMontage / HyperFrames Studio...';
      state.style.color = '';
    }

    try {
      const response = await fetch(`/api/openmontage/projects/${encodeURIComponent(id)}/studio`, {method:'POST'});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `studio HTTP ${response.status}`);

      const host = location.hostname || '127.0.0.1';
      const url = data.url || `http://${host}:${data.port}${data.studio_path || '/'}`;

      if (tab && !tab.closed) {
        tab.location.replace(url);
      } else {
        // Popup is blocked. Keep an explicit clickable link instead of failing silently.
        if (state) {
          state.innerHTML = `OpenMontage Studio запущен. Браузер заблокировал новую вкладку — <a href="${esc(url)}" target="_blank" rel="noopener" style="color:#bdafff;font-weight:700">открыть Studio</a>`;
          state.style.color = '#f1d18a';
        }
        return;
      }

      if (state) {
        state.innerHTML = `OpenMontage Studio запущен: <a href="${esc(url)}" target="_blank" rel="noopener" style="color:#bdafff">${esc(url)}</a>`;
        state.style.color = '#8ee59a';
      }
    } catch (error) {
      if (tab && !tab.closed) tab.close();
      if (state) {
        state.textContent = `Ошибка OpenMontage Studio: ${error.message || error}`;
        state.style.color = '#ff8d8d';
      }
    } finally {
      button.disabled = false;
    }
  }, true);
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store, max-age=0"})
