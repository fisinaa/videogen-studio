from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["story-repair-ui"])


@router.get("/videogen-story-repair.js")
async def videogen_story_repair_js():
    script = r'''
(() => {
  const projectId = () => new URL(location.href).searchParams.get('project') || document.querySelector('.recent-project.active')?.dataset?.projectId || null;
  let queued = false;

  async function jsonFetch(url, options={}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  async function enhance() {
    const actions = document.querySelector('.project-actions');
    const id = projectId();
    if (!actions || !id || actions.querySelector('.story-tail-repair')) return;

    let project;
    try { project = await jsonFetch(`/api/projects/${encodeURIComponent(id)}`, {cache:'no-store'}); }
    catch (_) { return; }

    const scenes = project.storyboard?.scenes || [];
    if (scenes.length < 2) return;

    const group = document.createElement('span');
    group.className = 'story-tail-repair';
    group.style.cssText = 'display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap;';

    const select = document.createElement('select');
    select.className = 'story-tail-repair-start';
    select.title = 'С какой сцены полностью пересобрать оставшийся сюжет';
    select.style.cssText = 'width:auto;min-width:150px;margin:0;padding:9px 28px 9px 9px;';
    scenes.forEach((scene, index) => {
      const option = document.createElement('option');
      option.value = scene.id;
      option.textContent = `с ${index + 1}: ${String(scene.title || scene.id).slice(0, 34)}`;
      select.appendChild(option);
    });
    // For an already-corrupted long storyboard, default near the middle instead of scene 1.
    select.selectedIndex = Math.min(6, scenes.length - 1);

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'story-tail-repair-button danger';
    button.textContent = 'Пересобрать хвост сюжета';
    button.title = 'Старые тексты, картинки и canon-keys выбранной сцены и всех последующих будут заменены';

    const state = document.createElement('span');
    state.className = 'story-tail-repair-state muted';
    state.style.cssText = 'font-size:12px;max-width:340px;';

    button.onclick = async () => {
      const startId = select.value;
      const startIndex = scenes.findIndex(scene => scene.id === startId);
      const count = scenes.length - startIndex;
      if (!confirm(`Полностью пересобрать сюжет с сцены ${startIndex + 1} до ${scenes.length}?\n\nБудут очищены старые картинки, audio, motion и reference keys у этих ${count} сцен.`)) return;

      button.disabled = true;
      select.disabled = true;
      state.textContent = `Qwen сначала строит новый цельный план для ${count} сцен, затем разворачивает его в storyboard...`;
      try {
        const data = await jsonFetch(`/api/projects/${encodeURIComponent(id)}/storyboard/rebuild-tail?start_scene_id=${encodeURIComponent(startId)}`, {method:'POST'});
        state.textContent = `Готово: пересобрано ${data.rebuilt_scenes} сцен. Перезагружаю...`;
        setTimeout(() => location.reload(), 450);
      } catch (e) {
        state.textContent = `Ошибка: ${e.message}`;
        button.disabled = false;
        select.disabled = false;
      }
    };

    group.append(select, button, state);
    actions.appendChild(group);
  }

  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; enhance(); });
  }

  const root = document.getElementById('result') || document.body;
  const observer = new MutationObserver(mutations => {
    if (mutations.some(m => [...m.addedNodes].some(n => n.nodeType === 1 && (n.matches?.('.project-actions') || n.querySelector?.('.project-actions'))))) schedule();
  });
  observer.observe(root, {childList:true, subtree:true});
  enhance();
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
