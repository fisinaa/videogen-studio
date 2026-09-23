from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-scene-collapse.js")
async def videogen_scene_collapse_js():
    script = r'''
(() => {
  const sceneSelector = '.scene-editor[data-scene-id]';

  function projectId() {
    return new URL(location.href).searchParams.get('project') ||
      document.querySelector('.recent-project.active')?.dataset?.projectId || 'unknown';
  }

  function storageKey(scene) {
    return `videogen:scene-collapsed:${projectId()}:${scene.dataset.sceneId || 'scene'}`;
  }

  function titleFor(scene) {
    const input = scene.querySelector('.f-title');
    return (input?.value || input?.getAttribute('value') || '').trim();
  }

  function durationFor(scene) {
    const input = scene.querySelector('.f-duration');
    const value = Number(input?.value || 0);
    return Number.isFinite(value) && value > 0 ? `${value:g}s`.replace(':g', '') : '';
  }

  function updateSummary(scene) {
    const summary = scene.querySelector('.scene-collapse-summary');
    if (!summary) return;
    const title = titleFor(scene);
    const duration = durationFor(scene);
    summary.textContent = [title, duration].filter(Boolean).join(' · ');
  }

  function setCollapsed(scene, collapsed, persist=true) {
    const head = scene.querySelector('.scene-head');
    if (!head) return;
    scene.dataset.collapsed = collapsed ? '1' : '0';
    [...scene.children].forEach(child => {
      if (child !== head) child.style.display = collapsed ? 'none' : '';
    });
    const button = scene.querySelector('.scene-collapse-toggle');
    if (button) {
      button.textContent = collapsed ? '▸ Развернуть' : '▾ Свернуть';
      button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      button.title = collapsed ? 'Развернуть сцену' : 'Свернуть сцену';
    }
    scene.style.paddingBottom = collapsed ? '10px' : '';
    updateSummary(scene);
    if (persist) {
      try { sessionStorage.setItem(storageKey(scene), collapsed ? '1' : '0'); } catch (_) {}
    }
  }

  function enhanceScene(scene, index) {
    if (scene.dataset.collapseUi === '1') {
      updateSummary(scene);
      return;
    }
    const head = scene.querySelector('.scene-head');
    if (!head) return;
    scene.dataset.collapseUi = '1';

    const badge = head.querySelector('.badge');
    const left = document.createElement('div');
    left.className = 'scene-collapse-left';
    left.style.cssText = 'display:flex;align-items:center;gap:8px;min-width:0;flex:1;';
    if (badge) left.appendChild(badge);

    const summary = document.createElement('span');
    summary.className = 'scene-collapse-summary muted';
    summary.style.cssText = 'font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:520px;';
    left.appendChild(summary);
    head.insertBefore(left, head.firstChild);

    const actions = head.querySelector('.scene-actions');
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'secondary scene-collapse-toggle';
    toggle.style.cssText = 'padding:7px 10px;white-space:nowrap;';
    toggle.onclick = () => setCollapsed(scene, scene.dataset.collapsed !== '1');
    if (actions) actions.insertBefore(toggle, actions.firstChild);
    else head.appendChild(toggle);

    scene.querySelector('.f-title')?.addEventListener('input', () => updateSummary(scene));
    scene.querySelector('.f-duration')?.addEventListener('input', () => updateSummary(scene));

    let collapsed = index > 0;
    try {
      const saved = sessionStorage.getItem(storageKey(scene));
      if (saved === '1' || saved === '0') collapsed = saved === '1';
    } catch (_) {}
    setCollapsed(scene, collapsed, false);
  }

  function ensureBulkControls() {
    const actions = document.querySelector('.project-actions');
    if (!actions || actions.querySelector('.scene-collapse-all')) return;

    const collapse = document.createElement('button');
    collapse.type = 'button';
    collapse.className = 'secondary scene-collapse-all';
    collapse.textContent = 'Свернуть все сцены';
    collapse.onclick = () => document.querySelectorAll(sceneSelector).forEach(scene => setCollapsed(scene, true));

    const expand = document.createElement('button');
    expand.type = 'button';
    expand.className = 'secondary scene-expand-all';
    expand.textContent = 'Развернуть все сцены';
    expand.onclick = () => document.querySelectorAll(sceneSelector).forEach(scene => setCollapsed(scene, false));

    actions.append(collapse, expand);
  }

  function enhance() {
    document.querySelectorAll(sceneSelector).forEach((scene, index) => enhanceScene(scene, index));
    ensureBulkControls();
  }

  const observer = new MutationObserver(enhance);
  observer.observe(document.documentElement, {childList:true, subtree:true});
  enhance();
})();
'''
    # Fix JavaScript formatting placeholder introduced above without involving the browser.
    script = script.replace("`${value:g}s`.replace(':g', '')", "`${value}s`")
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
