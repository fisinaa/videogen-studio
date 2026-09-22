from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-series.js")
async def videogen_series_js():
    script = r'''
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  let lastProjectId = null;
  let refreshing = false;

  const projectId = () => new URL(location.href).searchParams.get('project') || document.querySelector('.recent-project.active')?.dataset?.projectId || null;

  async function jsonFetch(url, options={}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  function ensureSeriesOption() {
    const select = document.getElementById('project_type');
    if (!select || select.querySelector('option[value="series"]')) return;
    const option = document.createElement('option');
    option.value = 'series';
    option.textContent = 'Сериал';
    const episode = select.querySelector('option[value="series_episode"]');
    if (episode) select.insertBefore(option, episode);
    else select.appendChild(option);
  }

  function episodeLink(item, activeId) {
    const active = item.id === activeId;
    return `<a href="?project=${encodeURIComponent(item.id)}" style="display:block;padding:8px 10px;margin:5px 0;border-radius:8px;border:1px solid ${active ? '#7c5cff' : '#2a3140'};background:${active ? '#171329' : '#0d1118'};color:#fff;text-decoration:none"><b>Серия ${esc(item.episode_number || '?')}</b> · ${esc(item.title)}</a>`;
  }

  async function renderSeriesTree() {
    let data;
    try { data = await jsonFetch('/api/series/tree', {cache:'no-store'}); } catch (_) { return; }
    const heading = [...document.querySelectorAll('h2')].find(x => x.textContent.trim() === 'Последние проекты');
    const section = heading?.parentElement;
    if (!section) return;
    let tree = section.querySelector('.videogen-series-tree');
    if (!tree) {
      tree = document.createElement('div');
      tree.className = 'videogen-series-tree';
      heading.insertAdjacentElement('afterend', tree);
    }
    const current = projectId();
    const allEpisodeIds = new Set();
    (data.series || []).forEach(group => (group.episodes || []).forEach(ep => allEpisodeIds.add(ep.id)));
    document.querySelectorAll('.recent-project').forEach(card => {
      card.style.display = allEpisodeIds.has(card.dataset.projectId) ? 'none' : '';
    });
    if (!(data.series || []).length) { tree.innerHTML = ''; return; }
    tree.innerHTML = `<div style="margin:10px 0 16px"><div class="muted" style="margin-bottom:7px">Сериалы</div>${data.series.map(group => `
      <div style="border:1px solid #35304c;border-radius:12px;padding:11px;margin:9px 0;background:#100f18">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap"><div><span class="badge">Сериал</span> <b>${esc(group.title)}</b></div><span class="muted">${(group.episodes||[]).length} сер.</span></div>
        <div style="margin:8px 0 0 16px;border-left:2px solid #34304d;padding-left:10px">${(group.episodes||[]).map(ep => episodeLink(ep,current)).join('')}</div>
      </div>`).join('')}</div>`;
  }

  function refCard(ref) {
    const a = ref.asset || {};
    const scope = ref.to_episode == null ? `с ${ref.from_episode} серии` : `серии ${ref.from_episode}–${ref.to_episode}`;
    return `<div class="series-ref-card" data-ref-id="${esc(ref.id)}" style="display:grid;grid-template-columns:92px 1fr;gap:10px;border:1px solid #33394a;border-radius:10px;padding:9px;background:#0d1118">
      <div>${a.preview_url ? `<img src="${esc(a.preview_url)}" style="width:92px;height:92px;object-fit:cover;border-radius:8px">` : ''}</div>
      <div><b>${esc(ref.name)}</b> <span class="badge">${esc(ref.kind)}</span><div class="muted" style="font-size:12px">${esc(scope)}</div><div style="font-size:12px;margin-top:4px">${esc(ref.description || '')}</div><button type="button" class="series-ref-delete danger" style="padding:6px 8px;margin-top:7px">Убрать Reference</button></div>
    </div>`;
  }

  async function loadRootImages(rootId, select) {
    const data = await jsonFetch(`/api/media-library/projects/${encodeURIComponent(rootId)}`, {cache:'no-store'});
    const images = (data.items || []).filter(item => item.kind === 'image' && item.bucket === 'media');
    select.innerHTML = images.length
      ? images.map(item => `<option value="${esc(item.filename)}">${esc(item.filename)}</option>`).join('')
      : '<option value="">Сначала загрузи/сгенерируй изображение в Media Manager корневой серии</option>';
  }

  async function renderSeriesPanel(project) {
    const relevant = project.request?.project_type === 'series' || project.request?.project_type === 'series_episode' || project.series_id;
    document.querySelector('.videogen-series-panel')?.remove();
    if (!relevant) return;

    if (project.request?.project_type === 'series' && !project.series_id) {
      await jsonFetch(`/api/series/${encodeURIComponent(project.id)}/initialize`, {method:'POST'});
      project = await jsonFetch(`/api/projects/${encodeURIComponent(project.id)}`, {cache:'no-store'});
    }

    const info = await jsonFetch(`/api/series/${encodeURIComponent(project.id)}`, {cache:'no-store'});
    const root = info.root;
    const panel = document.createElement('div');
    panel.className = 'videogen-series-panel';
    panel.style.cssText = 'margin:14px 0;padding:15px;border:1px solid #514276;border-radius:13px;background:#100d19;';
    panel.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap"><div><span class="badge">СЕРИАЛ</span> <b style="font-size:17px">${esc(info.title)}</b><div class="muted">Серия ${esc(project.episode_number || 1)} · Character Reference и continuity references общие для сериала</div></div><button type="button" class="series-sync secondary">Синхронизировать References</button></div>
      <div style="display:grid;grid-template-columns:minmax(240px,.7fr) minmax(320px,1.3fr);gap:14px;margin-top:13px">
        <div style="border:1px solid #302a44;border-radius:10px;padding:11px;background:#0c0b12">
          <b>Series Character Reference</b>
          <div class="muted" style="font-size:12px;margin:4px 0 8px">Генерируется один раз в корневом проекте и используется всеми сериями.</div>
          ${info.character_reference?.preview_url ? `<img src="${esc(info.character_reference.preview_url)}" style="width:180px;max-width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:9px;display:block;margin-bottom:8px">` : '<div class="muted" style="margin:8px 0">Ещё не создан.</div>'}
          <button type="button" class="series-character-generate">${info.character_reference ? 'Перегенерировать общий Character Reference' : 'Создать общий Character Reference'}</button>
        </div>
        <div><b>Постоянные References</b><div class="muted" style="font-size:12px;margin:4px 0 8px">Персонажи, предметы, локации. Можно ограничить диапазоном серий — например кораблик только 1–4.</div><div class="series-ref-list" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:8px">${(info.references||[]).map(refCard).join('') || '<div class="muted">Пока нет дополнительных references.</div>'}</div></div>
      </div>
      <details style="margin-top:12px"><summary>Добавить Series Reference из Media Manager</summary><div style="display:grid;grid-template-columns:1.2fr .8fr .6fr .45fr .45fr;gap:7px;align-items:end;margin-top:9px"><label>Изображение<select class="series-ref-file"></select></label><label>Название<input class="series-ref-name" placeholder="Кораблик"></label><label>Тип<select class="series-ref-kind"><option value="object">object</option><option value="character">character</option><option value="location">location</option><option value="style">style</option></select></label><label>С серии<input class="series-ref-from" type="number" min="1" value="1"></label><label>До серии<input class="series-ref-to" type="number" min="1" placeholder="∞"></label></div><label>Что именно нельзя менять<textarea class="series-ref-description" style="min-height:72px" placeholder="Маленький деревянный игрушечный кораблик, красный треугольный парус..."></textarea></label><button type="button" class="series-ref-add">Добавить Reference</button></details>
      <details style="margin-top:12px"><summary>Серии проекта</summary><div class="series-episodes" style="margin-top:8px">${(info.episodes||[]).map(ep => episodeLink(ep,project.id)).join('')}</div><label style="margin-top:9px">Идея следующей серии<textarea class="series-new-episode-prompt" style="min-height:76px" placeholder="Оставь пустым — LLM продолжит сериал сам"></textarea></label><div style="display:flex;gap:7px;align-items:end;flex-wrap:wrap"><label style="max-width:190px">Длительность, сек<input class="series-new-episode-duration" type="number" min="10" max="7200" value="${esc(root.request.duration_seconds)}"></label><button type="button" class="series-new-episode">Создать следующую серию</button></div></details>
      <div class="series-state muted" style="margin-top:8px"></div>`;

    const production = document.querySelector('.videogen-project-production');
    const actions = document.querySelector('.project-actions');
    if (production) production.insertAdjacentElement('beforebegin', panel);
    else if (actions) actions.insertAdjacentElement('afterend', panel);
    else document.getElementById('result')?.prepend(panel);

    await loadRootImages(root.id, panel.querySelector('.series-ref-file')).catch(() => {});
    const state = panel.querySelector('.series-state');

    panel.querySelector('.series-sync').onclick = async () => {
      state.textContent = 'Синхронизирую references по сериям...';
      try { const r = await jsonFetch(`/api/series/${encodeURIComponent(root.id)}/sync-references`, {method:'POST'}); state.textContent = `Синхронизировано серий: ${r.synced}`; } catch (e) { state.textContent = `Ошибка: ${e.message}`; }
    };

    panel.querySelector('.series-character-generate').onclick = async btn => {
      const b = btn.currentTarget; b.disabled = true; state.textContent = 'Генерирую общий Character Reference...';
      try {
        await jsonFetch(`/api/projects/${encodeURIComponent(root.id)}/character-reference/generate`, {method:'POST'});
        await jsonFetch(`/api/series/${encodeURIComponent(root.id)}/sync-references`, {method:'POST'});
        state.textContent = 'Character Reference готов и распространён на серии.';
        await refreshCurrent(true);
      } catch (e) { state.textContent = `Ошибка: ${e.message}`; b.disabled = false; }
    };

    panel.querySelector('.series-ref-add').onclick = async () => {
      const filename = panel.querySelector('.series-ref-file').value;
      const name = panel.querySelector('.series-ref-name').value.trim();
      if (!filename || !name) { state.textContent = 'Выбери изображение и задай название.'; return; }
      const rawTo = panel.querySelector('.series-ref-to').value.trim();
      const payload = {
        filename,
        name,
        kind: panel.querySelector('.series-ref-kind').value,
        description: panel.querySelector('.series-ref-description').value.trim(),
        from_episode: Number(panel.querySelector('.series-ref-from').value || 1),
        to_episode: rawTo ? Number(rawTo) : null,
      };
      state.textContent = 'Добавляю общий Reference...';
      try { await jsonFetch(`/api/series/${encodeURIComponent(root.id)}/references`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); state.textContent = 'Reference добавлен.'; await refreshCurrent(true); } catch (e) { state.textContent = `Ошибка: ${e.message}`; }
    };

    panel.querySelectorAll('.series-ref-delete').forEach(button => button.onclick = async () => {
      const card = button.closest('.series-ref-card');
      if (!confirm('Убрать этот reference из continuity сериала? Сам файл останется в Media Manager.')) return;
      try { await jsonFetch(`/api/series/${encodeURIComponent(root.id)}/references/${encodeURIComponent(card.dataset.refId)}`, {method:'DELETE'}); await refreshCurrent(true); } catch (e) { state.textContent = `Ошибка: ${e.message}`; }
    });

    panel.querySelector('.series-new-episode').onclick = async button => {
      button.currentTarget.disabled = true; state.textContent = 'Создаю storyboard следующей серии...';
      const payload = {prompt: panel.querySelector('.series-new-episode-prompt').value.trim(), duration_seconds: Number(panel.querySelector('.series-new-episode-duration').value || root.request.duration_seconds)};
      try {
        const created = await jsonFetch(`/api/series/${encodeURIComponent(root.id)}/episodes`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        location.href = `/?project=${encodeURIComponent(created.project.id)}`;
      } catch (e) { state.textContent = `Ошибка: ${e.message}`; button.currentTarget.disabled = false; }
    };
  }

  async function refreshCurrent(force=false) {
    if (refreshing) return;
    const id = projectId();
    if (!id) { document.querySelector('.videogen-series-panel')?.remove(); return; }
    if (!force && id === lastProjectId && document.querySelector('.videogen-series-panel')) return;
    refreshing = true;
    try {
      const project = await jsonFetch(`/api/projects/${encodeURIComponent(id)}`, {cache:'no-store'});
      lastProjectId = id;
      await renderSeriesPanel(project);
      await renderSeriesTree();
    } catch (_) {} finally { refreshing = false; }
  }

  ensureSeriesOption();
  renderSeriesTree();
  refreshCurrent(true);
  const observer = new MutationObserver(() => { ensureSeriesOption(); refreshCurrent(); });
  observer.observe(document.documentElement, {childList:true,subtree:true});
  setInterval(() => { ensureSeriesOption(); refreshCurrent(); }, 1200);
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
