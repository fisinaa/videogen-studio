# Local AI providers

VideoGen can now route TTS and image generation between local providers and OpenAI.

## Modes

```env
TTS_PROVIDER=auto       # auto | piper | openai
IMAGE_PROVIDER=auto     # auto | local | openai
```

`auto` prefers the local provider when it is fully configured and falls back to OpenAI if the local provider is unavailable or fails.

## Piper local TTS (CPU)

VideoGen expects a Piper binary plus a voice `.onnx` file and its matching `.onnx.json` file.

Example layout:

```text
/opt/piper/piper
/opt/piper/models/ru_RU-irina-medium.onnx
/opt/piper/models/ru_RU-irina-medium.onnx.json
```

`.env`:

```env
TTS_PROVIDER=auto
PIPER_BIN=/opt/piper/piper
PIPER_MODEL=/opt/piper/models/ru_RU-irina-medium.onnx
PIPER_TIMEOUT_SECONDS=120
```

Piper receives UTF-8 text on stdin and writes WAV files to the project `audio/` directory. It is intended to run on CPU and does not need to consume the GTX 1660 VRAM.

Test outside VideoGen:

```bash
echo 'Привет. Это тест локальной озвучки.' | \
  /opt/piper/piper \
  --model /opt/piper/models/ru_RU-irina-medium.onnx \
  --output_file /tmp/piper-test.wav

ffprobe /tmp/piper-test.wav
```

## Local image generation with stable-diffusion.cpp

VideoGen supports `sd-cli` with a FLUX-style split model layout:

```env
IMAGE_PROVIDER=auto
SD_CPP_BIN=/opt/stable-diffusion.cpp/build/bin/sd-cli
SD_CPP_DIFFUSION_MODEL=/opt/stable-diffusion.cpp/models/flux1-schnell-q3_k.gguf
SD_CPP_VAE=/opt/stable-diffusion.cpp/models/ae.safetensors
SD_CPP_CLIP_L=/opt/stable-diffusion.cpp/models/clip_l.safetensors
SD_CPP_T5XXL=/opt/stable-diffusion.cpp/models/t5xxl.gguf
SD_CPP_STEPS=4
SD_CPP_CFG_SCALE=1.0
SD_CPP_TIMEOUT_SECONDS=600
SD_CPP_CLIP_ON_CPU=true
SD_CPP_OFFLOAD_TO_CPU=true
```

For a 6 GB GTX 1660, start with a low-memory FLUX quantization and keep text encoders/offload on CPU. Do not try to keep the local LLM and image model fully resident in VRAM at the same time.

Basic standalone test:

```bash
/opt/stable-diffusion.cpp/build/bin/sd-cli \
  --diffusion-model "$SD_CPP_DIFFUSION_MODEL" \
  --vae "$SD_CPP_VAE" \
  --clip_l "$SD_CPP_CLIP_L" \
  --t5xxl "$SD_CPP_T5XXL" \
  -p 'small brown cartoon mouse near a river, cinematic animation frame' \
  --cfg-scale 1.0 \
  --sampling-method euler \
  --steps 4 \
  -W 1024 -H 576 \
  --clip-on-cpu \
  --offload-to-cpu \
  -o /tmp/local-image.png
```

## Provider behavior

### TTS

`auto` order:

```text
Piper local -> OpenAI TTS
```

### Images

`auto` order:

```text
stable-diffusion.cpp local -> OpenAI Image
```

If a Character Reference exists, VideoGen passes it to the chosen image provider. OpenAI uses the image-edit endpoint; the local provider uses img2img (`-i`) with a conservative strength.

## Recommended runtime layout

```text
CPU:
  Piper TTS
  FFmpeg
  FLUX text encoders where possible

GPU:
  llama.cpp OR stable-diffusion.cpp
```

On a 6 GB GPU, schedule llama.cpp and local image generation rather than expecting both large models to stay resident simultaneously. A dedicated GPU resource manager is the next layer to automate stop/start of llama-server around local image jobs.
