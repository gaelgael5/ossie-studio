from __future__ import annotations

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SESSION_SECRET_LENGTH = 32
MASTER_KEY_HEX_LENGTH = 64  # 32 octets


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
    # Sans cette clef, le service refuse de démarrer — jamais de clef par défaut.
    secrets_master_key: SecretStr

    @field_validator("session_secret")
    @classmethod
    def _session_secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_SESSION_SECRET_LENGTH:
            msg = f"SESSION_SECRET doit faire au moins {MIN_SESSION_SECRET_LENGTH} caractères"
            raise ValueError(msg)
        return value

    @field_validator("secrets_master_key")
    @classmethod
    def _master_key_is_32_hex_bytes(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        try:
            decoded = bytes.fromhex(raw)
        except ValueError as exc:
            msg = "SECRETS_MASTER_KEY doit être hexadécimale — générer avec openssl rand -hex 32"
            raise ValueError(msg) from exc
        if len(raw) != MASTER_KEY_HEX_LENGTH or len(decoded) != MASTER_KEY_HEX_LENGTH // 2:
            msg = "SECRETS_MASTER_KEY doit faire 64 caractères hexadécimaux (32 octets)"
            raise ValueError(msg)
        return value

    def master_key_bytes(self) -> bytes:
        return bytes.fromhex(self.secrets_master_key.get_secret_value())
