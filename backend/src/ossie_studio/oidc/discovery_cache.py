from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from ossie_studio.oidc.provider import DiscoveryError, discover

# État de la découverte d'un issuer, mis en cache quelques minutes pour ne pas
# réinterroger le fournisseur à chaque affichage de la page.
_TTL_SECONDS = 180.0
_cache: dict[str, tuple[str, float]] = {}

STATUS_OK = "ok"
STATUS_UNREACHABLE = "unreachable"


def _clear_cache() -> None:
    """Pour les tests."""
    _cache.clear()


async def connector_discovery_status(
    factory: Callable[[], httpx.AsyncClient], issuer: str
) -> str:
    """« ok » si la découverte de l'issuer répond, « unreachable » sinon. Une
    découverte injoignable ne lève jamais : elle ne bloque ni la page ni la
    suppression."""
    now = time.monotonic()
    cached = _cache.get(issuer)
    if cached is not None and cached[1] > now:
        return cached[0]
    status = STATUS_OK
    try:
        async with factory() as client:
            await discover(client, issuer)
    except DiscoveryError:
        status = STATUS_UNREACHABLE
    _cache[issuer] = (status, now + _TTL_SECONDS)
    return status
