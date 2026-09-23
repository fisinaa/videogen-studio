from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-series-text-refs-collapse.js")
async def videogen_series_text_refs_collapse_js():
    script = r'''
(() => {
  const panelSelector = '.videogen-text-refs-panel';
  const storageKey = 'videogen:text-refs-collapsed';
  let queued = false;

  function setCollapsed(panel, collapsed, persist=true) {
    panel.dataset.collapsed = collapsed ? '1' : '0';
    const header = panel.querySelector('.tr-collapse-header');
    [...panel.children].forEach(child => {
      if (child !== header) child.style.display = collapsed ? 'none' : '';
    });
    const button = panel.querySelector('.tr-collapse-toggle');
    if (button) {
      button.textContent = collapsed ? '▸ Развернуть' : '▾ Свернуть';
      button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      button.title = collapsed ? 'Развернуть Text References' : 'Свернуть Text References';
    }
    panel.style.paddingBottom = collapsed ? '10px' : '';
    if (persist) {
      try { sessionStorage.setItem(storageKey, collapsed ? '1' : '0'); } catch (_) {}
    }
  }

  function enhancePanel(panel) {
    if (panel.dataset.collapseReady === '1') return;
    const first = panel.firstElementChild;
    if (!first) return;

    panel.dataset.collapseReady = '1';
    first.classList.add('tr-collapse-header');

    const title = first.querySelector('b');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary tr-collapse-toggle';
    button.style.cssText = 'padding:7px 10px;white-space:nowrap;';
    button.onclick = () => setCollapsed(panel, panel.dataset.collapsed !== '1');

    // Put collapse control next to the existing canon-assignment action when possible.
    const assign = first.querySelector('.tr-assign-scenes');
    if (assign) assign.insertAdjacentElement('afterend', button);
    else first.appendChild(button);

    let collapsed = false;
    try {
      const saved = sessionStorage.getItem(storageKey);
      if (saved === '1' || saved === '0') collapsed = saved === '1';
    } catch (_) {}
    setCollapsed(panel, collapsed, false);

    if (title) title.title = 'Text References / Visual Aliases';
  }

  function enhance() {
    document.querySelectorAll(panelSelector).forEach(enhancePanel);
  }

  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      enhance();
    });
  }

  const root = document.getElementById('result') || document.body;
  const observer = new MutationObserver(mutations => {
    if (mutations.some(m => [...m.addedNodes].some(n =>
      n.nodeType === 1 && (n.matches?.(panelSelector) || n.querySelector?.(panelSelector))
    ))) schedule();
  });
  observer.observe(root, {childList:true, subtree:true});
  enhance();
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
