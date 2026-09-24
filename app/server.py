from starlette.responses import Response

from app.main import app
from app.services.llm_quality import install_llm_quality
from app.services.series_continuity import install_series_continuity
from app.services.storyboard_quality import install_storyboard_quality


# Patch app.main helpers before route modules import references to them (pipeline.py
# imports _generate_scene_image directly).
install_series_continuity()
install_llm_quality()
install_storyboard_quality()

from app.routes.canon_edit_ui import router as canon_edit_ui_router
from app.routes.media_files import router as media_files_router
from app.routes.media_library import router as media_library_router
from app.routes.motion import router as motion_router
from app.routes.motion_progress_ui import router as motion_progress_ui_router
from app.routes.openmontage import router as openmontage_router
from app.routes.pipeline import router as pipeline_router
from app.routes.pipeline_progress_ui import router as pipeline_progress_ui_router
from app.routes.production import router as production_router
from app.routes.project_prompt_ui import router as project_prompt_ui_router
from app.routes.project_tools_ui import router as project_tools_ui_router
from app.routes.scene_collapse_ui import router as scene_collapse_ui_router
from app.routes.series import router as series_router
from app.routes.series_text_ai import router as series_text_ai_router
from app.routes.series_text_refs_collapse_ui import router as series_text_refs_collapse_ui_router
from app.routes.series_text_refs_ui import router as series_text_refs_ui_router
from app.routes.series_ui import router as series_ui_router
from app.routes.story_repair import router as story_repair_router
from app.routes.story_repair_ui import router as story_repair_ui_router
from app.routes.storyboard_openai import router as storyboard_openai_router
from app.routes.storyboard_provider_ui import router as storyboard_provider_ui_router
from app.routes.ui_tools import router as ui_tools_router
from app.routes.workflow_ui import router as workflow_ui_router


app.include_router(openmontage_router)
app.include_router(production_router)
app.include_router(motion_router)
app.include_router(media_files_router)
app.include_router(media_library_router)
app.include_router(pipeline_router)
app.include_router(series_router)
app.include_router(series_text_ai_router)
app.include_router(story_repair_router)
app.include_router(storyboard_openai_router)
app.include_router(storyboard_provider_ui_router)
app.include_router(project_prompt_ui_router)
app.include_router(ui_tools_router)
app.include_router(project_tools_ui_router)
app.include_router(scene_collapse_ui_router)
app.include_router(series_ui_router)
app.include_router(series_text_refs_ui_router)
app.include_router(series_text_refs_collapse_ui_router)
app.include_router(workflow_ui_router)
app.include_router(canon_edit_ui_router)
app.include_router(story_repair_ui_router)
app.include_router(motion_progress_ui_router)
app.include_router(pipeline_progress_ui_router)


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
    markers = [
        '<script src="/videogen-enhancements.js"></script>',
        '<script src="/videogen-project-tools.js"></script>',
        '<script src="/videogen-project-prompt.js"></script>',
        '<script src="/videogen-scene-collapse.js"></script>',
        '<script src="/videogen-series.js"></script>',
        '<script src="/videogen-series-text-refs.js"></script>',
        '<script src="/videogen-series-text-refs-collapse.js"></script>',
        '<script src="/videogen-workflow.js"></script>',
        '<script src="/videogen-canon-edit.js"></script>',
        '<script src="/videogen-story-repair.js"></script>',
        '<script src="/videogen-storyboard-provider.js"></script>',
        '<script src="/videogen-motion-progress.js"></script>',
        '<script src="/videogen-pipeline-progress.js"></script>',
    ]
    injection = "\n".join(marker for marker in markers if marker not in text)
    if injection:
        text = text.replace("</body>", injection + "\n</body>")

    headers = dict(response.headers)
    headers.pop("content-length", None)
    return Response(
        content=text,
        status_code=response.status_code,
        headers=headers,
        media_type="text/html",
    )
