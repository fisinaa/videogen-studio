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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
