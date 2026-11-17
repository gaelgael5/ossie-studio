from __future__ import annotations

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SESSION_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Configuration du service, lue dans l'environnement (et `.env` en dev)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    database_url: str = "postgresql+asyncpg://ossie:ossie@localhost:5432/ossie_studio"
    session_secret: SecretStr
    session_cookie_secure: bool = False

    @field_validator("session_secret")
    @classmethod
    def _session_secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_SESSION_SECRET_LENGTH:
            msg = f"SESSION_SECRET doit faire au moins {MIN_SESSION_SECRET_LENGTH} caractères"
            raise ValueError(msg)
        return value
