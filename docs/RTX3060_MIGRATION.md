# RTX 3060 migration (staged)

This branch updates VideoGen's llama.cpp defaults for the locally verified Qwen3.5 9B Q6 model. It does not install models or modify the running VM.

## Before deployment

- Back up the VM's actual `.env` and existing project data.
- The actual `.env` overrides `app/config.py` and `.env.example`. Merge the new LLM settings manually.
- Ensure the model file exists at `/home/faa/models/qwen35-9b/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q6_K.gguf`.
- Stop the manually launched llama-server on port 8080 before testing GPU workloads; VideoGen uses its own port 8081 and orchestrator.
- Check that your llama-server supports `--jinja` and `-fa on` (verified in the prior local Qwen test).
- Keep the previous image settings available for rollback; 1024x576 is **untested** on this pipeline.

## First smoke test

1. Verify Python imports and syntax: `python -m compileall -q app`.
2. Start VideoGen with the existing project-specific service or launch script.
3. Inspect `/api/health` and confirm the model orchestration profiles.
4. Generate a short four-scene storyboard, then a single image at 1024x576; record peak VRAM and errors.
5. Test regeneration, character continuity and OpenMontage rendering separately.

## Known remaining work

- The current fast and quality LLM profiles deliberately point to the same known-good model; a distinct quality model needs a VRAM and quality benchmark.
- FLUX Q2 and CPU text encoder remain as-is. Moving to Q4/Q5 or CUDA text encoding requires installing compatible weights and benchmarking memory. No such model availability is assumed.
- The current storyboard implementation uses a line-oriented scene format; full JSON Schema migration is a separate code change.
- GPU lock is local to VideoGen's process. Cross-application coordination with ACE-Step is not yet implemented.
- Character bible exists, but stronger identity checks/reference conditioning are not yet implemented.
- No live VM end-to-end test has been performed by this GitHub-only change.
