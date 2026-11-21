from __future__ import annotations

from ossie_studio.databases.base import ENGINES, DatabaseEngine, DbEngineFormSpec
from ossie_studio.databases.postgres import PostgresEngine
from ossie_studio.databases.snowflake import SnowflakeEngine
from ossie_studio.databases.sqlserver import SqlServerEngine

_ENGINES: dict[str, DatabaseEngine] = {
    "postgres": PostgresEngine(),
    "sqlserver": SqlServerEngine(),
    "snowflake": SnowflakeEngine(),
}


def get_engine(engine: str) -> DatabaseEngine:
    """Rend le moteur d'une base. Ajouter un moteur = une entrée ici et une
    implémentation, sans toucher aux écrans ni aux consommateurs."""
    found = _ENGINES.get(engine)
    if found is None:
        msg = f"Moteur inconnu : {engine}"
        raise ValueError(msg)
    return found


def is_known_engine(engine: str) -> bool:
    return engine in ENGINES


def list_engine_specs() -> list[DbEngineFormSpec]:
    """Descriptions de formulaire des moteurs enregistrés, pour le menu
    « Ajouter une base » et les popups dédiées."""
    return [engine.form_spec() for engine in _ENGINES.values()]
