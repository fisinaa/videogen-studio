# VideoGen Studio — текущая архитектура

VideoGen Studio — локальная веб-студия для AI-assisted производства видео. Основной пайплайн:

```text
идея проекта
→ storyboard
→ сцены
→ canon / references
→ изображения
→ motion / image-to-video
→ озвучка
→ субтитры
→ монтаж
→ финальный MP4
```

## 1. Веб-слой

### Backend

Основной backend:

- Python
- FastAPI
- Uvicorn
- Pydantic
- async HTTP-вызовы

Ключевые файлы:

```text
app/main.py
app/server.py
app/config.py
app/schemas.py
app/storage.py
```

`app.server` подключает дополнительные роутеры, сервисы и JS-enhancements поверх основной страницы.

### Frontend

Отдельного React/Vue/Angular frontend пока нет. Используются:

- Jinja / HTML
- обычный JavaScript
- CSS

Основная страница:

```text
app/templates/index.html
```

Дополнительные UI-модули подключаются как JS endpoints, например:

```text
/videogen-enhancements.js
/videogen-project-tools.js
/videogen-project-prompt.js
/videogen-scene-collapse.js
/videogen-series.js
/videogen-series-text-refs.js
/videogen-workflow.js
/videogen-story-repair.js
/videogen-storyboard-provider.js
/videogen-openmontage-popup-fix.js
```

## 2. Хранение проектов

Проекты пока хранятся на файловой системе, без отдельной БД:

```text
data/projects/<project_id>/
```

Главный файл:

```text
project.json
```

В проекте сохраняются:

- параметры проекта;
- storyboard;
- сцены;
- visual prompts;
- canon / references;
- media candidates;
- выбранные изображения;
- audio;
- motion clips;
- LLM metadata.

## 3. Основные сущности

Pydantic-модели:

```text
Project
Storyboard
Scene
MediaAsset
AudioAsset
SeriesReference
SeriesTextReference
LLMRunInfo
```

Сцена содержит, в частности:

```text
id
title
duration_seconds
narration
dialogue
action
visual_prompt
visual_prompt_ru
visual_prompt_en
negative_prompt_en
media_search_query
reference_keys
selected_media
media_candidates
selected_audio
motion_mode
motion_candidates
selected_motion_media
llm_generation
```

## 4. LLM / storyboard

### Local LLM

Используется `llama.cpp` через OpenAI-compatible API:

```text
http://127.0.0.1:8081/v1
```

Локальные профили:

### Fast

```text
Qwen3-8B-abliterated.Q4_K_M.gguf
```

### Quality

```text
Qwen3-14B-Q4_K_M.gguf
```

Текущий baseline Quality:

```text
-ngl 25
-c 4096
-ctk q8_0
-ctv q8_0
-t 12
-tb 12
-np 1
```

Модельный оркестратор:

```text
app/services/model_orchestrator.py
```

Он управляет:

- запуском llama-server;
- Fast / Quality профилями;
- освобождением GPU;
- idle shutdown;
- переключением модели под задачу.

### OpenAI storyboard

Для создания storyboard доступен отдельный OpenAI-путь.

В UI можно выбирать:

```text
Local Quality — Qwen3 14B
OpenAI — GPT-5 mini
```

Переменные:

```text
OPENAI_API_KEY
OPENAI_STORYBOARD_MODEL=gpt-5-mini
```

## 5. Storyboard quality / continuity

Ключевые модули:

```text
app/services/llm_quality.py
app/services/storyboard_quality.py
app/routes/story_repair.py
```

Используются continuity-правила:

- не переименовывать canonical characters;
- не придумывать случайные постоянные признаки;
- не добавлять магию без причины;
- не повторять одинаковые сцены;
- не ломать established canon;
- сохранять visual aliases;
- не допускать unexplained teleportation/state jumps.

Есть отдельный режим пересборки хвоста storyboard, который строит продолжение от последних нормальных сцен, не используя испорченный tail как источник истины.

## 6. Series / Episode

Поддерживается структура:

```text
Series
├── Episode 1
├── Episode 2
└── Episode N
```

Canon и references могут наследоваться от series root к эпизодам.

Основные файлы:

```text
app/routes/series.py
app/routes/series_ui.py
app/services/series_continuity.py
```

## 7. Canon / Text References / Visual Aliases

Примеры atomic keys:

```text
@char_tim
@prop_boat
@loc_river
@style_cartoon
```

Composite alias:

```text
@visual_tim_boat
```

может разворачиваться в несколько atomic references.

Основной текст сцены остаётся human-readable, а ссылки на canon хранятся отдельно в:

```text
reference_keys
```

Перед image generation references разворачиваются в реальные текстовые описания.

## 8. Image generation

### Local Fast

Runtime:

```text
stable-diffusion.cpp
```

Модель:

```text
flux1-schnell-q2_k.gguf
```

### Local Quality

```text
z_image_turbo-Q3_K.gguf
```

с Qwen3-4B для text conditioning.

### Local Next

Экспериментальный FLUX.2-профиль:

```text
flux-2-klein-4b-Q4_0.gguf
Qwen3-4B-Q4_K_M.gguf
```

### OpenAI Image

```text
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_QUALITY=medium
```

## 9. stable-diffusion.cpp runtime

Основной локальный image runtime:

