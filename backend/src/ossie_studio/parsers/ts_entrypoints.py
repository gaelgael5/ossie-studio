"""Points d'entrée TypeScript (epic « Cartographie applicative dans raw », ticket
f619f4a7) : fonctions pures sur les décorateurs et les appels lus par le parseur
`typescript`.

- **NestJS** : méthodes d'une classe `@Controller(...)` portant `@Get`, `@Post`,
  `@Put`, `@Patch`, `@Delete`, `@Head`, `@Options` ; route = chemin du contrôleur
  + chemin de la méthode. `@Cron` / `@Interval` / `@Timeout` → tâche ;
  `@MessagePattern` / `@EventPattern` → message.
- **Express et routeurs semblables** : `router.post('/paniers', creer)` sur un
  objet qui ressemble à un routeur (nommé `app`, `router`, `server`, `api`,
  `routes`, `fastify` ou `…Router`, ou créé dans le fichier par `express()`,
  `Router()`, `fastify()`, `new Hono()`…), avec au moins un gestionnaire.
- Les paramètres `:id` deviennent `{id}`. Une route qui n'est pas un texte
  littéral garde son point d'entrée avec `route: null`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

NEST_VERBS = {
    "Get": "GET",
    "Post": "POST",
    "Put": "PUT",
    "Patch": "PATCH",
    "Delete": "DELETE",
    "Head": "HEAD",
    "Options": "OPTIONS",
}
NEST_TASKS = frozenset({"Cron", "Interval", "Timeout"})
NEST_MESSAGES = frozenset({"MessagePattern", "EventPattern"})
ROUTER_METHODS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "head": "HEAD",
    "options": "OPTIONS",
}
# Fabriques dont le résultat est un routeur (`const app = express()`…).
ROUTER_FACTORIES = frozenset({"express", "Router", "fastify", "Fastify", "Hono", "Elysia", "Koa"})
_ROUTER_NAME = re.compile(r"^(?:app|server|router|api|routes?|fastify)$|.Router$|.router$")
_STRING = re.compile(r"""^(['"`])(.*)\1$""", re.DOTALL)
_PATH_KEY = re.compile(r"""\bpath\s*:\s*(['"`])(.*?)\1""")
_PARAM = re.compile(r":(\w+)(?:\([^)]*\))?\??")


@dataclass(frozen=True)
class Decorator:
    """Un décorateur et ses arguments **tels qu'écrits** (guillemets compris)."""

    name: str
    args: tuple[str, ...] = ()


def literal(raw: str | None) -> str | None:
    """Le texte d'un argument littéral (`'x'`, `"x"`, `` `x` `` sans `${}`, ou le
    premier élément de `['x', 'y']`) ; None sinon."""
    if raw is None:
        return None
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].split(",", 1)[0].strip()
    match = _STRING.match(text)
    if match is None or (match.group(1) == "`" and "${" in match.group(2)):
        return None
    return match.group(2)


def normalize(path: str) -> str:
    """`/commandes/:id` → `/commandes/{id}` (`:id?`, `:id(\\d+)` aussi)."""
    return _PARAM.sub(r"{\1}", path)


def join_routes(base: str | None, path: str | None) -> str | None:
    if base is None or path is None:
        return None
    parts = [p.strip("/") for p in (base, path) if p.strip("/")]
    return normalize("/" + "/".join(parts))


def _controller_path(decorator: Decorator) -> str | None:
    if not decorator.args:
        return ""
    raw = decorator.args[0].strip()
    if raw.startswith("{"):
        found = _PATH_KEY.search(raw)
        return found.group(2) if found else ""
    return literal(raw)


def nest_entry_point(method: list[Decorator], owner: list[Decorator]) -> dict[str, Any] | None:
    """Le point d'entrée d'une méthode NestJS (`owner` : décorateurs de la classe)."""
    for decorator in method:
        if decorator.name in NEST_TASKS:
            return {"kind": "task"}
        if decorator.name in NEST_MESSAGES:
            topic = literal(decorator.args[0]) if decorator.args else None
            return {"kind": "message", **({"topic": topic} if topic else {})}
    controller = next((d for d in owner if d.name == "Controller"), None)
    if controller is None:
        return None
    for decorator in method:
        if decorator.name in NEST_VERBS:
            path = literal(decorator.args[0]) if decorator.args else ""
            route = join_routes(_controller_path(controller), path)
            return {"kind": "http", "verb": NEST_VERBS[decorator.name], "route": route}
    return None


def is_router(name: str, created: set[str]) -> bool:
    """L'objet d'un appel `x.get(...)` ressemble-t-il à un routeur ?"""
    return name in created or _ROUTER_NAME.search(name) is not None


def router_entry_point(method: str, args: list[str]) -> dict[str, Any] | None:
    """`router.<méthode>(chemin, …gestionnaires)` → point d'entrée HTTP ; None si
    ce n'est pas une méthode de routeur ou s'il n'y a pas de gestionnaire."""
    verb = ROUTER_METHODS.get(method)
    if verb is None or len(args) < 2:
        return None
    path = literal(args[0])
    return {"kind": "http", "verb": verb, "route": normalize(path) if path is not None else None}
