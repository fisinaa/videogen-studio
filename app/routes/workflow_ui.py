from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["workflow-ui"])


@router.get("/videogen-workflow.js")
async def videogen_workflow_js():
    script = r'''
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const projectId = () => new URL(location.href).searchParams.get('project') || document.querySelector('.recent-project.active')?.dataset?.projectId || null;

  async function jsonFetch(url, options={}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  function enhanceBulkImageMenu() {
    const actions = document.querySelector('.project-actions');
    if (!actions || actions.querySelector('.bulk-image-model-menu')) return;
    const fast = actions.querySelector('#generate-all-fast');
    const quality = actions.querySelector('#generate-all-quality');
    const next = actions.querySelector('#generate-all-next');
    const openai = actions.querySelector('#generate-all-openai');
    if (!fast || !quality || !next || !openai) return;

    [fast, quality, next, openai].forEach(button => { button.style.display = 'none'; });

    const group = document.createElement('span');
    group.className = 'bulk-image-model-menu';
    group.style.cssText = 'display:inline-flex;gap:6px;align-items:center;';

    const select = document.createElement('select');
    select.className = 'bulk-image-model-select';
    select.title = 'Модель для генерации всех сцен';
    select.style.cssText = 'width:auto;min-width:165px;margin:0;padding:10px 30px 10px 10px;background:#0d1016;color:#fff;border:1px solid #303646;border-radius:10px;';
    select.innerHTML = `
      <option value="fast">Local Fast</option>
      <option value="quality" ${quality.disabled ? 'disabled' : ''}>Local Quality${quality.disabled ? ' — недоступна' : ''}</option>
      <option value="next" ${next.disabled ? 'disabled' : ''}>Local Next${next.disabled ? ' — недоступна' : ''}</option>
      <option value="openai">OpenAI Final</option>
    `;
    if (!next.disabled) select.value = 'next';
    else if (!quality.disabled) select.value = 'quality';
    else select.value = 'fast';

    const generate = document.createElement('button');
    generate.type = 'button';
    generate.className = 'bulk-image-generate';
    generate.textContent = 'Сгенерировать все картинки';
    generate.style.background = '#2d7651';
    generate.onclick = () => {
      const target = {fast, quality, next, openai}[select.value];
      if (target && !target.disabled) target.click();
    };

    group.append(select, generate);
    const audio = actions.querySelector('#generate-all-audio');
    actions.insertBefore(group, audio || null);
  }

  function suggestionCard(item, index) {
    const tag = '@' + item.key;
    return `<div class="series-text-suggestion" data-index="${index}" style="border:1px solid #343b49;border-radius:10px;padding:10px;background:#0d1118">
      <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap"><code style="font-size:13px">${esc(tag)}</code><span class="badge">${item.is_block ? 'BLOCK' : esc(item.kind)}</span><b>${esc(item.name)}</b></div>
      <div style="font-size:12px;margin-top:7px"><b>RU:</b> ${esc(item.text_ru || '')}</div>
      <div style="font-size:12px;margin-top:5px"><b>EN:</b> ${esc(item.text_en || '')}</div>
      <button type="button" class="accept-text-suggestion" style="margin-top:8px;padding:7px 10px">Принять</button>
    </div>`;
  }

  async function acceptSuggestion(seriesId, item, card, state) {
    const button = card?.querySelector('.accept-text-suggestion');
    if (button) button.disabled = true;
    try {
      await jsonFetch(`/api/series/${encodeURIComponent(seriesId)}/text-references`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(item),
      });
      if (card) {
        card.style.opacity = '.5';
        if (button) button.textContent = 'Принято';
      }
      return true;
    } catch (e) {
      if (button) button.disabled = false;
      if (state) state.textContent = `Ошибка: ${e.message}`;
      return false;
    }
  }

  function enhanceSeriesTextAI() {
    const panel = document.querySelector('.videogen-series-panel');
    if (!panel || panel.querySelector('.series-text-ai')) return;

    const box = document.createElement('div');
    box.className = 'series-text-ai';
    box.style.cssText = 'margin-top:12px;padding:11px;border:1px solid #3b3456;border-radius:10px;background:#11101b;';
    box.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap">
        <div><b>AI Text References</b><div class="muted" style="font-size:12px;margin-top:3px">Qwen анализирует канон и предлагает @char_ / @prop_ / @loc_ / @style_ / @visual_ блоки. Ничего не добавляется в canon без подтверждения.</div></div>
        <button type="button" class="series-text-ai-generate">Сгенерировать Text References из проекта</button>
      </div>
      <div class="series-text-ai-state muted" style="margin-top:7px"></div>
      <div class="series-text-ai-results" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:8px;margin-top:9px"></div>`;

    const details = panel.querySelector('details');
    if (details) panel.insertBefore(box, details);
    else panel.appendChild(box);

    const state = box.querySelector('.series-text-ai-state');
    const results = box.querySelector('.series-text-ai-results');
    const button = box.querySelector('.series-text-ai-generate');

    button.onclick = async () => {
      const id = projectId();
      if (!id) return;
      button.disabled = true;
      state.textContent = 'Qwen анализирует сериал и собирает канонические ключи...';
      results.innerHTML = '';
      try {
        const data = await jsonFetch(`/api/series/${encodeURIComponent(id)}/text-references/generate`, {method:'POST'});
        const items = data.suggestions || [];
        state.textContent = `Предложено: ${items.length} · LLM profile: ${data.profile}`;
        results.innerHTML = items.map(suggestionCard).join('');
        if (items.length > 1) {
          const acceptAll = document.createElement('button');
          acceptAll.type = 'button';
          acceptAll.className = 'series-text-ai-accept-all';
          acceptAll.textContent = 'Принять все предложения';
          acceptAll.style.cssText = 'grid-column:1/-1;background:#2d7651';
          results.prepend(acceptAll);
          acceptAll.onclick = async () => {
            acceptAll.disabled = true;
            let accepted = 0;
            for (let i = 0; i < items.length; i++) {
              const card = results.querySelector(`.series-text-suggestion[data-index="${i}"]`);
              if (card?.querySelector('.accept-text-suggestion')?.textContent === 'Принято') continue;
              if (await acceptSuggestion(data.series_id, items[i], card, state)) accepted++;
            }
            state.textContent = `Добавлено в canon: ${accepted}. Обновляю страницу...`;
            setTimeout(() => location.reload(), 500);
          };
        }
        results.querySelectorAll('.accept-text-suggestion').forEach(btn => btn.onclick = async () => {
          const card = btn.closest('.series-text-suggestion');
          const item = items[Number(card.dataset.index)];
          const ok = await acceptSuggestion(data.series_id, item, card, state);
          if (ok) state.textContent = `${'@' + item.key} добавлен в canon.`;
        });
      } catch (e) {
        state.textContent = `Ошибка: ${e.message}`;
      } finally {
        button.disabled = false;
      }
    };
  }

  function enhance() {
    enhanceBulkImageMenu();
    enhanceSeriesTextAI();
  }

  const observer = new MutationObserver(enhance);
  observer.observe(document.documentElement, {childList:true, subtree:true});
  enhance();
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