```text
~/stable-diffusion.cpp/build/bin/sd-cli
```

Используется CUDA + CPU offload, что важно для GPU с ограниченной VRAM.

Пример конфигурации:

```text
SD_CPP_BACKEND=all=cuda0,te=cpu
SD_CPP_OFFLOAD_TO_CPU=true
SD_CPP_DIFFUSION_FA=true
```

## 10. Character Reference / Project References

Сейчас есть Character Reference и series references.

Следующий planned шаг:

- ручная загрузка reference image;
- project-level references;
- character / object / location / style references;
- использование одного reference между сценами и эпизодами.

## 11. Motion / image-to-video

Режимы:

```text
static
camera_motion
image_to_video
```

`camera_motion` используется для pan / zoom / Ken Burns / drift.

`image_to_video` может использовать настоящий motion clip.

Основные файлы:

```text
app/services/motion.py
app/routes/motion.py
```

## 12. TTS

### Local Piper

Русский голос:

```text
ru_RU-irina-medium
```

Путь:

```text
/opt/piper/venv/bin/piper
```

### OpenAI TTS

```text
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=coral
```

## 13. Stock media / local media

Поддерживаются:

- Pexels
- Pixabay
- локальные image/video/audio uploads

У сцены могут быть несколько media candidates и один selected media asset.

## 14. OpenMontage

OpenMontage используется как production integration layer.

Текущий локальный root:

```text
/home/faa/OpenMontage
```

VideoGen формирует production job и HyperFrames workspace.

Основные файлы:

```text
app/integrations/openmontage.py
app/routes/openmontage.py
scripts/openmontage_preview.py
scripts/openmontage_render.py
```

## 15. HyperFrames

HyperFrames используется как основной HTML/CSS/JS/GSAP-based video composition runtime.

Он отвечает за:

- сценовую композицию;
- transitions;
- camera movement;
- subtitles;
- audio;
- timeline preview;
- финальный render.

Preview запускается через:

```bash
npx --yes hyperframes preview --port <PORT> --force-new
```

Workspace проекта создаётся примерно здесь:

```text
/home/faa/OpenMontage/projects/videogen-<project_id>/hyperframes/
```

## 16. FFmpeg

FFmpeg используется для:

- encoding;
- muxing;
- audio/video processing;
- финальной сборки MP4.

Текущая среда ранее тестировалась на FFmpeg 4.4.2.

## 17. Node.js / Chromium

Node.js требуется для HyperFrames / npm / npx.

Текущий tested runtime:

```text
Node.js 22.x
```

Headless Chrome/Chromium используется browser-based render/preview инструментами.

## 18. Docker

В репозитории есть:

```text
Dockerfile
docker-compose.yml
```

Но текущий GPU-heavy runtime, llama.cpp, stable-diffusion.cpp и OpenMontage в основном запускаются напрямую на Ubuntu host/VM.

## 19. Текущая ОС и hardware profile

Рабочая среда:

```text
Ubuntu 22.04 LTS
Xeon E5-2660 v4
около 18 vCPU
около 63 GiB RAM
GTX 1660 SUPER 6 GB
```

Для текущего полного пайплайна разумно держать около 56–64 GiB RAM.

## 20. Общая схема

```text
                 Browser
                    │
                    ▼
               FastAPI UI
                    │
        ┌───────────┼───────────┐
        │           │           │
        ▼           ▼           ▼
     Local LLM   OpenAI LLM   Project Storage
     llama.cpp                JSON / files
        │
        ▼
    Storyboard
        │
        ▼
  Canon / References
        │
        ▼
 Image Generation
 ┌──────┼────────────┐
 │      │            │
FLUX   Z-Image     OpenAI
 │
 ▼
stable-diffusion.cpp
        │
        ▼
       Scene
        │
        ▼
 Motion / I2V
        │
        ▼
       TTS
   Piper / OpenAI
        │
        ▼
    OpenMontage
        │
        ▼
    HyperFrames
        │
        ▼
      FFmpeg
        │
        ▼
       MP4
```

## 21. OpenMontage / HyperFrames troubleshooting

### Проверить статус интеграции

```bash
curl -s http://127.0.0.1:8090/api/openmontage/status | python3 -m json.tool
```

### Посмотреть preview logs

```bash
ls -lh /tmp/videogen-openmontage-preview-*.log
```

Последний лог:

```bash
LOG=$(ls -t /tmp/videogen-openmontage-preview-*.log 2>/dev/null | head -1)
echo "$LOG"
tail -200 "$LOG"
```

Следить в реальном времени:

```bash
tail -f "$LOG"
```

### Проверить процессы

```bash
ps aux | grep -E '[h]yperframes|[n]px|[n]ode'
```

### Проверить слушающие порты

```bash
ss -lntp | grep -E ':(3[2-9][0-9]{2}|4[0-1][0-9]{2})\b'
```

### Ручной тест HyperFrames CLI

```bash
cd /home/faa/OpenMontage
npx --yes hyperframes doctor --json
```

### Ручной preview конкретного workspace

```bash
cd /home/faa/OpenMontage/projects/videogen-<PROJECT_ID>/hyperframes
npx --yes hyperframes preview --port 3500 --force-new
```

Если эта команда падает, причина будет видна непосредственно в stdout/stderr и уже не связана с VideoGen UI.
