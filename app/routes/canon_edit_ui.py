from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-canon-edit.js")
async def videogen_canon_edit_js():
    script = r'''
(() => {
  let queued = false;

  function setEditable(card, editable) {
    card.dataset.editing = editable ? '1' : '0';
    card.querySelectorAll('.s-key,.s-name,.s-kind,.s-ru,.s-en,.s-block').forEach(field => {
      field.disabled = !editable;
    });
    const button = card.querySelector('.edit-text-suggestion');
    if (button) {
      button.textContent = editable ? 'Готово' : 'Исправить';
      button.style.background = editable ? '#2d7651' : '#6247aa';
    }
    card.style.borderColor = editable ? '#6f5bb0' : '#343b49';
  }

  function enhanceCard(card) {
    if (card.dataset.explicitEditUi === '1') return;
    card.dataset.explicitEditUi = '1';

    const accept = card.querySelector('.accept-text-suggestion');
    if (!accept) return;

    const actions = document.createElement('div');
    actions.className = 'canon-suggestion-actions';
    actions.style.cssText = 'display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:8px;';

    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'edit-text-suggestion secondary';
    edit.textContent = 'Исправить';
    edit.style.cssText = 'padding:7px 10px;background:#6247aa';
    edit.onclick = () => {
      if (card.dataset.accepted === '1') return;
      setEditable(card, card.dataset.editing !== '1');
      if (card.dataset.editing === '1') card.querySelector('.s-name')?.focus();
    };

    accept.parentElement?.insertBefore(actions, accept);
    actions.append(edit, accept);
    accept.style.marginTop = '0';

    setEditable(card, false);
  }

  function enhance() {
    document.querySelectorAll('.series-text-suggestion').forEach(enhanceCard);
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
    if (mutations.some(m => [...m.addedNodes].some(n => n.nodeType === 1 && (n.matches?.('.series-text-suggestion') || n.querySelector?.('.series-text-suggestion'))))) {
      schedule();
    }
  });
  observer.observe(root, {childList:true, subtree:true});
  enhance();
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
