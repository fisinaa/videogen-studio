# Local AI providers

VideoGen routes local LLM, image generation and TTS between several providers and now includes automatic GPU orchestration.

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

The local llama.cpp model now creates production-ready image prompts instead of a single short `VISUAL` line.

Each new scene stores:

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

The English prompt is intended for local image models and is normally much more detailed than narration. It should not be a literary retelling.

The storyboard UI has **Обновить visual prompt** for rebuilding only these image-generation fields without rewriting narration/action or removing existing media candidates.

Endpoint:

```text
POST /api/projects/{project_id}/scenes/{scene_id}/visual-prompt/rebuild
```

Local/OpenAI image generation prefers `visual_prompt_en`. The negative prompt is appended as explicit `Avoid:` guidance.

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

VideoGen now has `app/services/model_orchestrator.py`.

When orchestration is enabled, the local image flow becomes:

```text
LLM/text request
  -> ensure videogen-llama.service is running

Local image request
  -> acquire image/GPU lock
  -> stop videogen-llama.service
  -> run stable-diffusion.cpp
  -> restart videogen-llama.service
  -> release lock
```

This prevents llama.cpp and the image model from competing for the GTX 1660 6 GB VRAM.

VideoGen uses a fixed user-level systemd unit instead of arbitrary shell commands from `.env`.

Example unit is committed at:

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

`/api/health` reports orchestration state:

```text
orchestrator.enabled
orchestrator.llm_active
orchestrator.gpu_busy
orchestrator.current_job
```

Important: do not set `MODEL_ORCHESTRATION_ENABLED=true` until the user systemd unit is installed and works.

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
