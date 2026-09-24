from fastapi import APIRouter
from fastapi.responses import Response


router = APIRouter(tags=["ui"])


@router.get("/videogen-openai-image-errors.js")
async def videogen_openai_image_errors_js():
    script = r'''
(() => {
  if (window.__videogenImageErrorDetailsInstalled) return;
  window.__videogenImageErrorDetailsInstalled = true;

  const previousFetch = window.fetch.bind(window);
  window.fetch = async function(input, init) {
    const response = await previousFetch(input, init);
    try {
      const url = typeof input === 'string' ? input : input?.url || '';
      if (url.includes('/media/generate-batch')) {
        const copy = response.clone();
        copy.json().then(data => {
          const errors = Array.isArray(data?.errors) ? data.errors : [];
          if (!errors.length) return;
          const lines = errors.map(item => `${item.scene_id || 'scene'}: ${item.error || 'unknown error'}`);
          // The original handler updates #bulk-progress immediately after fetch returns.
          // Wait one tick, then append the concrete provider errors instead of only a count.
          setTimeout(() => {
            const box = document.getElementById('bulk-progress');
            if (!box) return;
            const details = document.createElement('details');
            details.open = true;
            details.style.marginTop = '8px';
            details.innerHTML = `<summary style="color:#ffb0b0">Ошибки генерации (${errors.length})</summary><pre style="white-space:pre-wrap;word-break:break-word;margin:8px 0 0;padding:9px;background:#160d0d;border:1px solid #5c3030;border-radius:8px;color:#ffb0b0"></pre>`;
            details.querySelector('pre').textContent = lines.join('\n\n');
            box.appendChild(details);
          }, 50);
        }).catch(() => {});
      }
    } catch (_) {}
    return response;
  };
})();
'''
    return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-store, max-age=0"})
