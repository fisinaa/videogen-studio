from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-project-reference.js")
async def videogen_project_reference_js():
    script = r'''
(() => {
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  let activeHasReference = false;

  // Existing UI historically enabled Character Reference automatically only for
  // OpenAI. Keep the normal UI untouched, but when a project has an uploaded
  // reference make sure OpenAI and Local Next receive it for single and batch
  // image generation. Local Fast/Quality remain opt-in through the checkbox.
  const previousFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    if (activeHasReference && typeof input === 'string' && input.includes('/media/generate-')) {
      try {
        const url = new URL(input, location.origin);
        const provider = url.searchParams.get('provider');
        if (provider === 'openai' || provider === 'local_next') {
          url.searchParams.set('use_reference', 'true');
          input = url.pathname + url.search + url.hash;
        }
      } catch (_) {}
    }
    return previousFetch(input, init);
  };

  function projectId() {
    return new URL(location.href).searchParams.get('project') || document.querySelector('.recent-project.active')?.dataset?.projectId || '';
  }

  function modalCss() {
    if (document.getElementById('videogen-project-modal-css')) return;
    const style = document.createElement('style');
    style.id = 'videogen-project-modal-css';
    style.textContent = `
      .vg-modal-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:9998;display:flex;align-items:center;justify-content:center;padding:24px}
      .vg-modal{width:min(760px,96vw);max-height:92vh;overflow:auto;background:#12151d;border:1px solid #333b4d;border-radius:18px;padding:22px;box-shadow:0 24px 80px rgba(0,0,0,.5)}
      .vg-modal h2{margin:0 0 16px}.vg-modal .two{display:grid;grid-template-columns:1fr 1fr;gap:12px}.vg-modal .actions{display:flex;gap:8px;justify-content:flex-end;margin-top:14px}
      .vg-ref-preview{max-width:260px;aspect-ratio:1/1;object-fit:cover;border-radius:10px;margin:8px 0;border:1px solid #344158}
      @media(max-width:700px){.vg-modal .two{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);
  }

  function installNewProjectButton() {
    const form = document.getElementById('project-form');
    if (!form || form.dataset.interactiveProject === '1') return;
    form.dataset.interactiveProject = '1';
    form.style.display = 'none';

    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Новый проект';
    button.style.fontSize = '16px';
    button.style.padding = '13px 20px';
    form.parentElement?.insertBefore(button, form);
    button.addEventListener('click', openNewProjectModal);
  }

  async function openNewProjectModal() {
    modalCss();
    const wrap = document.createElement('div');
    wrap.className = 'vg-modal-backdrop';
    wrap.innerHTML = `
      <div class="vg-modal">
        <h2>Новый проект</h2>
        <label>Название проекта<input id="vg-name" maxlength="200" placeholder="Например: Девушка под дождём"></label>
        <label>Что происходит?<textarea id="vg-prompt" style="min-height:130px" placeholder="Например: девушка танцует под дождём в неоновом городе"></textarea></label>
        <div class="two">
          <label>Тип<select id="vg-type"><option value="cartoon">Мультфильм</option><option value="series">Сериал</option><option value="series_episode">Серия</option><option value="video">Видео</option><option value="reel">Reel</option></select></label>
          <label>Storyboard<select id="vg-provider"><option value="local">Local Quality — Qwen3 14B</option><option value="openai">OpenAI</option></select></label>
          <label>Формат<select id="vg-aspect"><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option></select></label>
          <label>Длительность, сек<input id="vg-duration" type="number" min="10" max="7200" value="180"></label>
        </div>
        <div style="margin-top:8px;padding:14px;border:1px solid #3a4a62;border-radius:12px;background:#0c131d">
          <b>Character Reference — опционально</b>
          <div class="muted" style="margin:5px 0 10px">Можно сразу приложить фото/арт персонажа. Для OpenAI Image и Local Next reference будет включаться автоматически.</div>
          <div class="two">
            <label>Имя персонажа<input id="vg-character-name" maxlength="200" placeholder="Лея"></label>
            <label>Фото / арт<input id="vg-reference" type="file" accept="image/jpeg,image/png,image/webp"></label>
          </div>
          <label>Инструкция к reference<textarea id="vg-character-prompt" style="min-height:80px" placeholder="Сохранять лицо, длинные тёмные волосы, красную куртку"></textarea></label>
          <img id="vg-reference-preview" class="vg-ref-preview" style="display:none">
        </div>
        <div id="vg-create-state" class="muted" style="margin-top:10px"></div>
        <div class="actions"><button type="button" class="secondary" id="vg-cancel">Отмена</button><button type="button" id="vg-create">Создать проект</button></div>
      </div>`;
    document.body.appendChild(wrap);

    const file = wrap.querySelector('#vg-reference');
    const preview = wrap.querySelector('#vg-reference-preview');
    file.addEventListener('change', () => {
      const f = file.files?.[0];
      if (!f) { preview.style.display = 'none'; return; }
      preview.src = URL.createObjectURL(f);
      preview.style.display = 'block';
    });

    wrap.querySelector('#vg-cancel').addEventListener('click', () => wrap.remove());
    wrap.addEventListener('click', e => { if (e.target === wrap) wrap.remove(); });
    wrap.querySelector('#vg-create').addEventListener('click', async () => {
      const create = wrap.querySelector('#vg-create');
      const state = wrap.querySelector('#vg-create-state');
      const prompt = wrap.querySelector('#vg-prompt').value.trim();
      if (prompt.length < 3) { state.textContent = 'Нужен prompt проекта.'; state.style.color = '#ff8d8d'; return; }

      const data = new FormData();
      data.append('project_name', wrap.querySelector('#vg-name').value.trim());
      data.append('prompt', prompt);
      data.append('project_type', wrap.querySelector('#vg-type').value);
      data.append('aspect_ratio', wrap.querySelector('#vg-aspect').value);
      data.append('duration_seconds', wrap.querySelector('#vg-duration').value || '180');
      data.append('language', 'ru');
      data.append('storyboard_provider', wrap.querySelector('#vg-provider').value);
      data.append('character_name', wrap.querySelector('#vg-character-name').value.trim());
      data.append('character_prompt', wrap.querySelector('#vg-character-prompt').value.trim());
      if (file.files?.[0]) data.append('reference_image', file.files[0]);

      create.disabled = true;
      state.style.color = '';
      state.textContent = 'Создаю storyboard и проект...';
      try {
        const response = await fetch('/api/projects/interactive', {method:'POST', body:data});
        const project = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(project.detail || `HTTP ${response.status}`);
        state.style.color = '#8ee59a';
        state.textContent = 'Проект создан.';
        location.href = `/?project=${encodeURIComponent(project.id)}`;
      } catch (error) {
        state.style.color = '#ff8d8d';
        state.textContent = `Ошибка: ${error.message || error}`;
        create.disabled = false;
      }
    });
  }

  async function enhanceCharacterReference() {
    const id = projectId();
    const box = document.querySelector('.character-reference');
    if (!id || !box || box.dataset.uploadUi === '1') return;
    box.dataset.uploadUi = '1';

    let project = null;
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(id)}`, {cache:'no-store'});
      if (response.ok) project = await response.json();
    } catch (_) {}

    activeHasReference = !!project?.character_reference;

    if (project?.name) {
      const title = document.querySelector('#result > h3');
      if (title && !title.dataset.projectNameApplied) {
        title.dataset.projectNameApplied = '1';
        title.textContent = `${project.name} — ${title.textContent}`;
      }
    }

    const controls = document.createElement('div');
    controls.style.cssText = 'margin-top:12px;padding-top:12px;border-top:1px solid #2d4059;';
    controls.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <label>Имя персонажа<input class="vg-ref-name" maxlength="200" value="${esc(project?.character_reference_name || '')}"></label>
        <label>Загрузить / заменить фото<input class="vg-ref-file" type="file" accept="image/jpeg,image/png,image/webp"></label>
      </div>
      <label>Инструкция к reference<textarea class="vg-ref-prompt" style="min-height:76px">${esc(project?.character_reference_prompt || '')}</textarea></label>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button type="button" class="vg-ref-upload">Загрузить фото</button>${project?.character_reference ? '<button type="button" class="danger vg-ref-delete">Удалить reference</button>' : ''}</div>
      ${project?.character_reference ? '<div class="muted" style="margin-top:7px">Reference автоматически передаётся в OpenAI Image и Local Next. Для Local Fast/Quality можно управлять чекбоксом сцены вручную.</div>' : ''}
      <div class="vg-ref-state muted" style="margin-top:7px"></div>
    `;
    box.appendChild(controls);

    if (project?.character_reference) {
      document.querySelectorAll('.use-reference').forEach(input => {
        if (input.dataset.touched !== '1') input.checked = true;
      });
    }

    controls.querySelector('.vg-ref-upload').addEventListener('click', async () => {
      const f = controls.querySelector('.vg-ref-file').files?.[0];
      const state = controls.querySelector('.vg-ref-state');
      if (!f) { state.textContent = 'Сначала выбери JPG/PNG/WEBP.'; state.style.color = '#ff8d8d'; return; }
      const data = new FormData();
      data.append('file', f);
      data.append('name', controls.querySelector('.vg-ref-name').value.trim());
      data.append('prompt', controls.querySelector('.vg-ref-prompt').value.trim());
      state.style.color = '';
      state.textContent = 'Загружаю Character Reference...';
      const response = await fetch(`/api/projects/${encodeURIComponent(id)}/character-reference/upload`, {method:'POST', body:data});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) { state.style.color='#ff8d8d'; state.textContent=`Ошибка: ${payload.detail || response.status}`; return; }
      location.reload();
    });

    controls.querySelector('.vg-ref-delete')?.addEventListener('click', async () => {
      if (!confirm('Удалить Character Reference из проекта?')) return;
      const response = await fetch(`/api/projects/${encodeURIComponent(id)}/character-reference`, {method:'DELETE'});
      if (response.ok) location.reload();
    });
  }

  function tick() {
    installNewProjectButton();
    enhanceCharacterReference();
  }

  tick();
  new MutationObserver(tick).observe(document.body, {childList:true, subtree:true});
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control":"no-store, max-age=0"})
