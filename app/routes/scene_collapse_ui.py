from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-scene-collapse.js")
async def videogen_scene_collapse_js():
    script = r'''
(() => {
  const sceneSelector = '.scene-editor[data-scene-id]';
  let enhanceScheduled = false;

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
    return Number.isFinite(value) && value > 0 ? `${value}s` : '';
  }

  function updateSummary(scene) {
    const summary = scene.querySelector('.scene-collapse-summary');
    if (!summary) return;
    const title = titleFor(scene);
    const duration = durationFor(scene);
    const next = [title, duration].filter(Boolean).join(' · ');
    if (summary.textContent !== next) summary.textContent = next;
  }

  function setCollapsed(scene, collapsed, persist=true) {
    const head = scene.querySelector('.scene-head');
    if (!head) return;
    const value = collapsed ? '1' : '0';
    if (scene.dataset.collapsed !== value) scene.dataset.collapsed = value;

    [...scene.children].forEach(child => {
      if (child === head) return;
      const nextDisplay = collapsed ? 'none' : '';
      if (child.style.display !== nextDisplay) child.style.display = nextDisplay;
    });

    const button = scene.querySelector('.scene-collapse-toggle');
    if (button) {
      const nextText = collapsed ? '▸ Развернуть' : '▾ Свернуть';
      if (button.textContent !== nextText) button.textContent = nextText;
      const expanded = collapsed ? 'false' : 'true';
      if (button.getAttribute('aria-expanded') !== expanded) button.setAttribute('aria-expanded', expanded);
      button.title = collapsed ? 'Развернуть сцену' : 'Свернуть сцену';
    }

    const nextPadding = collapsed ? '10px' : '';
    if (scene.style.paddingBottom !== nextPadding) scene.style.paddingBottom = nextPadding;
    updateSummary(scene);

    if (persist) {
      try { sessionStorage.setItem(storageKey(scene), value); } catch (_) {}
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

  function scheduleEnhance() {
    if (enhanceScheduled) return;
    enhanceScheduled = true;
    requestAnimationFrame(() => {
      enhanceScheduled = false;
      enhance();
    });
  }

  function mutationNeedsEnhance(records) {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (!(node instanceof Element)) continue;
        if (node.matches?.(sceneSelector) || node.querySelector?.(sceneSelector)) return true;
        if (node.matches?.('.project-actions') || node.querySelector?.('.project-actions')) return true;
      }
    }
    return false;
  }

  const observer = new MutationObserver(records => {
    if (mutationNeedsEnhance(records)) scheduleEnhance();
  });
  observer.observe(document.getElementById('result') || document.body, {childList:true, subtree:true});
  enhance();
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
