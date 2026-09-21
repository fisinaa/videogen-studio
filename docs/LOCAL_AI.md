# Local AI providers

VideoGen routes local LLM, image generation and TTS between several providers and includes automatic GPU orchestration.

## Image modes

```text
auto | local | local_fast | local_quality | local_next | openai
```

`auto` order:

```text
Local Next -> Local Quality -> Local Fast -> OpenAI
```

The web UI exposes **Local Fast**, **Local Quality**, **Local Next**, and **OpenAI Final** per scene.

## Structured visual prompt builder

The local llama.cpp model creates production-ready image prompts instead of a single short `VISUAL` line.

Each scene stores:

```text
visual_prompt_ru
visual_prompt_en
negative_prompt_en
media_search_query
```

`visual_prompt` remains for backward compatibility and mirrors `visual_prompt_en` on newly generated scenes.

The LLM is instructed to explicitly describe:

```text
main character and stable visual traits
action
environment
key props
spatial relationships
camera/framing/composition
lighting
mood
project visual style
```

The storyboard UI has **Обновить visual prompt** for rebuilding only the image-generation fields without rewriting narration/action or removing existing media candidates.

Endpoints:

```text
POST /api/projects/{project_id}/scenes/{scene_id}/visual-prompt/rebuild
POST /api/projects/{project_id}/visual-prompts/rebuild-all
```

Local/OpenAI image generation prefers `visual_prompt_en`. The negative prompt is appended as explicit `Avoid:` guidance.

## Visual Bible / continuity

Projects now keep a `storyboard.visual_bible` with:

```text
canonical project visual style
canonical character descriptions
continuity rules for colors, proportions, clothing and distinctive traits
recurring-location / recurring-prop consistency rules
```

Old projects receive a Visual Bible automatically when loaded. The image-generation prompt receives this continuity context so scene prompts do not drift as easily.

## Prepare All workflow

The UI now has **Prepare All**. It performs:

```text
1. rebuild visual prompts for every scene with the local LLM
2. generate one new local image candidate for every scene
3. generate missing narration audio
```

The preferred image profile is:

```text
Local Next, if configured
otherwise Local Quality
otherwise Local Fast
```

The generated images are added to `media_candidates`; existing selected images are not blindly overwritten.

Batch endpoints:

```text
POST /api/projects/{project_id}/media/generate-batch?provider=local_next
POST /api/projects/{project_id}/audio/generate-missing
```

## Piper local TTS (CPU)

```env
TTS_PROVIDER=auto
PIPER_BIN=/opt/piper/venv/bin/piper
PIPER_MODEL=/opt/piper/models/ru_RU-irina-medium.onnx
PIPER_TIMEOUT_SECONDS=120
```

Piper runs on CPU.

## Shared stable-diffusion.cpp runtime

```env
SD_CPP_BIN=/home/faa/stable-diffusion.cpp/build/bin/sd-cli
SD_CPP_TIMEOUT_SECONDS=600
SD_CPP_BACKEND=all=cuda0,te=cpu
SD_CPP_OFFLOAD_TO_CPU=true
SD_CPP_MAX_VRAM=-1
SD_CPP_DIFFUSION_FA=true
SD_CPP_THREADS=14
SD_CPP_VERBOSE=true
```

## Local Fast

```text
FLUX.1-schnell Q2_K
4 steps
CFG 1.0
```

```env
SD_CPP_VAE=/home/faa/models/flux/ae.safetensors
SD_CPP_CLIP_L=/home/faa/models/flux/clip_l.safetensors
SD_CPP_T5XXL=/home/faa/models/flux/t5xxl_fp16.safetensors
SD_CPP_DIFFUSION_MODEL=/home/faa/models/flux/flux1-schnell-q2_k.gguf
SD_CPP_STEPS=4
SD_CPP_CFG_SCALE=1.0
SD_CPP_SAMPLING_METHOD=euler
```

## Local Quality

```text
Z-Image-Turbo Q3_K
Qwen3-4B encoder
8 steps
CFG 1.0
```

