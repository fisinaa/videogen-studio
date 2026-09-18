# VideoGen Studio

Universal AI-assisted video production studio.

MVP goals:
- Web UI for describing a video/cartoon project
- FastAPI backend
- Local llama.cpp provider via OpenAI-compatible API
- Structured storyboard generation and project persistence
- Provider architecture for stock media, TTS and render backends
- Future external deployment behind a reverse proxy

The initial local LLM target is `Qwen3-8B-abliterated.Q4_K_M.gguf` served by llama.cpp.
