from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-series-text-refs.js")
async def videogen_series_text_refs_js():
    script = r'''
(() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const projectId = () => new URL(location.href).searchParams.get('project') || document.querySelector('.recent-project.active')?.dataset?.projectId || null;
  let busy = false;
  let lastSignature = '';

  async function jsonFetch(url, options={}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  function refCard(ref) {
    const scope = ref.to_episode == null ? `с ${ref.from_episode} серии` : `серии ${ref.from_episode}–${ref.to_episode}`;
    const type = ref.is_block ? 'BLOCK' : ref.kind;
    return `<div class="text-ref-card" data-id="${esc(ref.id)}" style="border:1px solid #343a4d;border-radius:10px;padding:10px;background:#0c1017">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap"><div><code style="font-size:14px;color:#bdafff">@${esc(ref.key)}</code> <b>${esc(ref.name)}</b> <span class="badge">${esc(type)}</span></div><button type="button" class="copy-ref secondary" data-key="${esc(ref.key)}" style="padding:6px 8px">Копировать тег</button></div>
      <div class="muted" style="font-size:11px;margin-top:3px">${esc(scope)}</div>
      ${ref.text_ru ? `<div style="margin-top:7px"><span class="muted">RU:</span> ${esc(ref.text_ru)}</div>` : ''}
      ${ref.text_en ? `<div style="margin-top:5px"><span class="muted">EN:</span> ${esc(ref.text_en)}</div>` : ''}
      <button type="button" class="delete-text-ref danger" style="padding:6px 8px;margin-top:8px">Удалить</button>
    </div>`;
  }

  async function render() {
    if (busy) return;
    const id = projectId();
    if (!id) { document.querySelector('.videogen-text-refs-panel')?.remove(); return; }
    let project;
    try { project = await jsonFetch(`/api/projects/${encodeURIComponent(id)}`, {cache:'no-store'}); } catch (_) { return; }
    if (!(project.request?.project_type === 'series' || project.request?.project_type === 'series_episode' || project.series_id)) {
      document.querySelector('.videogen-text-refs-panel')?.remove(); return;
    }
    let info;
    try { info = await jsonFetch(`/api/series/${encodeURIComponent(id)}`, {cache:'no-store'}); } catch (_) { return; }
    const refs = info.text_references || [];
    const signature = `${id}:${JSON.stringify(refs)}`;
    if (signature === lastSignature && document.querySelector('.videogen-text-refs-panel')) return;
    lastSignature = signature;

    document.querySelector('.videogen-text-refs-panel')?.remove();
    const panel = document.createElement('div');
    panel.className = 'videogen-text-refs-panel';
    panel.style.cssText = 'margin:14px 0;padding:15px;border:1px solid #38506b;border-radius:13px;background:#0c1219;';
    panel.innerHTML = `
      <div><b style="font-size:17px">Text References / Visual Aliases</b><div class="muted" style="margin-top:3px">Пиши в visual prompt короткие теги вроде <code>@char_tim</code> или готовый блок <code>@visual_tim_boat</code>. Перед image generation backend развернёт их в полный EN prompt.</div></div>
      <div class="text-ref-list" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:8px;margin-top:11px">${refs.map(refCard).join('') || '<div class="muted">Пока нет текстовых references.</div>'}</div>
      <details style="margin-top:12px"><summary>Добавить ключ / готовый блок</summary>
        <div style="display:grid;grid-template-columns:1fr 1.3fr .8fr .6fr;gap:7px;align-items:end;margin-top:9px">
          <label>Ключ без @<input class="tr-key" placeholder="char_tim"></label>
          <label>Название<input class="tr-name" placeholder="Мышонок Тим"></label>
          <label>Тип<select class="tr-kind"><option value="character">character</option><option value="object">object</option><option value="location">location</option><option value="style">style</option></select></label>
          <label style="display:flex;align-items:center;gap:7px;padding-bottom:18px"><input class="tr-block" type="checkbox" style="width:auto;margin:0"> Готовый блок</label>
        </div>
        <label>RU<textarea class="tr-ru" style="min-height:72px" placeholder="Мышонок Тим с золотистыми глазами, серым мехом и красной шапочкой"></textarea></label>
        <label>EN<textarea class="tr-en" style="min-height:72px" placeholder="Tim the mouse, a small gray mouse with golden eyes, soft gray fur and a red cap"></textarea></label>
        <div class="muted" style="font-size:12px;margin:-4px 0 8px">Для блока можно использовать другие ключи: <code>@char_tim, @prop_boat, @style_cartoon</code></div>
        <div style="display:grid;grid-template-columns:160px 160px auto;gap:8px;align-items:end"><label>С серии<input class="tr-from" type="number" min="1" value="1"></label><label>До серии<input class="tr-to" type="number" min="1" placeholder="∞"></label><button type="button" class="tr-add">Добавить</button></div>
      </details>
      <div class="tr-state muted" style="margin-top:8px"></div>`;

    const seriesPanel = document.querySelector('.videogen-series-panel');
    if (seriesPanel) seriesPanel.insertAdjacentElement('afterend', panel);
    else document.querySelector('.project-actions')?.insertAdjacentElement('afterend', panel);

    const state = panel.querySelector('.tr-state');
    panel.querySelectorAll('.copy-ref').forEach(btn => btn.onclick = async () => {
      const value = '@' + btn.dataset.key;
      try { await navigator.clipboard.writeText(value); state.textContent = `${value} скопирован.`; } catch (_) { state.textContent = value; }
    });
    panel.querySelectorAll('.delete-text-ref').forEach(btn => btn.onclick = async () => {
      const card = btn.closest('.text-ref-card');
      if (!confirm('Удалить этот текстовый reference/alias?')) return;
      busy = true;
      try { await jsonFetch(`/api/series/${encodeURIComponent(info.root.id)}/text-references/${encodeURIComponent(card.dataset.id)}`, {method:'DELETE'}); lastSignature=''; state.textContent='Удалено.'; } catch(e) { state.textContent=`Ошибка: ${e.message}`; } finally { busy=false; render(); }
    });
    panel.querySelector('.tr-add').onclick = async () => {
      const key = panel.querySelector('.tr-key').value.trim().replace(/^@/, '');
      const name = panel.querySelector('.tr-name').value.trim();
      if (!key || !name) { state.textContent='Нужны ключ и название.'; return; }
      const rawTo = panel.querySelector('.tr-to').value.trim();
      const payload = {key, name, kind:panel.querySelector('.tr-kind').value, text_ru:panel.querySelector('.tr-ru').value.trim(), text_en:panel.querySelector('.tr-en').value.trim(), is_block:panel.querySelector('.tr-block').checked, from_episode:Number(panel.querySelector('.tr-from').value||1), to_episode:rawTo?Number(rawTo):null};
      busy = true; state.textContent='Добавляю...';
      try { await jsonFetch(`/api/series/${encodeURIComponent(info.root.id)}/text-references`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); lastSignature=''; state.textContent=`@${key} добавлен.`; } catch(e) { state.textContent=`Ошибка: ${e.message}`; } finally { busy=false; render(); }
    };
  }

  const observer = new MutationObserver(render);
  observer.observe(document.documentElement,{childList:true,subtree:true});
  render();
  setInterval(render,1400);
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
