"""Points d'entrée Python (epic « Cartographie applicative dans raw », ticket
3343afe4) : fonctions pures sur les décorateurs lus par le parseur `python`.

- **FastAPI** : `@app.get(...)`, `@router.post(...)`… et `@router.api_route(...,
  methods=[...])` ; route = `prefix` de l'`APIRouter(...)` créé dans le fichier +
  chemin.
- **Flask** : `@app.route(..., methods=[...])` (GET par défaut), `@bp.get(...)` ;
  route = `url_prefix` du `Blueprint(...)` créé dans le fichier + chemin ; les
  paramètres `<int:id>` deviennent `{id}`.
- **Celery** : `@shared_task`, `@app.task`, `@celery.task` → tâche.

Un décorateur ne compte que sur un objet qui ressemble à une application ou à un
routeur (créé dans le fichier par `FastAPI()`, `APIRouter()`, `Flask()`,
`Blueprint()`, `Celery()`, ou nommé `app`, `router`, `api`, `bp`, `blueprint`,
`…_router`, `…_bp`). Une route non littérale garde son point d'entrée avec
`route: null`. Une route à plusieurs verbes donne un point d'entrée par verbe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

HTTP_METHODS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "head": "HEAD",
    "options": "OPTIONS",
}
# Fabriques d'application ou de routeur, et le mot-clé qui porte leur préfixe.
FACTORIES = {
    "FastAPI": "",
    "APIRouter": "prefix",
    "Flask": "",
    "Blueprint": "url_prefix",
    "Celery": "",
}
_ROUTER_NAME = re.compile(r"^(?:app|router|api|bp|blueprint|application)$|_router$|_bp$|_app$")
_CELERY_NAME = re.compile(r"^(?:app|celery|celery_app|worker)$|_celery$|_app$")
_STRING = re.compile(r"""^[rbuRBU]?(['"])(.*)\1$""", re.DOTALL)
_FLASK_PARAM = re.compile(r"<(?:[\w]+:)?(\w+)>")
_VERB = re.compile(r"""['"](\w+)['"]""")


@dataclass(frozen=True)
class Decorator:
    """`@objet.attribut(arguments)` tels qu'écrits : `target` est l'objet (vide
    pour `@shared_task`), `name` l'attribut ou le nom seul."""

    target: str
    name: str
    positional: tuple[str, ...] = ()
    named: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Router:
    """Une application ou un routeur créé dans le fichier."""

    factory: str
    prefix: str | None = ""  # None : préfixe non littéral


def literal(raw: str | None) -> str | None:
    """Le texte d'un argument littéral (`"x"`, `'x'`), None sinon (f-string,
    constante, concaténation)."""
    if raw is None:
        return None
    match = _STRING.match(raw.strip())
    return match.group(2) if match else None


def normalize(path: str) -> str:
    return _FLASK_PARAM.sub(r"{\1}", path)


def join_routes(prefix: str | None, path: str | None) -> str | None:
    if prefix is None or path is None:
        return None
    parts = [p.strip("/") for p in (prefix, path) if p.strip("/")]
    return normalize("/" + "/".join(parts))


def router_from_call(factory: str, named: dict[str, str]) -> Router | None:
    """Le routeur créé par `X = FastAPI(...)`, `APIRouter(prefix=...)`…"""
    if factory not in FACTORIES:
        return None
    key = FACTORIES[factory]
    if not key or key not in named:
        return Router(factory)
    return Router(factory, literal(named[key]))


def _verbs(raw: str | None) -> list[str]:
    if raw is None:
        return []
    return [v.upper() for v in _VERB.findall(raw) if v.lower() in HTTP_METHODS]


def entry_points(decorator: Decorator, routers: dict[str, Router]) -> list[dict[str, Any]]:
    """Les points d'entrée d'un décorateur de fonction (aucun s'il n'en pose pas)."""
    if decorator.target == "" and decorator.name == "shared_task":
        return [{"kind": "task"}]
    router = routers.get(decorator.target)
    if decorator.name == "task":
        celery = (router is not None and router.factory == "Celery") or bool(
            _CELERY_NAME.search(decorator.target)
        )
        return [{"kind": "task"}] if celery else []
    if router is None and not _ROUTER_NAME.search(decorator.target):
        return []
    prefix = router.prefix if router is not None else ""
    raw_path = decorator.positional[0] if decorator.positional else decorator.named.get("path")
    if raw_path is None and decorator.name == "route":
        raw_path = decorator.named.get("rule")
    path = literal(raw_path) if raw_path is not None else ""
    if decorator.name in HTTP_METHODS:
        verbs = [HTTP_METHODS[decorator.name]]
    elif decorator.name in ("route", "api_route"):
        verbs = _verbs(decorator.named.get("methods")) or ["GET"]
    else:
        return []
    route = join_routes(prefix, path)
    return [{"kind": "http", "verb": verb, "route": route} for verb in verbs]
