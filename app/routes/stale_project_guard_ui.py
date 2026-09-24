from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-stale-project-guard.js")
async def videogen_stale_project_guard_js():
    script = r'''
(() => {
  if (window.__videogenStaleProjectGuard) return;
  window.__videogenStaleProjectGuard = true;

  const dead = new Set();
  const originalFetch = window.fetch.bind(window);

  function projectIdFromPath(value) {
    try {
      const url = new URL(typeof value === 'string' ? value : value?.url || '', location.origin);
      const match = url.pathname.match(/^\/api\/projects\/([^/]+)(?:\/|$)/);
      return match ? decodeURIComponent(match[1]) : '';
    } catch (_) {
      return '';
    }
  }

  function clearProject(id) {
    dead.add(id);
    const url = new URL(location.href);
    if (url.searchParams.get('project') === id) {
      url.searchParams.delete('project');
      history.replaceState({}, '', url.pathname + url.search + url.hash);
    }
    document.querySelectorAll('.recent-project').forEach(node => {
      if (node.dataset.projectId === id) node.classList.remove('active');
    });
    const result = document.getElementById('result');
    const status = document.getElementById('status');
    if (result && (!window.currentProject || window.currentProject?.id === id)) result.innerHTML = '';
    if (status) status.textContent = 'Выбранный проект уже удалён. Выбери существующий проект или создай новый.';
    window.dispatchEvent(new CustomEvent('videogen:project-missing', {detail:{projectId:id}}));
  }

  window.fetch = async function(input, init) {
    const method = String(init?.method || 'GET').toUpperCase();
    const id = method === 'GET' ? projectIdFromPath(input) : '';
    if (id && dead.has(id)) {
      return new Response(JSON.stringify({detail:'Project not found'}), {
        status: 404,
        headers: {'Content-Type':'application/json', 'X-VideoGen-Stale-Project':'1'}
      });
    }

    const response = await originalFetch(input, init);
    if (id && response.status === 404) clearProject(id);
    return response;
  };

  // Validate a project id left in a bookmarked/stale URL once, before polling
  // enhancement scripts get a chance to keep requesting it forever.
  const initialId = new URL(location.href).searchParams.get('project');
  if (initialId) {
    originalFetch(`/api/projects/${encodeURIComponent(initialId)}`, {cache:'no-store'})
      .then(response => { if (response.status === 404) clearProject(initialId); })
      .catch(() => {});
  }
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control":"no-store, max-age=0"})