```env
SD_CPP_QUALITY_DIFFUSION_MODEL=/home/faa/models/z-image/z_image_turbo-Q3_K.gguf
SD_CPP_QUALITY_VAE=/home/faa/models/flux/ae.safetensors
SD_CPP_QUALITY_LLM=/home/faa/models/z-image/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
SD_CPP_QUALITY_CLIP_L=
SD_CPP_QUALITY_T5XXL=
SD_CPP_QUALITY_STEPS=8
SD_CPP_QUALITY_CFG_SCALE=1.0
SD_CPP_QUALITY_SAMPLING_METHOD=euler
```

## Local Next

```text
FLUX.2 Klein 4B Q4_0
Qwen3-4B Q4_K_M text encoder
FLUX.2 small decoder/VAE
4 steps
CFG 1.0
```

```env
SD_CPP_NEXT_DIFFUSION_MODEL=/home/faa/models/flux2/flux-2-klein-4b-Q4_0.gguf
SD_CPP_NEXT_VAE=/home/faa/models/flux2/full_encoder_small_decoder.safetensors
SD_CPP_NEXT_LLM=/home/faa/models/flux2/Qwen3-4B-Q4_K_M.gguf
SD_CPP_NEXT_CLIP_L=
SD_CPP_NEXT_T5XXL=
SD_CPP_NEXT_STEPS=4
SD_CPP_NEXT_CFG_SCALE=1.0
SD_CPP_NEXT_SAMPLING_METHOD=euler
```

Download sources:

```text
Diffusion GGUF: leejet/FLUX.2-klein-4B-GGUF
Text encoder:    unsloth/Qwen3-4B-GGUF
VAE/decoder:     black-forest-labs/FLUX.2-small-decoder
```

FLUX.2 reference generation uses native `-r` when Character Reference is enabled. Older local profiles retain the conservative img2img path.

## Automatic GPU/model orchestration

VideoGen uses `app/services/model_orchestrator.py`.

Single local-image request:

```text
acquire GPU lock
stop videogen-llama.service
run stable-diffusion.cpp
restart videogen-llama.service
release lock
```

Batch local-image request:

```text
acquire GPU lock once
stop videogen-llama.service once
scene 1 image
scene 2 image
...
scene N image
restart videogen-llama.service once
release lock
```

This avoids repeatedly loading/unloading llama.cpp between scenes and prevents the LLM and image model from competing for the GTX 1660 VRAM.

Example unit:

```text
deploy/systemd/videogen-llama.service.example
```

Install it:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/videogen-llama.service.example ~/.config/systemd/user/videogen-llama.service
systemctl --user daemon-reload
systemctl --user enable --now videogen-llama.service
systemctl --user status videogen-llama.service
```

Then enable orchestration in the real `.env`:

```env
MODEL_ORCHESTRATION_ENABLED=true
LLM_SYSTEMD_UNIT=videogen-llama.service
LLM_RESTART_AFTER_IMAGE=true
LLM_START_TIMEOUT_SECONDS=90
LLM_STOP_TIMEOUT_SECONDS=30
GPU_LOCK_FILE=/tmp/videogen-gpu.lock
```

`/api/health` reports:

```text
orchestrator.enabled
orchestrator.llm_active
orchestrator.gpu_busy
orchestrator.current_job
orchestrator.image_batch_profile
```

## Scene image gallery

Each scene stores:

```text
selected_media
media_candidates[]
```

New generations are appended to `media_candidates` and do not overwrite an already selected image. Older OpenAI/local PNGs are recovered into the gallery when a project is loaded.

## Character Reference defaults

```text
Local Fast / Local Quality / Local Next
  Character Reference = OFF

OpenAI Final
  Character Reference = ON when available
```

For Local Next, Character Reference can be enabled manually to test FLUX.2 native reference conditioning.

## Permanent project deletion

The UI now has **Удалить проект совсем** and a delete button in the recent-project list.

Endpoint:

```text
DELETE /api/projects/{project_id}
```

This permanently removes the whole project directory, including `project.json`, generated images and audio. The action is intentionally confirmed in the UI and cannot be undone.
