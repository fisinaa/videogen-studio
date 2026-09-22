from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-pipeline-progress.js")
async def videogen_pipeline_progress_js():
    script = r'''
(() => {
  const LABELS = {images:'Картинки', audio:'Озвучка', motion:'AI Motion', sync:'Синхронизация'};

  async function status() {
    const r = await fetch('/api/pipeline/status', {cache:'no-store'});
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  function render(data) {
    const state = document.querySelector('.project-production-state');
    if (!state || !data) return;
    const total = Number(data.total || 0);
    const current = Number(data.current || 0);
    const pct = total ? Math.min(100, Math.round(current * 100 / total)) : 0;
    const stage = LABELS[data.stage] || data.stage || '';
    const scene = data.scene_id ? ` · ${data.scene_id}` : '';
    state.innerHTML = `<b>${stage}</b> ${current}/${total} · ${pct}%${scene} · ${Number(data.elapsed_seconds || 0).toFixed(1)} сек<div style="height:5px;background:#202733;border-radius:5px;margin-top:6px;overflow:hidden"><div style="width:${pct}%;height:100%;background:#7387b8"></div></div><div style="margin-top:4px">${String(data.detail || '')}</div>`;
  }

  function start() {
    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      try {
        const data = await status();
        render(data);
        if (data.state === 'complete' || data.state === 'complete-with-errors') {
          stopped = true;
          return;
        }
      } catch (_) {}
      setTimeout(tick, 700);
    };
    tick();
  }

  document.addEventListener('click', event => {
    if (event.target.closest?.('.project-animate-all, .project-build-episode')) {
      setTimeout(start, 200);
    }
  }, true);
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
