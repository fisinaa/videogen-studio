# Local AI providers

VideoGen routes TTS and image generation between local providers and OpenAI.

## Modes

```env
TTS_PROVIDER=auto
IMAGE_PROVIDER=auto
```

Image route values:

```text
auto | local | local_fast | local_quality | local_next | openai
```

`auto` order:

```text
Local Next -> Local Quality -> Local Fast -> OpenAI
```

The web UI exposes explicit **Local Fast**, **Local Quality**, **Local Next**, and **OpenAI Final** actions per scene.

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

Tested baseline:

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

Current quality candidate:

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

Experimental newer profile:

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

Download sources used for this profile:

```text
Diffusion GGUF: leejet/FLUX.2-klein-4B-GGUF
Text encoder:    unsloth/Qwen3-4B-GGUF
VAE/decoder:     black-forest-labs/FLUX.2-small-decoder
```

FLUX.2 reference generation uses its native `-r` reference input when Character Reference is enabled, instead of the older img2img `-i` path.

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

For Local Next you can manually enable Character Reference to test FLUX.2 native reference conditioning.

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
