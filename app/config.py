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

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_image_model: str = "gpt-image-2"
    openai_image_quality: str = "medium"
    openai_image_timeout_seconds: int = 180

    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "coral"
    openai_tts_format: str = "mp3"
    openai_tts_instructions: str = (
        "Speak naturally in Russian with warm cinematic narration, clear diction, "
        "moderate pace, and no exaggerated announcer style."
    )
    openai_tts_timeout_seconds: int = 180

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
