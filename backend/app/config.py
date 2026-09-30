from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ACR_ENV_FILE", ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://acr:acr@127.0.0.1:5432/acr"

    llm_provider: str = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    llm_model: str = "gemma3:4b"
    llm_timeout_s: float = 240.0
    llm_temperature: float = 0.2
    analyze_per_ip_per_hour: int = 30

    crm_provider: str = "mock"
    mock_crm_dir: Path = ROOT / "data" / "mock_amocrm"
    kb_seed_file: Path = ROOT / "data" / "kb_seed.json"
    frontend_dir: Path = ROOT / "frontend"


@lru_cache
def get_settings() -> Settings:
    return Settings()
