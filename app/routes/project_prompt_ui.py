from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.config import settings
from app.providers.llm.llama_cpp import OUTLINE_SYSTEM_PROMPT
from app.services.model_orchestrator import model_orchestrator
from app.storage import project_store


router = APIRouter(tags=["project-prompt"])


def _outline_user_prompt(request) -> str:
    target_scene_count = max(1, round(request.duration_seconds / 10))
    return (
        f"User idea:\n{request.prompt}\n\n"
        f"Project type: {request.project_type}\n"
        f"Aspect ratio: {request.aspect_ratio}\n"
        f"Target duration: {request.duration_seconds} seconds\n"
        f"Required SCENE lines: exactly {target_scene_count}\n"
        f"Language for TITLE, LOGLINE, CHARACTER and SCENE text: {request.language}\n\n"
        f"Return exactly {target_scene_count} SCENE: lines. Keep each scene idea very short."
    )


@router.get("/api/projects/{project_id}/llm-prompt")
async def project_llm_prompt(project_id: str):
    project = project_store.load(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "project_id": project.id,
        "request_prompt": project.request.prompt,
        "system_prompt": OUTLINE_SYSTEM_PROMPT,
        "outline_user_prompt": _outline_user_prompt(project.request),
        "project_type": project.request.project_type,
        "aspect_ratio": project.request.aspect_ratio,
        "duration_seconds": project.request.duration_seconds,
        "language": project.request.language,
        "requested_llm_profile": project.request.llm_profile,
        "storyboard_llm": project.storyboard.llm_generation,
        "scene_llm": {
            scene.id: scene.llm_generation
            for scene in project.storyboard.scenes
            if scene.llm_generation is not None
        },
        "llm_defaults": {
            "storyboard": settings.llm_profile_storyboard,
            "visual_prompt": settings.llm_profile_visual_prompt,
            "rewrite": settings.llm_profile_rewrite,
        },
        "llm_status": await model_orchestrator.status(),
    }


