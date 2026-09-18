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
    llm_timeout_seconds: int = 180
    llm_temperature: float = 0.7
    llm_max_tokens: int = 1800

    pexels_api_key: str = ""
    pixabay_api_key: str = ""

    # Image routing: auto | local | openai
    image_provider: str = "auto"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_image_model: str = "gpt-image-2"
    openai_image_quality: str = "medium"
    openai_image_timeout_seconds: int = 180

    # stable-diffusion.cpp / FLUX local image provider
    sd_cpp_bin: Path = Path("/opt/stable-diffusion.cpp/build/bin/sd-cli")
    sd_cpp_diffusion_model: Path = Path("")
    sd_cpp_vae: Path = Path("")
    sd_cpp_clip_l: Path = Path("")
    sd_cpp_t5xxl: Path = Path("")
    sd_cpp_steps: int = 4
    sd_cpp_cfg_scale: float = 1.0
    sd_cpp_timeout_seconds: int = 600
    sd_cpp_clip_on_cpu: bool = True
    sd_cpp_offload_to_cpu: bool = True

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
