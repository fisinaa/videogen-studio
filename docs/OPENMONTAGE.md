# OpenMontage render bridge

VideoGen owns project planning, local LLM prompts, image generation, asset selection, TTS and continuity. Final composition is delegated to the installed OpenMontage checkout.

## Configuration

```env
OPENMONTAGE_ROOT=/home/faa/OpenMontage
OPENMONTAGE_PYTHON=/home/faa/OpenMontage/.venv/bin/python
OPENMONTAGE_TIMEOUT_SECONDS=3600
VIDEOGEN_OPENMONTAGE_RUNNER=./scripts/openmontage_render.py
```

The VideoGen server must be started through `./run.sh` (or `uvicorn app.server:app`) so the OpenMontage API router is registered.

## Runtime status

```bash
curl -s http://127.0.0.1:8090/api/openmontage/status | jq
```

OpenMontage reports the render runtimes independently. Do not silently swap runtimes. If both HyperFrames and Remotion are available, the caller explicitly chooses one.

## Render a project

All scenes need a selected local image. TTS is optional, but selected audio is added to the narration timeline when available.

HyperFrames:

```bash
curl -s -X POST \
  'http://127.0.0.1:8090/api/openmontage/projects/PROJECT_ID/render?runtime=hyperframes' | jq
```

Remotion:

```bash
curl -s -X POST \
  'http://127.0.0.1:8090/api/openmontage/projects/PROJECT_ID/render?runtime=remotion' | jq
```

Output is written under the VideoGen project:

```text
data/projects/<project-id>/renders/final-hyperframes.mp4
data/projects/<project-id>/renders/final-remotion.mp4
```

The OpenMontage HyperFrames workspace is created under:

```text
/home/faa/OpenMontage/projects/videogen-<project-id>/hyperframes/
```

The response contains `download_url`, which can be opened in a browser or downloaded from VideoGen.

## What is exported

For each scene VideoGen builds an OpenMontage `asset_manifest` and `edit_decisions` artifact containing:

- selected scene image
- scene timing
- camera motion hint
- transitions
- selected TTS narration segment
- title/action metadata
- render runtime locked to the caller's explicit choice
- output media profile derived from the project's aspect ratio

Aspect ratio mapping:

```text
16:9 -> youtube_landscape
9:16 -> tiktok
1:1  -> instagram_feed
```

HyperFrames is currently the recommended first smoke test because it has already been validated on the target host. Once both runtimes are confirmed, VideoGen can expose both render actions in the main UI.