@router.get("/videogen-project-prompt.js")
async def project_prompt_js():
    script = r'''
(() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const projectId = () => new URL(location.href).searchParams.get('project') || document.querySelector('.recent-project.active')?.dataset?.projectId || null;
  let lastId = null;

  function ensureProfileSelector() {
    const form = document.getElementById('project-form');
    if (!form || document.getElementById('llm_profile')) return;
    const row = form.querySelector('.row');
    if (!row) return;
    const label = document.createElement('label');
    label.innerHTML = `LLM для storyboard<select id="llm_profile"><option value="quality" selected>Quality — Qwen3 14B</option><option value="fast">Fast — Qwen3 8B</option></select>`;
    row.appendChild(label);
  }

  if (!window.__videogenLlmFetchWrapped) {
    window.__videogenLlmFetchWrapped = true;
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init={}) => {
      try {
        const url = typeof input === 'string' ? input : input?.url || '';
        const method = String(init?.method || 'GET').toUpperCase();
        if (method === 'POST' && /\/api\/projects\/?$/.test(url) && typeof init.body === 'string') {
          const payload = JSON.parse(init.body);
          if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
            payload.llm_profile = document.getElementById('llm_profile')?.value || 'quality';
            init = {...init, body: JSON.stringify(payload)};
          }
        }
      } catch (_) {}
      return originalFetch(input, init);
    };
  }

  function runLabel(meta) {
    if (!meta) return '<span class="muted">Для старого проекта metadata модели не сохранена.</span>';
    const seconds = Number(meta.duration_seconds || 0).toFixed(1);
    return `<b>${esc(meta.profile)}</b> · ${esc(meta.model_name)} · ${seconds} сек · ${esc(meta.calls || 0)} LLM выз.`;
  }

  function annotateScenes(sceneMeta) {
    document.querySelectorAll('.scene-editor[data-scene-id]').forEach(editor => {
      const id = editor.dataset.sceneId;
      const meta = sceneMeta?.[id];
      let badge = editor.querySelector('.scene-llm-meta');
      if (!meta) { badge?.remove(); return; }
      if (!badge) {
        badge = document.createElement('span');
        badge.className = 'scene-llm-meta muted';
        badge.style.cssText = 'font-size:11px;margin-left:7px;';
        editor.querySelector('.scene-head')?.appendChild(badge);
      }
      badge.innerHTML = `LLM: ${esc(meta.model_name)} · ${Number(meta.duration_seconds||0).toFixed(1)} сек · ${esc(meta.operation||'')}`;
    });
  }

  async function load(force=false) {
    ensureProfileSelector();
    const id = projectId();
    if (!id) {
      document.querySelector('.videogen-project-prompt')?.remove();
      lastId = null;
      return;
    }
    if (!force && id === lastId && document.querySelector('.videogen-project-prompt')) return;

    let data;
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(id)}/llm-prompt`, {cache:'no-store'});
      data = await response.json();
      if (!response.ok) return;
    } catch (_) { return; }

    annotateScenes(data.scene_llm || {});
    lastId = id;
    document.querySelector('.videogen-project-prompt')?.remove();
    const card = document.createElement('div');
    card.className = 'videogen-project-prompt';
    card.style.cssText = 'margin:12px 0;padding:13px;border:1px solid #35405a;border-radius:12px;background:#0d121a;';
    const active = data.llm_status?.active_profile
      ? `${esc(data.llm_status.active_profile)} · ${esc(data.llm_status.active_model || '')}`
      : 'LLM сейчас выключена (on-demand)';
    card.innerHTML = `
      <div style="display:flex;gap:12px;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;margin-bottom:9px">
        <div><b>LLM storyboard</b><div style="margin-top:4px">${runLabel(data.storyboard_llm)}</div></div>
        <div class="muted" style="font-size:12px;text-align:right">Сейчас: ${active}<br>Defaults: storyboard=${esc(data.llm_defaults?.storyboard)} · visual=${esc(data.llm_defaults?.visual_prompt)} · rewrite=${esc(data.llm_defaults?.rewrite)}</div>
      </div>
      <details>
        <summary style="cursor:pointer"><b>Промпт проекта / что отправлено LLM</b></summary>
        <div style="margin-top:10px">
          <div class="muted" style="font-size:12px;margin-bottom:4px">Исходный запрос пользователя</div>
          <pre style="white-space:pre-wrap;word-break:break-word;background:#090c11;border:1px solid #262e3b;border-radius:8px;padding:10px">${esc(data.request_prompt)}</pre>
          <div class="muted" style="font-size:12px;margin:10px 0 4px">Фактический user prompt для outline</div>
          <pre style="white-space:pre-wrap;word-break:break-word;background:#090c11;border:1px solid #262e3b;border-radius:8px;padding:10px">${esc(data.outline_user_prompt)}</pre>
          <details style="margin-top:8px"><summary>System prompt</summary><pre style="white-space:pre-wrap;word-break:break-word;background:#090c11;border:1px solid #262e3b;border-radius:8px;padding:10px">${esc(data.system_prompt)}</pre></details>
        </div>
      </details>`;

    const production = document.querySelector('.videogen-project-production');
    const actions = document.querySelector('.project-actions');
    const firstScene = document.querySelector('.scene-editor');
    if (production) production.insertAdjacentElement('beforebegin', card);
    else if (actions) actions.insertAdjacentElement('beforebegin', card);
    else if (firstScene) firstScene.insertAdjacentElement('beforebegin', card);
    else document.getElementById('result')?.prepend(card);
  }

  const observer = new MutationObserver(() => { ensureProfileSelector(); load(false); });
  observer.observe(document.documentElement, {childList:true,subtree:true});
  ensureProfileSelector();
  load(true);
  setInterval(() => load(true), 3000);
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
