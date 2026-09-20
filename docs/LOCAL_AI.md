# Local AI providers

VideoGen routes TTS and image generation between local providers and OpenAI.

## Modes

```env
TTS_PROVIDER=auto
IMAGE_PROVIDER=auto
```

Image route values:

```text
auto | local | local_fast | local_quality | openai
```

`auto` order is now:

```text
Local Quality -> Local Fast -> OpenAI
```

The web UI exposes explicit **Local Fast**, **Local Quality**, and **OpenAI Final** actions per scene.

## Piper local TTS (CPU)

Current tested layout:

```text
/opt/piper/venv/bin/piper
/opt/piper/models/ru_RU-irina-medium.onnx
/opt/piper/models/ru_RU-irina-medium.onnx.json
```

```env
TTS_PROVIDER=auto
PIPER_BIN=/opt/piper/venv/bin/piper
PIPER_MODEL=/opt/piper/models/ru_RU-irina-medium.onnx
PIPER_TIMEOUT_SECONDS=120
```

Piper runs on CPU and leaves the GTX 1660 free for image/LLM work.

## Local Fast profile

Current tested hardware/profile:

```text
GPU: GTX 1660 6 GB
Model: FLUX.1-schnell Q2_K
Text encoders: CPU
Diffusion: CUDA
Steps: 4
Threads: 14
Flash attention: diffusion only
CPU offload: enabled
VRAM reserve: --max-vram -1
```

```env
SD_CPP_BIN=/home/faa/stable-diffusion.cpp/build/bin/sd-cli
SD_CPP_VAE=/home/faa/models/flux/ae.safetensors
SD_CPP_CLIP_L=/home/faa/models/flux/clip_l.safetensors
SD_CPP_T5XXL=/home/faa/models/flux/t5xxl_fp16.safetensors

SD_CPP_DIFFUSION_MODEL=/home/faa/models/flux/flux1-schnell-q2_k.gguf
SD_CPP_STEPS=4
SD_CPP_CFG_SCALE=1.0
SD_CPP_SAMPLING_METHOD=euler

SD_CPP_TIMEOUT_SECONDS=600
SD_CPP_BACKEND=all=cuda0,te=cpu
SD_CPP_OFFLOAD_TO_CPU=true
SD_CPP_MAX_VRAM=-1
SD_CPP_DIFFUSION_FA=true
SD_CPP_THREADS=14
SD_CPP_VERBOSE=true
```

## Local Quality profile

A second local model can be configured independently. The UI enables **Local Quality** automatically when the required files exist.

For FLUX-style models, configure a diffusion model and optionally override CLIP/T5/VAE:

```env
SD_CPP_QUALITY_DIFFUSION_MODEL=/path/to/model.gguf
SD_CPP_QUALITY_STEPS=8
SD_CPP_QUALITY_CFG_SCALE=1.0
SD_CPP_QUALITY_SAMPLING_METHOD=euler
SD_CPP_QUALITY_VAE=
SD_CPP_QUALITY_CLIP_L=
SD_CPP_QUALITY_T5XXL=
```

For newer pipelines such as Z-Image-Turbo, use an LLM text encoder instead:

```env
SD_CPP_QUALITY_DIFFUSION_MODEL=/home/faa/models/z-image/z_image_turbo-Q3_K.gguf
SD_CPP_QUALITY_VAE=/home/faa/models/flux/ae.safetensors
SD_CPP_QUALITY_LLM=/home/faa/models/z-image/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
SD_CPP_QUALITY_STEPS=8
SD_CPP_QUALITY_CFG_SCALE=1.0
SD_CPP_QUALITY_SAMPLING_METHOD=euler
```

When `SD_CPP_QUALITY_LLM` points to a real file, VideoGen builds the quality command with `--llm` instead of `--clip_l/--t5xxl`.

## Scene image gallery

Each scene now stores:

```text
selected_media
media_candidates[]
```

New generations are appended to `media_candidates` and no longer overwrite an already selected image. The first image becomes selected only when the scene has no selection yet.

The UI shows all candidates for the scene, including recovered older OpenAI/local PNG files already present in the project media directory. You can:

```text
Choose     -> make a candidate the selected scene image
Remove     -> remove it from the gallery (the file remains on disk for now)
```

Stock search results are added to the candidate gallery when selected.

Project loading/recovery scans both:

```text
scene-XXX-openai-*.png
scene-XXX-local-*.png
```

so older generated frames that were previously overwritten in `selected_media` can become visible again.

## Character Reference behavior

Current FLUX Q2_K img2img follows Character Reference too strongly, so defaults remain:

```text
Local Fast / Local Quality
  Character Reference = OFF

OpenAI Final
  Character Reference = ON when available
```

Each scene has a checkbox to override this manually.

## Recommended runtime layout

```text
CPU:
  Piper TTS
  FFmpeg
  image text encoders where possible

GPU:
  llama.cpp OR stable-diffusion.cpp
```

On a 6 GB GPU, schedule llama.cpp and local image generation rather than keeping both resident. A GPU resource manager can automate stop/start of llama-server around local image jobs later.
