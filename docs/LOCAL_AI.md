# Local AI providers

VideoGen routes TTS and image generation between local providers and OpenAI.

## Modes

```env
TTS_PROVIDER=auto       # auto | piper | openai
IMAGE_PROVIDER=auto     # auto | local | openai
```

`auto` remains the default backend route for API calls that do not choose a provider explicitly. The web UI now exposes explicit **Local Draft** and **OpenAI Final** actions per scene.

## Piper local TTS (CPU)

Current tested layout:

```text
/opt/piper/venv/bin/piper
/opt/piper/models/ru_RU-irina-medium.onnx
/opt/piper/models/ru_RU-irina-medium.onnx.json
```

`.env`:

```env
TTS_PROVIDER=auto
PIPER_BIN=/opt/piper/venv/bin/piper
PIPER_MODEL=/opt/piper/models/ru_RU-irina-medium.onnx
PIPER_TIMEOUT_SECONDS=120
```

Piper receives UTF-8 text on stdin and writes WAV files to the project `audio/` directory. It runs on CPU and leaves the GTX 1660 free for LLM/image work.

## Local image generation with stable-diffusion.cpp

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

`.env`:

```env
IMAGE_PROVIDER=auto
SD_CPP_BIN=/home/faa/stable-diffusion.cpp/build/bin/sd-cli
SD_CPP_DIFFUSION_MODEL=/home/faa/models/flux/flux1-schnell-q2_k.gguf
SD_CPP_VAE=/home/faa/models/flux/ae.safetensors
SD_CPP_CLIP_L=/home/faa/models/flux/clip_l.safetensors
SD_CPP_T5XXL=/home/faa/models/flux/t5xxl_fp16.safetensors
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
SD_CPP_WIDTH_16_9=768
SD_CPP_HEIGHT_16_9=432
SD_CPP_WIDTH_9_16=432
SD_CPP_HEIGHT_9_16=768
SD_CPP_WIDTH_1_1=512
SD_CPP_HEIGHT_1_1=512
```

Equivalent standalone profile:

```bash
cd ~/stable-diffusion.cpp

./build/bin/sd-cli \
  --diffusion-model ~/models/flux/flux1-schnell-q2_k.gguf \
  --vae ~/models/flux/ae.safetensors \
  --clip_l ~/models/flux/clip_l.safetensors \
  --t5xxl ~/models/flux/t5xxl_fp16.safetensors \
  -p "cute small gray mouse standing near a river, children's animated movie, cinematic forest background, soft morning light" \
  -W 512 -H 512 \
  --steps 4 \
  --cfg-scale 1.0 \
  --sampling-method euler \
  --backend all=cuda0,te=cpu \
  --offload-to-cpu \
  --max-vram -1 \
  --diffusion-fa \
  -t 14 \
  -o ~/flux-test.png \
  -v
```

## Image workflow

The tested FLUX Q2_K img2img path follows a Character Reference too strongly and can preserve the neutral reference pose/background instead of following the scene prompt. Therefore the default workflow is deliberately split:

```text
Local Draft
  provider = local
  Character Reference = OFF by default
  purpose = composition, environment, action, cheap scene drafts

OpenAI Final
  provider = openai
  Character Reference = ON by default when it exists
  purpose = final frames and stronger character consistency
```

Each scene has a **Use Character Reference** checkbox, so either default can be overridden manually. The backend endpoint also accepts `provider=local|openai|auto` and `use_reference=true|false`.

Mass actions are separated into **All draft local** and **All final OpenAI**. OpenAI final generation may incur API cost.

A better local identity workflow can later replace img2img with IP-Adapter, PuLID, Flux Kontext/reference conditioning, or a character LoRA. Until then, reference-off local drafts avoid the current over-attachment problem.

## Provider behavior

TTS auto order:

```text
Piper local -> OpenAI TTS
```

Legacy image auto order:

```text
stable-diffusion.cpp local -> OpenAI Image
```

The explicit UI buttons bypass that ambiguity and select the requested provider directly.

## Recommended runtime layout

```text
CPU:
  Piper TTS
  FFmpeg
  FLUX text encoders

GPU:
  llama.cpp OR stable-diffusion.cpp
```

On a 6 GB GPU, schedule llama.cpp and local image generation rather than expecting both models to remain fully resident in VRAM. A GPU resource manager is the next layer to automate stop/start of llama-server around local image jobs.
