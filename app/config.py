from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    videogen_host: str = "127.0.0.1"
    videogen_port: int = 8090
    videogen_projects_dir: Path = Path("./data/projects")

    llm_provider: str = "llama_cpp"
    llm_base_url: str = "http://127.0.0.1:8081/v1"
    llm_model: str = "local"
    llm_api_key: str = "local"
    llm_timeout_seconds: int = 300
    llm_temperature: float = 0.7
    llm_max_tokens: int = 1800

    # Automatic model/GPU orchestration.
    model_orchestration_enabled: bool = True
    llm_on_demand: bool = True
    llm_idle_timeout_seconds: int = 60
    llm_restart_after_image: bool = True
    llm_start_timeout_seconds: int = 180
    llm_stop_timeout_seconds: int = 30
    gpu_lock_file: Path = Path("/tmp/videogen-gpu.lock")

    # Legacy fixed-model systemd unit. Kept for compatibility, but the default
    # multi-profile launcher below starts llama-server directly so it can switch
    # between Fast and Quality models on demand.
    llm_systemd_unit: str = "videogen-llama.service"
    llm_launch_mode: str = "direct"  # direct | systemd
    llm_server_bin: Path = Path("/home/faa/llama.cpp/build/bin/llama-server")
    llm_host: str = "127.0.0.1"
    llm_port: int = 8081
    llm_state_file: Path = Path("/tmp/videogen-llm-state.json")
    llm_log_file: Path = Path("/tmp/videogen-llama.log")

    # Fast profile: small model for optional low-latency work.
    llm_fast_model_name: str = "Qwen3-8B"
    llm_fast_model_path: Path = Path("/home/faa/models/Qwen3-8B-abliterated.Q4_K_M.gguf")
    llm_fast_args: str = "-ngl 32 -c 4096 -ctk q8_0 -ctv q8_0 -t 12 -tb 12 -np 1"

    # Quality profile: Qwen3 14B tested on GTX 1660 6 GB. 25 GPU layers fit
    # reliably and provide a practical quality/speed balance for storyboard work.
    llm_quality_model_name: str = "Qwen3-14B"
    llm_quality_model_path: Path = Path("/home/faa/models/Qwen3-14B-Q4_K_M.gguf")
    llm_quality_args: str = "-ngl 25 -c 4096 -ctk q8_0 -ctv q8_0 -t 12 -tb 12 -np 1"

    llm_profile_default: str = "fast"
    llm_profile_storyboard: str = "quality"
    llm_profile_visual_prompt: str = "quality"
    llm_profile_rewrite: str = "quality"

    pexels_api_key: str = ""
    pixabay_api_key: str = ""

    # Image routing: auto | local | local_fast | local_quality | local_next | openai
    image_provider: str = "auto"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_image_model: str = "gpt-image-2"
    openai_image_quality: str = "medium"
    openai_image_timeout_seconds: int = 180

    # Optional fal.ai credential used by OpenMontage video providers such as Seedance.
    fal_key: str = ""

    # stable-diffusion.cpp shared runtime
    sd_cpp_bin: Path = Path("/opt/stable-diffusion.cpp/build/bin/sd-cli")
    sd_cpp_vae: Path = Path("")
    sd_cpp_clip_l: Path = Path("")
    sd_cpp_t5xxl: Path = Path("")
    sd_cpp_timeout_seconds: int = 600
    sd_cpp_backend: str = "all=cuda0,te=cpu"
    sd_cpp_max_vram: str = "-1"
    sd_cpp_threads: int = 14
    sd_cpp_diffusion_fa: bool = True
    sd_cpp_offload_to_cpu: bool = True
    sd_cpp_verbose: bool = True

    # Local FAST profile (FLUX.1-schnell Q2_K)
    sd_cpp_diffusion_model: Path = Path("")
    sd_cpp_steps: int = 4
    sd_cpp_cfg_scale: float = 1.0
    sd_cpp_sampling_method: str = "euler"

    # Local QUALITY profile (Z-Image-Turbo or another --llm/CLIP+T5 profile)
    sd_cpp_quality_diffusion_model: Path = Path("")
    sd_cpp_quality_steps: int = 8
    sd_cpp_quality_cfg_scale: float = 1.0
    sd_cpp_quality_sampling_method: str = "euler"
    sd_cpp_quality_vae: Path = Path("")
    sd_cpp_quality_clip_l: Path = Path("")
    sd_cpp_quality_t5xxl: Path = Path("")
    sd_cpp_quality_llm: Path = Path("")

    # Local NEXT profile (FLUX.2 Klein 4B + Qwen3-4B)
    sd_cpp_next_diffusion_model: Path = Path("")
    sd_cpp_next_steps: int = 4
    sd_cpp_next_cfg_scale: float = 1.0
    sd_cpp_next_sampling_method: str = "euler"
    sd_cpp_next_vae: Path = Path("")
    sd_cpp_next_llm: Path = Path("")
    sd_cpp_next_clip_l: Path = Path("")
    sd_cpp_next_t5xxl: Path = Path("")

    # Legacy compatibility. Ignored when SD_CPP_BACKEND is set.
    sd_cpp_clip_on_cpu: bool = True

    # Conservative local render sizes for a 6 GB GPU.
    sd_cpp_width_16_9: int = 768
    sd_cpp_height_16_9: int = 432
    sd_cpp_width_9_16: int = 432
    sd_cpp_height_9_16: int = 768
    sd_cpp_width_1_1: int = 512
    sd_cpp_height_1_1: int = 512

    # Motion / image-to-video routing.
    # openmontage: use OpenMontage VideoSelector and its provider registry.
    # command: legacy/custom external runner fallback.
    motion_provider: str = "openmontage"  # disabled | openmontage | command
    motion_command: str = ""
    motion_timeout_seconds: int = 3600
    motion_default_duration_seconds: float = 10.0
    motion_max_duration_seconds: float = 12.0
    motion_width_16_9: int = 768
    motion_height_16_9: int = 432
    motion_width_9_16: int = 432
    motion_height_9_16: int = 768
    motion_width_1_1: int = 512
    motion_height_1_1: int = 512
    motion_openmontage_preferred_provider: str = "auto"
    motion_openmontage_runner: Path = Path("./scripts/openmontage_motion.py")
    motion_openmontage_reserve_gpu: bool = False

    # TTS routing: auto | piper | openai
    tts_provider: str = "auto"

    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "coral"
    openai_tts_format: str = "mp3"
    openai_tts_instructions: str = (
        "Speak naturally in Russian with warm cinematic narration, clear diction, "
        "moderate pace, and no exaggerated announcer style."
    )
    openai_tts_timeout_seconds: int = 180

    # Piper local CPU TTS
    piper_bin: Path = Path("/opt/piper/piper")
    piper_model: Path = Path("")
    piper_speaker: int | None = None
    piper_timeout_seconds: int = 120

    # OpenMontage rendering bridge. The UI makes the runtime choice explicit.
    openmontage_root: Path = Path("/home/faa/OpenMontage")
    openmontage_python: Path = Path("/home/faa/OpenMontage/.venv/bin/python")
    openmontage_timeout_seconds: int = 3600
    videogen_openmontage_runner: Path = Path("./scripts/openmontage_render.py")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
