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

  function enhanceImageModelMenus() {
    document.querySelectorAll('.scene-editor').forEach(scene => {
      if (scene.dataset.imageModelMenu === '1') return;

      const actions = scene.querySelector('.scene-actions');
      const fast = actions?.querySelector('.generate-fast');
      const quality = actions?.querySelector('.generate-quality');
      const next = actions?.querySelector('.generate-next');
      const openai = actions?.querySelector('.generate-openai');
      if (!actions || !fast || !quality || !next || !openai) return;

      scene.dataset.imageModelMenu = '1';
      [fast, quality, next, openai].forEach(button => { button.style.display = 'none'; });

      const group = document.createElement('span');
      group.className = 'image-model-menu';
      group.style.cssText = 'display:inline-flex;gap:6px;align-items:center;';

      const select = document.createElement('select');
      select.className = 'image-model-select';
      select.title = 'Модель генерации изображения';
      select.style.cssText = 'width:auto;min-width:150px;margin:0;padding:10px 30px 10px 10px;background:#0d1016;color:#fff;border:1px solid #303646;border-radius:10px;';
      select.innerHTML = `
        <option value="fast">Local Fast</option>
        <option value="quality" ${quality.disabled ? 'disabled' : ''}>Local Quality${quality.disabled ? ' — недоступна' : ''}</option>
        <option value="next" ${next.disabled ? 'disabled' : ''}>Local Next${next.disabled ? ' — недоступна' : ''}</option>
        <option value="openai">OpenAI</option>
      `;
      if (!next.disabled) select.value = 'next';

      const generate = document.createElement('button');
      generate.type = 'button';
      generate.className = 'image-model-generate';
      generate.textContent = 'Сгенерировать картинку';
      generate.style.background = '#2d7651';
      generate.addEventListener('click', () => {
        const target = {fast, quality, next, openai}[select.value];
        if (target && !target.disabled) target.click();
      });

      group.append(select, generate);
      actions.insertBefore(group, fast);
    });
  }

  async function fetchMotionState(sceneId) {
    const id = projectId();
    if (!id) throw new Error('Не удалось определить ID проекта');
    const response = await fetch(`/api/motion/projects/${encodeURIComponent(id)}/scenes/${encodeURIComponent(sceneId)}`, {cache:'no-store'});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `motion HTTP ${response.status}`);
    return data;
  }

  function motionCandidateHtml(asset, selectedId) {
    const selected = asset.asset_id === selectedId;
    const url = asset.preview_url || asset.download_url || '';
    return `<div class="motion-candidate" data-motion-id="${esc(asset.asset_id)}" style="border:1px solid ${selected ? '#58a66b' : '#2a3140'};border-radius:10px;padding:9px;background:#0b0d12">
      ${selected ? '<div style="font-size:11px;color:#8ee59a;font-weight:700;margin-bottom:6px">ВЫБРАНО</div>' : ''}
      ${url ? `<video controls preload="metadata" src="${esc(url)}" style="width:100%;max-width:360px;border-radius:8px;background:#000"></video>` : ''}
      <div class="muted" style="font-size:12px;margin-top:6px">${esc(asset.label || asset.asset_id)}</div>
      <div style="display:flex;gap:6px;margin-top:7px">
        <button type="button" class="motion-select" ${selected ? 'disabled' : ''} style="padding:7px 9px">Выбрать</button>
        <button type="button" class="motion-delete danger" ${selected ? 'disabled' : ''} style="padding:7px 9px">Удалить</button>
      </div>
    </div>`;
  }

  async function hydrateMotionPanel(sceneEl) {
    const sceneId = sceneEl.dataset.sceneId;
    const panel = sceneEl.querySelector('.videogen-motion-panel');
    if (!sceneId || !panel) return;
    const stateEl = panel.querySelector('.motion-state');
    const listEl = panel.querySelector('.motion-list');
    const generateBtn = sceneEl.querySelector('.motion-generate');
    const resetBtn = sceneEl.querySelector('.motion-reset');
    if (!stateEl || !listEl || !generateBtn || !resetBtn) return;

    try {
      const data = await fetchMotionState(sceneId);
      const provider = data.provider || {};
      const selected = data.selected_motion_media;
      const candidates = data.motion_candidates || [];
      generateBtn.disabled = !provider.enabled;
      generateBtn.title = provider.enabled ? 'Создать настоящий image-to-video MP4' : (provider.note || 'Motion provider не настроен');
      resetBtn.disabled = data.motion_mode !== 'image_to_video';
      stateEl.style.color = '';
      stateEl.innerHTML = data.motion_mode === 'image_to_video' && selected
        ? `<span style="color:#8ee59a">AI motion активен:</span> ${esc(selected.asset_id)}`
        : `Режим: camera motion (pan/zoom). ${provider.enabled ? 'AI motion provider готов.' : esc(provider.note || '')}`;
      listEl.innerHTML = candidates.length
        ? `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:9px;margin-top:9px">${candidates.map(a => motionCandidateHtml(a, selected?.asset_id)).join('')}</div>`
        : '<div class="muted" style="margin-top:7px">Motion-клипов пока нет.</div>';

      listEl.querySelectorAll('.motion-select').forEach(button => button.addEventListener('click', async () => {
        const card = button.closest('.motion-candidate');
        if (!card) return;
        await motionAction(sceneEl, `/api/motion/projects/${encodeURIComponent(projectId())}/scenes/${encodeURIComponent(sceneId)}/select/${encodeURIComponent(card.dataset.motionId)}`, 'POST', 'Выбираю motion-клип...');
      }));
      listEl.querySelectorAll('.motion-delete').forEach(button => button.addEventListener('click', async () => {
        const card = button.closest('.motion-candidate');
        if (!card) return;
        await motionAction(sceneEl, `/api/motion/projects/${encodeURIComponent(projectId())}/scenes/${encodeURIComponent(sceneId)}/candidates/${encodeURIComponent(card.dataset.motionId)}`, 'DELETE', 'Удаляю motion-клип...');
      }));
    } catch (error) {
      stateEl.textContent = `Motion: ${error.message || error}`;
      stateEl.style.color = '#ff8d8d';
      generateBtn.disabled = true;
      resetBtn.disabled = true;
    }
  }

  async function motionAction(sceneEl, url, method, progressText) {
    const panel = sceneEl.querySelector('.videogen-motion-panel');
    if (!panel) return;
    const stateEl = panel.querySelector('.motion-state');
    const buttons = sceneEl.querySelectorAll('.motion-generate, .motion-reset, .motion-select, .motion-delete');
    buttons.forEach(b => { b.disabled = true; });
    if (stateEl) {
      stateEl.style.color = '';
      stateEl.textContent = progressText;
    }
    try {
      const response = await fetch(url, {method});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      if (stateEl) {
        stateEl.style.color = '#8ee59a';
        stateEl.textContent = 'Готово.';
      }
    } catch (error) {
      if (stateEl) {
        stateEl.style.color = '#ff8d8d';
        stateEl.textContent = `Ошибка: ${error.message || error}`;
      }
    } finally {
      await hydrateMotionPanel(sceneEl);
    }
  }

  function enhanceMotionControls() {
    document.querySelectorAll('.scene-editor').forEach(sceneEl => {
      if (sceneEl.dataset.motionUi === '1') return;
      const actions = sceneEl.querySelector('.scene-actions');
      const sceneId = sceneEl.dataset.sceneId;
      if (!actions || !sceneId) return;
      sceneEl.dataset.motionUi = '1';

      const generate = document.createElement('button');
      generate.type = 'button';
      generate.className = 'motion-generate';
      generate.textContent = 'Анимировать сцену';
      generate.style.background = '#8b4f27';

      const reset = document.createElement('button');
      reset.type = 'button';
      reset.className = 'motion-reset secondary';
      reset.textContent = 'Использовать картинку';

      actions.append(generate, reset);

      const panel = document.createElement('div');
      panel.className = 'videogen-motion-panel';
      panel.style.cssText = 'margin:12px 0;padding:11px;border:1px solid #4c3828;border-radius:10px;background:#15100c;';
      panel.innerHTML = '<b>AI Motion / image-to-video</b><div class="motion-state muted" style="margin-top:6px">Проверяю motion provider...</div><div class="motion-list"></div>';
      const selectedMedia = sceneEl.querySelector('.selected-media-slot');
      if (selectedMedia) selectedMedia.insertAdjacentElement('afterend', panel);
      else sceneEl.appendChild(panel);

      generate.addEventListener('click', async () => {
        await motionAction(
          sceneEl,
          `/api/motion/projects/${encodeURIComponent(projectId())}/scenes/${encodeURIComponent(sceneId)}/generate`,
          'POST',
          'Генерация AI motion MP4... На локальной GPU это может занять много времени.'
        );
      });
      reset.addEventListener('click', async () => {
        await motionAction(
          sceneEl,
          `/api/motion/projects/${encodeURIComponent(projectId())}/scenes/${encodeURIComponent(sceneId)}/reset`,
          'POST',
          'Возвращаю сцену на картинку + camera motion...'
        );
      });
      hydrateMotionPanel(sceneEl);
    });
  }

  function setRenderButtonState(button, hasExisting) {
    button.textContent = hasExisting ? 'Пересобрать видео' : 'Собрать видео';
    button.dataset.hasExisting = hasExisting ? '1' : '0';
  }

  function ensurePanel() {
    enhanceImageModelMenus();
    enhanceMotionControls();

    const actions = document.querySelector('.project-actions');
    if (!actions || actions.querySelector('.videogen-render-main')) return;

    const runtime = document.createElement('select');
    runtime.className = 'videogen-render-runtime';
    runtime.title = 'Движок финальной сборки';
    runtime.style.cssText = 'width:auto;min-width:230px;margin:0;padding:10px 30px 10px 10px;background:#0d1016;color:#fff;border:1px solid #303646;border-radius:10px;';
    runtime.innerHTML = `
      <option value="hyperframes">HyperFrames — основной</option>
      <option value="remotion">Remotion — альтернативный</option>
    `;

    const render = document.createElement('button');
    render.type = 'button';
    render.className = 'videogen-render-main';
    render.style.background = '#7b3f98';
    setRenderButtonState(render, false);

    const controls = document.createElement('span');
    controls.className = 'videogen-render-controls';
    controls.style.cssText = 'display:inline-flex;gap:7px;align-items:center;flex-wrap:wrap;';
    controls.append(runtime, render);

    const wrap = document.createElement('div');
    wrap.className = 'videogen-render-panel';
    wrap.style.cssText = 'margin:14px 0;padding:14px;border:1px solid #303646;border-radius:12px;background:#0d1016;';
    wrap.innerHTML = `
      <b>Финальное видео</b>
      <div class="muted" style="margin-top:6px;line-height:1.45">
        HyperFrames — основной монтаж: картинки/готовые motion-клипы, движение камеры, озвучка и субтитры.<br>
        Remotion — альтернативный движок композиции; сам по себе персонажей на картинке не оживляет.
      </div>
      <div class="render-state muted" style="margin-top:8px">Готов к сборке.</div>
      <div class="render-output" style="margin-top:10px"></div>
    `;

    actions.append(controls);
    actions.parentElement?.appendChild(wrap);

    render.addEventListener('click', () => runRender(runtime.value, render, runtime, wrap));
    runtime.addEventListener('change', () => loadExisting(wrap, render, runtime));
    loadExisting(wrap, render, runtime);
  }

  async function loadExisting(panel, button, runtimeSelect) {
    const id = projectId();
    if (!id) return;
    try {
      const response = await fetch(`/api/openmontage/projects/${encodeURIComponent(id)}/renders`, {cache:'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      const renders = data.renders || [];
      const chosenRuntime = runtimeSelect.value;
      const chosen = renders.find(item => String(item.filename || '').includes(chosenRuntime));
      setRenderButtonState(button, Boolean(chosen));
      if (chosen) {
        panel.querySelector('.render-state').textContent = 'Есть готовая версия. После изменений нажми «Пересобрать видео».';
        showVideo(panel, chosen.download_url, chosen.filename);
      } else {
        panel.querySelector('.render-state').textContent = 'Для выбранного движка готового рендера пока нет.';
      }
    } catch (_) {}
  }

  function showVideo(panel, url, filename) {
    const output = panel.querySelector('.render-output');
    const stamp = `${url}${url.includes('?') ? '&' : '?'}_=${Date.now()}`;
    output.innerHTML = `<video controls preload="metadata" src="${esc(stamp)}" style="width:100%;max-width:760px;border-radius:10px;background:#000"></video><div style="margin-top:8px"><a href="${esc(url)}" target="_blank" style="color:#bdafff">${esc(filename || 'Открыть MP4')}</a></div>`;
  }

  async function runRender(runtime, button, runtimeSelect, panel) {
    const id = projectId();
    const state = panel.querySelector('.render-state');
    if (!id) {
      state.textContent = 'Не удалось определить ID проекта.';
      state.style.color = '#ff8d8d';
      return;
    }

    const isRerender = button.dataset.hasExisting === '1';
    button.disabled = true;
    runtimeSelect.disabled = true;
    state.style.color = '';
    state.textContent = isRerender
      ? 'Синхронизация изменений перед пересборкой...'
      : 'Синхронизация таймингов по озвучке...';
    try {
      const sync = await fetch(`/api/production/projects/${encodeURIComponent(id)}/sync`, {method:'POST'});
      if (!sync.ok) {
        const err = await sync.json().catch(() => ({}));
        throw new Error(err.detail || `sync HTTP ${sync.status}`);
      }

      state.textContent = `${isRerender ? 'Пересборка' : 'Сборка'} через ${runtime} запущена. Это может занять несколько минут...`;
      const response = await fetch(`/api/openmontage/projects/${encodeURIComponent(id)}/render?runtime=${encodeURIComponent(runtime)}`, {method:'POST'});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `render HTTP ${response.status}`);

      state.textContent = `Готово: ${data.filename || 'MP4'}`;
      state.style.color = '#8ee59a';
      setRenderButtonState(button, true);
      if (data.download_url) showVideo(panel, data.download_url, data.filename);
    } catch (error) {
      state.textContent = `Ошибка рендера: ${error.message || error}`;
      state.style.color = '#ff8d8d';
    } finally {
      button.disabled = false;
      runtimeSelect.disabled = false;
    }
  }

  const observer = new MutationObserver(() => {
    ensurePanel();
    enhanceImageModelMenus();
    enhanceMotionControls();
  });
  observer.observe(document.documentElement, {childList:true, subtree:true});
  ensurePanel();
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
