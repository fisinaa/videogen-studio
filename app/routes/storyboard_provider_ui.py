from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["storyboard-provider-ui"])


@router.get("/videogen-storyboard-provider.js")
async def videogen_storyboard_provider_js():
    script = r'''
(() => {
  const form = document.getElementById('project-form');
  if (!form || document.getElementById('storyboard_provider')) return;

  const label = document.createElement('label');
  label.style.marginTop = '4px';
  label.innerHTML = `Storyboard LLM
    <select id="storyboard_provider">
      <option value="local">Local Quality — Qwen3 14B</option>
      <option value="openai">OpenAI — GPT-5 mini</option>
    </select>
    <div class="muted storyboard-provider-note" style="font-size:12px;margin-top:-8px;margin-bottom:12px">
      OpenAI используется только для storyboard/текста сцен; изображения остаются выбранным image provider.
    </div>`;

  const submit = form.querySelector('#submit');
  form.insertBefore(label, submit);
  const select = label.querySelector('#storyboard_provider');
  const note = label.querySelector('.storyboard-provider-note');

  const saved = sessionStorage.getItem('videogen-storyboard-provider');
  if (saved === 'openai' || saved === 'local') select.value = saved;
  select.addEventListener('change', () => {
    sessionStorage.setItem('videogen-storyboard-provider', select.value);
  });

  fetch('/api/storyboard-provider/status', {cache:'no-store'})
    .then(r => r.json())
    .then(data => {
      const openai = select.querySelector('option[value="openai"]');
      if (openai) {
        openai.textContent = `OpenAI — ${data.openai_model || 'GPT-5 mini'}`;
        openai.disabled = !data.openai;
      }
      if (!data.openai && select.value === 'openai') select.value = 'local';
      if (!data.openai) note.textContent = 'OpenAI storyboard недоступен: в .env не задан OPENAI_API_KEY.';
    })
    .catch(() => {});

  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, init={}) => {
    let url = typeof input === 'string' ? input : input?.url;
    const method = String(init?.method || (typeof input !== 'string' ? input?.method : '') || 'GET').toUpperCase();
    if (method === 'POST' && url === '/api/projects' && select.value === 'openai') {
      if (typeof input === 'string') input = '/api/projects/openai';
      else input = new Request('/api/projects/openai', input);
    }
    return originalFetch(input, init);
  };
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store"})
