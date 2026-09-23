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
  let badgeLoadFor = '';
  let enhanceQueued = false;

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
    return `<div class="series-text-suggestion" data-index="${index}" style="border:1px solid #343b49;border-radius:10px;padding:10px;background:#0d1118">
      <div style="display:grid;grid-template-columns:1fr 1.2fr .8fr;gap:7px">
        <label>Ключ<input class="s-key" value="${esc(item.key)}"></label>
        <label>Название<input class="s-name" value="${esc(item.name)}"></label>
        <label>Тип<select class="s-kind"><option value="character" ${item.kind==='character'?'selected':''}>character</option><option value="object" ${item.kind==='object'?'selected':''}>object</option><option value="location" ${item.kind==='location'?'selected':''}>location</option><option value="style" ${item.kind==='style'?'selected':''}>style</option></select></label>
      </div>
      <label>RU<textarea class="s-ru" style="min-height:68px">${esc(item.text_ru || '')}</textarea></label>
      <label>EN<textarea class="s-en" style="min-height:68px">${esc(item.text_en || '')}</textarea></label>
      <label style="display:flex;align-items:center;gap:7px"><input class="s-block" type="checkbox" style="width:auto;margin:0" ${item.is_block?'checked':''}> Готовый visual block</label>
      <div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:8px">
        <button type="button" class="delete-text-suggestion danger" style="padding:7px 10px">Удалить</button>
        <button type="button" class="accept-text-suggestion" style="padding:7px 10px">Принять</button>
      </div>
    </div>`;
  }

  function readSuggestion(card, original) {
    return {
      key: card.querySelector('.s-key').value.trim().replace(/^@/, ''),
      name: card.querySelector('.s-name').value.trim(),
      kind: card.querySelector('.s-kind').value,
      text_ru: card.querySelector('.s-ru').value.trim(),
      text_en: card.querySelector('.s-en').value.trim(),
      is_block: card.querySelector('.s-block').checked,
      from_episode: Number(original.from_episode || 1),
      to_episode: original.to_episode ?? null,
    };
  }

  async function acceptSuggestion(seriesId, original, card, state) {
    const button = card?.querySelector('.accept-text-suggestion');
    if (button) button.disabled = true;
    try {
      const item = readSuggestion(card, original);
      if (!item.key || !item.name || (!item.text_ru && !item.text_en)) throw new Error('Заполни ключ, название и описание');
      await jsonFetch(`/api/series/${encodeURIComponent(seriesId)}/text-references`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(item),
      });
      if (card) {
        card.dataset.accepted = '1';
        card.style.opacity = '.62';
        if (button) button.textContent = 'Принято';
        card.querySelector('.delete-text-suggestion')?.setAttribute('disabled', 'disabled');
      }
      return true;
    } catch (e) {
      if (button) button.disabled = false;
      if (state) state.textContent = `Ошибка: ${e.message}`;
      return false;
    }
  }

  async function assignSceneReferences(state, button) {
    const id = projectId();
    if (!id) return;
    button.disabled = true;
    state.textContent = 'Qwen сопоставляет утверждённый canon с каждой сценой. Текст сцен не меняется...';
    try {
      const data = await jsonFetch(`/api/series/${encodeURIComponent(id)}/text-references/assign-scenes`, {method:'POST'});
      state.textContent = `Привязано: ${data.assigned_scenes}/${data.total_scenes} сцен · LLM profile: ${data.profile}`;
      badgeLoadFor = '';
      await refreshSceneReferenceBadges(true);
    } catch (e) {
      state.textContent = `Ошибка: ${e.message}`;
    } finally {
      button.disabled = false;
    }
  }

  function enhanceSeriesTextAI() {
    const panel = document.querySelector('.videogen-text-refs-panel') || document.querySelector('.videogen-series-panel');
    if (!panel || panel.querySelector('.series-text-ai')) return;

    const box = document.createElement('div');
    box.className = 'series-text-ai';
    box.style.cssText = 'margin-top:12px;padding:11px;border:1px solid #3b3456;border-radius:10px;background:#11101b;';
    box.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap">
        <div><b>AI Canon Builder</b><div class="muted" style="font-size:12px;margin-top:3px">Сначала проверь и поправь storyboard. Затем Qwen пройдёт по уже утверждённым сценам и предложит общий visual canon. Сцены автоматически не переписываются.</div></div>
        <div style="display:flex;gap:7px;flex-wrap:wrap"><button type="button" class="series-text-ai-generate">Собрать Text References из сцен</button><button type="button" class="series-text-ai-assign secondary">Привязать canon к сценам</button></div>
      </div>
      <div class="series-text-ai-state muted" style="margin-top:7px"></div>
      <div class="series-text-ai-results" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:8px;margin-top:9px"></div>`;

    panel.appendChild(box);

    const state = box.querySelector('.series-text-ai-state');
    const results = box.querySelector('.series-text-ai-results');
    const button = box.querySelector('.series-text-ai-generate');
    const assignButton = box.querySelector('.series-text-ai-assign');
    assignButton.onclick = () => assignSceneReferences(state, assignButton);

    button.onclick = async () => {
      const id = projectId();
      if (!id) return;
      button.disabled = true;
      state.textContent = 'Qwen читает проверенные сцены: action, narration, dialogue и visual prompts...';
      results.innerHTML = '';
      try {
        const data = await jsonFetch(`/api/series/${encodeURIComponent(id)}/text-references/generate`, {method:'POST'});
        const items = data.suggestions || [];
        state.textContent = `Предложено: ${items.length} · LLM profile: ${data.profile}. Можешь удалить лишнее или отредактировать карточку перед принятием.`;
        results.innerHTML = items.map(suggestionCard).join('');
        if (items.length > 1) {
          const acceptAll = document.createElement('button');
          acceptAll.type = 'button';
          acceptAll.className = 'series-text-ai-accept-all';
          acceptAll.textContent = 'Принять все после проверки';
          acceptAll.style.cssText = 'grid-column:1/-1;background:#2d7651';
          results.prepend(acceptAll);
          acceptAll.onclick = async () => {
            acceptAll.disabled = true;
            let accepted = 0;
            for (let i = 0; i < items.length; i++) {
              const card = results.querySelector(`.series-text-suggestion[data-index="${i}"]`);
              if (!card || card.dataset.accepted === '1' || card.dataset.deleted === '1') continue;
              if (await acceptSuggestion(data.series_id, items[i], card, state)) accepted++;
            }
            state.textContent = `Добавлено в canon: ${accepted}. Теперь нажми «Привязать canon к сценам».`;
          };
        }
        results.querySelectorAll('.delete-text-suggestion').forEach(btn => btn.onclick = () => {
          const card = btn.closest('.series-text-suggestion');
          if (!card || card.dataset.accepted === '1') return;
          const key = card.querySelector('.s-key')?.value?.trim() || 'reference';
          if (!confirm(`Убрать предложение ${key} из текущего списка? В canon оно не попадёт.`)) return;
          card.dataset.deleted = '1';
          card.remove();
          state.textContent = `${key} исключён из предложений.`;
        });
        results.querySelectorAll('.accept-text-suggestion').forEach(btn => btn.onclick = async () => {
          const card = btn.closest('.series-text-suggestion');
          const item = items[Number(card.dataset.index)];
          const ok = await acceptSuggestion(data.series_id, item, card, state);
          if (ok) state.textContent = `@${card.querySelector('.s-key').value.trim().replace(/^@/, '')} добавлен в canon.`;
        });
      } catch (e) {
        state.textContent = `Ошибка: ${e.message}`;
      } finally {
        button.disabled = false;
      }
    };
  }

  function tooltipFor(ref) {
    const description = ref.text_ru || ref.text_en || '';
    const prefix = ref.is_block ? 'BLOCK' : ref.kind;
    return `${'@' + ref.key} · ${prefix}\n${ref.name}\n${description}`;
  }

  async function refreshSceneReferenceBadges(force=false) {
    const id = projectId();
    const scenes = [...document.querySelectorAll('.scene-editor[data-scene-id]')];
    if (!id || !scenes.length) return;
    const signature = `${id}:${scenes.map(x => x.dataset.sceneId).join(',')}`;
    if (!force && badgeLoadFor === signature && scenes.every(x => x.querySelector('.scene-reference-badges'))) return;
    badgeLoadFor = signature;
    try {
      const [project, info] = await Promise.all([
        jsonFetch(`/api/projects/${encodeURIComponent(id)}`, {cache:'no-store'}),
        jsonFetch(`/api/series/${encodeURIComponent(id)}`, {cache:'no-store'}),
      ]);
      const refMap = new Map((info.text_references || []).map(ref => [String(ref.key).toLowerCase(), ref]));
      const sceneMap = new Map((project.storyboard?.scenes || []).map(scene => [scene.id, scene]));
      scenes.forEach(sceneEl => {
        const scene = sceneMap.get(sceneEl.dataset.sceneId);
        let row = sceneEl.querySelector('.scene-reference-badges');
        if (!row) {
          row = document.createElement('div');
          row.className = 'scene-reference-badges';
          row.style.cssText = 'display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:8px 0 10px;';
          const promptFields = sceneEl.querySelector('.prompt-fields');
          if (promptFields) sceneEl.insertBefore(row, promptFields);
          else sceneEl.querySelector('.scene-head')?.insertAdjacentElement('afterend', row);
        }
        const keys = scene?.reference_keys || [];
        row.innerHTML = keys.length
          ? `<span class="muted" style="font-size:12px">References:</span>` + keys.map(key => {
              const ref = refMap.get(String(key).toLowerCase());
              const title = ref ? tooltipFor(ref) : `@${key}`;
              return `<span class="badge scene-ref-badge" title="${esc(title)}" style="cursor:help">@${esc(key)}</span>`;
            }).join('')
          : '<span class="muted" style="font-size:12px">References: пока не назначены</span>';
      });
    } catch (_) {
      badgeLoadFor = '';
    }
  }

  function enhance() {
    enhanceBulkImageMenu();
    enhanceSeriesTextAI();
    refreshSceneReferenceBadges();
  }

  function scheduleEnhance() {
    if (enhanceQueued) return;
    enhanceQueued = true;
    requestAnimationFrame(() => {
      enhanceQueued = false;
      enhance();
    });
  }

  const root = document.getElementById('result') || document.body;
  const observer = new MutationObserver(mutations => {
    if (mutations.some(m => [...m.addedNodes].some(n => n.nodeType === 1 && (n.matches?.('.scene-editor,.project-actions,.videogen-text-refs-panel,.videogen-series-panel') || n.querySelector?.('.scene-editor,.project-actions,.videogen-text-refs-panel,.videogen-series-panel'))))) {
      scheduleEnhance();
    }
  });
  observer.observe(root, {childList:true, subtree:true});
  enhance();
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
