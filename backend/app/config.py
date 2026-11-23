from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    default_llm_base_url: str = "https://api.openai.com/v1"
    default_llm_api_key: str = ""
    default_llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = Field(default=90, ge=1, le=600)
    llm_request_budget: int = Field(default=2, ge=1, le=4)

    backend_host: str = "127.0.0.1"
    backend_port: int = 7878

    database_url: str = f"sqlite:///{BACKEND_ROOT / 'playground.db'}"
    upload_dir: str = str(BACKEND_ROOT / "uploads")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def upload_path(self) -> Path:
        path = Path(self.upload_dir)
        if not path.is_absolute():
            path = (BACKEND_ROOT / path).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
