# VideoGen Studio

Universal AI-assisted video production studio.

## Current MVP

The first version already provides:

- FastAPI backend
- browser UI for project creation
- llama.cpp provider through the OpenAI-compatible API
- structured storyboard generation
- project persistence to `data/projects/<project_id>/project.json`
- Docker support
- provider layout ready for Pexels, Pixabay, TTS and render workers

## Tested local LLM profile

Current target hardware profile:

- model: `Qwen3-8B-abliterated.Q4_K_M.gguf`
- llama.cpp
- context: 4096
- GPU layers: 38
- K/V cache: q8_0

Example llama.cpp server:

```bash
~/llama.cpp/build/bin/llama-server -m ~/models/Qwen3-8B-abliterated.Q4_K_M.gguf -ngl 38 -c 4096 -ctk q8_0 -ctv q8_0 -t 12 -tb 12 -np 1 --host 127.0.0.1 --port 8081
```

Check it:

```bash
curl http://127.0.0.1:8081/health
```

## Local install

```bash
git clone https://github.com/fisinaa/videogen-studio.git
cd videogen-studio

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Start VideoGen:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8090 --reload
```

Then open:

```text
http://SERVER_IP:8090
```

For local-only access, use `127.0.0.1` instead of `0.0.0.0`.

## First test prompt

The default UI prompt creates an original adventure cartoon about a small mouse drifting away from home on a toy boat. It is intentionally inspired only at a high level by classic animal-adventure storytelling and does not reproduce literary text.

The expected pipeline is currently:

```text
Browser
  -> FastAPI
  -> llama.cpp
  -> storyboard JSON
  -> project.json
```

Next milestones:

```text
storyboard
  -> Pexels / Pixabay / local media / AI media
  -> TTS
  -> HyperFrames
  -> FFmpeg
  -> MP4
```

## Docker

If llama.cpp runs directly on the host:

```bash
cp .env.example .env
docker compose up --build
```

The compose configuration points the container to the host llama.cpp service through `host.docker.internal:8081`.

## Project layout

```text
app/
  main.py
  config.py
  schemas.py
  storage.py
  providers/
    llm/
      base.py
      llama_cpp.py
  templates/
    index.html

data/
  projects/

providers to add next:
  media/
    pexels
    pixabay
  tts/
  video/

workers/
  render
```

## Security

Do not commit API keys or secrets. Put them in `.env`; the file is ignored by Git.

The current MVP should not be exposed directly to the public Internet yet. Authentication, job isolation, reverse proxying and rate limiting will be added before public deployment.
