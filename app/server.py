from starlette.responses import Response

from app.main import app
from app.routes.motion import router as motion_router
from app.routes.openmontage import router as openmontage_router
from app.routes.production import router as production_router
from app.routes.ui_tools import router as ui_tools_router


app.include_router(openmontage_router)
app.include_router(production_router)
app.include_router(motion_router)
app.include_router(ui_tools_router)


@app.middleware("http")
async def inject_videogen_ui_tools(request, call_next):
    response = await call_next(request)
    if request.url.path != "/":
        return response
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type.lower():
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    text = body.decode("utf-8", errors="replace")
    marker = '<script src="/videogen-enhancements.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")

    headers = dict(response.headers)
    headers.pop("content-length", None)
    return Response(
        content=text,
        status_code=response.status_code,
        headers=headers,
        media_type="text/html",
    )
