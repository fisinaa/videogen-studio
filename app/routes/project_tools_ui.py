from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-project-tools.js")
async def videogen_project_tools_js():
    script = r'''
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

  function projectId() {
    return new URL(location.href).searchParams.get('project') || document.querySelector('.recent-project.active')?.dataset?.projectId || null;
  }

  function sceneIds() {
    return [...document.querySelectorAll('.scene-editor[data-scene-id]')].map(x => x.dataset.sceneId).filter(Boolean);
  }

  async function jsonFetch(url, options={}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  function ensureModal() {
    let overlay = document.querySelector('.videogen-media-manager-overlay');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.className = 'videogen-media-manager-overlay';
    overlay.style.cssText = 'display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.76);padding:4vh 4vw;overflow:auto;';
    overlay.innerHTML = `
      <div style="max-width:1200px;margin:auto;background:#0d1016;border:1px solid #343b49;border-radius:14px;padding:16px;color:#fff;box-shadow:0 20px 60px rgba(0,0,0,.5)">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
          <div><b style="font-size:18px">Media Manager</b><div class="muted" style="font-size:12px;margin-top:3px">Физические файлы проекта: изображения, видео, аудио и рендеры.</div></div>
          <button type="button" class="media-manager-close secondary">Закрыть</button>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:13px">
          <input type="file" class="media-manager-upload-input" accept="image/*,video/*,audio/*" style="max-width:360px">
          <button type="button" class="media-manager-upload">Загрузить в проект</button>
          <button type="button" class="media-manager-refresh secondary">Обновить</button>
          <span class="media-manager-state muted"></span>
        </div>
        <div class="media-manager-list" style="margin-top:14px"></div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('.media-manager-close').onclick = () => { overlay.style.display = 'none'; };
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.style.display = 'none'; });
    overlay.querySelector('.media-manager-refresh').onclick = () => loadLibrary(overlay);
    overlay.querySelector('.media-manager-upload').onclick = () => uploadLibraryFile(overlay);
    return overlay;
  }

  function preview(item) {
    if (item.kind === 'image') return `<img src="${esc(item.url)}" style="width:120px;height:72px;object-fit:cover;border-radius:7px;background:#000">`;
    if (item.kind === 'video' || item.kind === 'render') return `<video src="${esc(item.url)}" controls preload="metadata" style="width:160px;height:90px;object-fit:cover;border-radius:7px;background:#000"></video>`;
    if (item.kind === 'audio') return `<audio src="${esc(item.url)}" controls preload="metadata" style="width:260px"></audio>`;
    return '';
  }

  function roleOptions(item) {
    if (item.kind === 'audio') return '<option value="audio">Озвучка сцены</option>';
    if (item.kind === 'video') return '<option value="media">Основное видео</option><option value="motion">AI Motion</option>';
    if (item.kind === 'image') return '<option value="media">Изображение сцены</option>';
    return '';
  }

  async function loadLibrary(overlay) {
    const id = projectId();
    if (!id) return;
    const list = overlay.querySelector('.media-manager-list');
    const state = overlay.querySelector('.media-manager-state');
    state.textContent = 'Загружаю...';
    try {
      const data = await jsonFetch(`/api/media-library/projects/${encodeURIComponent(id)}`, {cache:'no-store'});
      const scenes = sceneIds();
      const sceneOptions = scenes.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
      list.innerHTML = data.items.length ? data.items.map(item => `
        <div class="media-library-item" data-filename="${esc(item.filename)}" data-kind="${esc(item.kind)}" style="display:grid;grid-template-columns:minmax(160px,280px) 1fr;gap:12px;padding:11px;margin:8px 0;border:1px solid #282f3b;border-radius:10px;background:#11151d">
          <div>${preview(item)}</div>
          <div>
            <div style="font-weight:700;word-break:break-all">${esc(item.filename)}</div>
            <div class="muted" style="font-size:12px;margin-top:3px">${esc(item.kind)} · ${(Number(item.size||0)/1024/1024).toFixed(2)} MB</div>
            <div style="font-size:12px;margin-top:5px;color:${item.in_use ? '#f3d37a' : '#8ee59a'}">${item.in_use ? 'Используется: ' + esc(item.usages.join(', ')) : 'Не используется'}</div>
            ${item.kind !== 'render' ? `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:9px"><select class="library-scene" style="width:auto;margin:0">${sceneOptions}</select><select class="library-role" style="width:auto;margin:0">${roleOptions(item)}</select><button type="button" class="library-assign" style="padding:7px 9px">Назначить</button><button type="button" class="library-delete danger" style="padding:7px 9px">Удалить файл</button></div>` : ''}
          </div>
        </div>`).join('') : '<div class="muted">В проекте пока нет файлов.</div>';

      list.querySelectorAll('.library-assign').forEach(btn => btn.onclick = async () => {
        const card = btn.closest('.media-library-item');
        const scene = card.querySelector('.library-scene').value;
        const role = card.querySelector('.library-role').value;
        btn.disabled = true;
        try {
          await jsonFetch(`/api/media-library/projects/${encodeURIComponent(id)}/assign/${encodeURIComponent(scene)}/${encodeURIComponent(card.dataset.filename)}?role=${encodeURIComponent(role)}`, {method:'POST'});
          state.textContent = `${card.dataset.filename} → ${scene}`;
          await loadLibrary(overlay);
          location.reload();
        } catch (e) { state.textContent = `Ошибка: ${e.message}`; btn.disabled = false; }
      });

      list.querySelectorAll('.library-delete').forEach(btn => btn.onclick = async () => {
        const card = btn.closest('.media-library-item');
        const filename = card.dataset.filename;
        if (!confirm(`Удалить ${filename} физически из проекта? Все ссылки на файл будут очищены.`)) return;
        btn.disabled = true;
        try {
          await jsonFetch(`/api/projects/${encodeURIComponent(id)}/files/${encodeURIComponent(filename)}`, {method:'DELETE'});
          state.textContent = `Удалён: ${filename}`;
          await loadLibrary(overlay);
        } catch (e) { state.textContent = `Ошибка: ${e.message}`; btn.disabled = false; }
      });
      state.textContent = `${data.items.length} файлов`;
    } catch (e) {
      state.textContent = `Ошибка: ${e.message}`;
      list.innerHTML = '';
    }
  }

  async function uploadLibraryFile(overlay) {
    const id = projectId();
    const input = overlay.querySelector('.media-manager-upload-input');
    const state = overlay.querySelector('.media-manager-state');
    const file = input.files?.[0];
    if (!id || !file) { state.textContent = 'Выбери файл.'; return; }
    const form = new FormData();
    form.append('file', file);
    state.textContent = `Загрузка ${file.name}...`;
    try {
      await jsonFetch(`/api/media-library/projects/${encodeURIComponent(id)}/upload`, {method:'POST', body:form});
      input.value = '';
      await loadLibrary(overlay);
    } catch (e) { state.textContent = `Ошибка: ${e.message}`; }
  }

  async function openMediaManager() {
    const overlay = ensureModal();
    overlay.style.display = 'block';
    await loadLibrary(overlay);
  }

  async function animateAll(panel) {
    const id = projectId();
    const provider = panel.querySelector('.project-motion-provider').value;
    const state = panel.querySelector('.project-production-state');
    const button = panel.querySelector('.project-animate-all');
    button.disabled = true;
    state.textContent = `Анимация всех сцен через ${provider}...`;
    try {
      const data = await jsonFetch(`/api/pipeline/projects/${encodeURIComponent(id)}/animate-all?preferred_provider=${encodeURIComponent(provider)}&only_missing=true`, {method:'POST'});
      state.textContent = `Motion: готово ${data.generated}, пропущено ${data.skipped}, ошибок ${data.errors.length}.`;
      if (data.errors.length) console.warn('VideoGen motion batch errors', data.errors);
      setTimeout(() => location.reload(), 800);
    } catch (e) { state.textContent = `Ошибка: ${e.message}`; }
    finally { button.disabled = false; }
  }

  async function buildEpisode(panel) {
    const id = projectId();
    const imageProvider = panel.querySelector('.project-image-provider').value;
    const motionProvider = panel.querySelector('.project-motion-provider').value;
    const state = panel.querySelector('.project-production-state');
    const button = panel.querySelector('.project-build-episode');
    button.disabled = true;
    state.textContent = 'Проверяю и достраиваю недостающие assets...';
    try {
      const data = await jsonFetch(`/api/pipeline/projects/${encodeURIComponent(id)}/build?image_provider=${encodeURIComponent(imageProvider)}&motion_provider=${encodeURIComponent(motionProvider)}`, {method:'POST'});
      state.textContent = `Картинки ${data.images_generated}, аудио ${data.audio_generated}, motion ${data.motion_generated}, ошибок ${data.errors.length}. Открываю Studio...`;
      if (data.errors.length) console.warn('VideoGen build errors', data.errors);
      const studio = await jsonFetch(`/api/openmontage/projects/${encodeURIComponent(id)}/studio`, {method:'POST'});
      if (studio.url) window.open(studio.url, '_blank', 'noopener');
    } catch (e) { state.textContent = `Ошибка: ${e.message}`; }
    finally { button.disabled = false; }
  }

  function ensureProjectTools() {
    const actions = document.querySelector('.project-actions');
    if (!actions || document.querySelector('.videogen-project-production')) return;
    const panel = document.createElement('div');
    panel.className = 'videogen-project-production';
    panel.style.cssText = 'margin:14px 0;padding:14px;border:1px solid #304057;border-radius:12px;background:#0b1119;';
    panel.innerHTML = `
      <b>Production Pipeline</b>
      <div class="muted" style="margin-top:5px">Медиатека проекта, массовая анимация и сборка эпизода.</div>
      <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:10px">
        <button type="button" class="project-media-manager secondary">Media Manager</button>
        <select class="project-image-provider" style="width:auto;margin:0"><option value="local_next">Image: Local Next</option><option value="local_quality">Image: Local Quality</option><option value="local_fast">Image: Local Fast</option><option value="openai">Image: OpenAI</option></select>
        <select class="project-motion-provider" style="width:auto;margin:0"><option value="openai">Motion: Sora / OpenAI</option><option value="wan">Motion: Wan Local GPU</option><option value="auto">Motion: Auto</option></select>
        <button type="button" class="project-animate-all" style="background:#8b4f27">Анимировать все сцены</button>
        <button type="button" class="project-build-episode" style="background:#2d7651">Собрать эпизод</button>
      </div>
      <div class="project-production-state muted" style="margin-top:8px">Готово к работе.</div>`;
    actions.parentElement?.insertBefore(panel, actions.nextSibling);
    panel.querySelector('.project-media-manager').onclick = openMediaManager;
    panel.querySelector('.project-animate-all').onclick = () => animateAll(panel);
    panel.querySelector('.project-build-episode').onclick = () => buildEpisode(panel);
  }

  const observer = new MutationObserver(ensureProjectTools);
  observer.observe(document.documentElement, {childList:true, subtree:true});
  ensureProjectTools();
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
