# VideoGen motion / image-to-video

VideoGen treats AI motion as a separate stage from final composition:

`scene image -> motion provider -> scene MP4 -> OpenMontage -> final MP4`

The application is model-agnostic. A local motion runner is configured through `MOTION_COMMAND`, so the backend can later be replaced without changing project JSON, the web UI, or the OpenMontage adapter.

## Environment

```env
MOTION_PROVIDER=command
MOTION_TIMEOUT_SECONDS=3600
MOTION_DEFAULT_DURATION_SECONDS=5
MOTION_MAX_DURATION_SECONDS=6
MOTION_WIDTH_16_9=768
MOTION_HEIGHT_16_9=432
MOTION_WIDTH_9_16=432
MOTION_HEIGHT_9_16=768
MOTION_WIDTH_1_1=512
MOTION_HEIGHT_1_1=512
```

`MOTION_COMMAND` supports these placeholders:

- `{input}` selected scene image
- `{output}` MP4 target path
- `{prompt_file}` UTF-8 motion prompt file
- `{duration}` requested duration
- `{width}` requested width
- `{height}` requested height

Example command for the optional Stable Video Diffusion runner included in this repository:

```env
MOTION_COMMAND=.venv/bin/python scripts/svd_image_to_video.py --input {input} --output {output} --prompt-file {prompt_file} --duration {duration} --width {width} --height {height}
```

## Optional SVD dependencies

Use the same Python environment as VideoGen only if you are comfortable adding heavy ML dependencies. A separate venv and wrapper command are also valid.

Typical packages needed by `scripts/svd_image_to_video.py`:

```bash
pip install torch diffusers transformers accelerate 'imageio[ffmpeg]'
```

The first run downloads `stabilityai/stable-video-diffusion-img2vid-xt`, so disk and network access are required. The runner enables CPU offload and forward chunking to reduce VRAM pressure. On a 6 GB GPU it can still be slow or fail depending on driver/model memory; the motion API is intentionally designed so a better runner can be substituted later.

## API

```text
GET  /api/motion/status
GET  /api/motion/projects/{project_id}/scenes/{scene_id}
POST /api/motion/projects/{project_id}/scenes/{scene_id}/generate
POST /api/motion/projects/{project_id}/scenes/{scene_id}/select/{asset_id}
POST /api/motion/projects/{project_id}/scenes/{scene_id}/reset
DELETE /api/motion/projects/{project_id}/scenes/{scene_id}/candidates/{asset_id}
```

A successful generation automatically selects the new MP4 and switches the scene to `motion_mode=image_to_video`. OpenMontage already prefers `selected_motion_media` over the source image when that mode is active.
