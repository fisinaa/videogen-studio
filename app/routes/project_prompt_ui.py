from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.providers.llm.llama_cpp import OUTLINE_SYSTEM_PROMPT
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
    }


@router.get("/videogen-project-prompt.js")
async def project_prompt_js():
    script = r'''
(() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const projectId = () => new URL(location.href).searchParams.get('project') || document.querySelector('.recent-project.active')?.dataset?.projectId || null;
  let lastId = null;

  async function load() {
    const id = projectId();
    if (!id) {
      document.querySelector('.videogen-project-prompt')?.remove();
      lastId = null;
      return;
    }
    if (id === lastId && document.querySelector('.videogen-project-prompt')) return;

    let data;
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(id)}/llm-prompt`, {cache:'no-store'});
      data = await response.json();
      if (!response.ok) return;
    } catch (_) { return; }

    lastId = id;
    document.querySelector('.videogen-project-prompt')?.remove();
    const card = document.createElement('div');
    card.className = 'videogen-project-prompt';
    card.style.cssText = 'margin:12px 0;padding:13px;border:1px solid #35405a;border-radius:12px;background:#0d121a;';
    card.innerHTML = `
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

  const observer = new MutationObserver(load);
  observer.observe(document.documentElement, {childList:true,subtree:true});
  load();
  setInterval(load, 1000);
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
