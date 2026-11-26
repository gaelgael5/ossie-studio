from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SESSION_SECRET_LENGTH = 32
MASTER_KEY_HEX_LENGTH = 64  # 32 octets


class Settings(BaseSettings):
    """Configuration du service, lue dans l'environnement (et `.env` en dev).

    Stricte : une clef inconnue est refusée (`extra="forbid"`), et toute
    variable obligatoire absente fait échouer le démarrage — jamais de valeur
    par défaut sur une donnée critique.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    # Base — pas de défaut : le service ne démarre pas sans elle.
    database_url: str

    # Les deux volumes mappés (cf. enabler Socle). Chemins obligatoires :
    # sans eux, le backend refuse de démarrer.
    work_volume_path: Path
    sources_volume_path: Path

    session_secret: SecretStr
    session_cookie_secure: bool = False
    # Sans cette clef, le service refuse de démarrer — jamais de clef par défaut.
    secrets_master_key: SecretStr

    # Adresse publique de l'application (ex. https://studio.exemple.org), pour
    # calculer l'URL de retour OIDC à déclarer chez le fournisseur. Optionnelle :
    # si absente, on la déduit de la requête entrante.
    public_base_url: str = ""

    # Lectures de branche : réaligner la copie sur la forge à chaque lecture
    # (ticket 2daae568). Faux en service : seule l'application pousse, et la
    # publication réaligne la copie. Vrai pour les tests qui modifient la forge.
    git_read_follows_forge: bool = False

    # Taille maximale d'un contrat OpenAPI déposé (octets). Au-delà : refus.
    openapi_max_contract_bytes: int = 5 * 1024 * 1024

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

    def asyncpg_dsn(self) -> str:
        """DSN compris par asyncpg : il n'accepte pas le préfixe SQLAlchemy."""
        dsn = self.database_url
        for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
            if dsn.startswith(prefix):
                return "postgresql://" + dsn[len(prefix) :]
        return dsn
