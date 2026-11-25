"""Points d'entrée C# : lecture des littéraux et calcul des routes (epic
« Cartographie applicative dans raw », ticket f1cc03c4).

Fonctions pures, sans arbre syntaxique : le parseur `csharp` leur passe les
textes lus (arguments d'attributs, littéraux de `MapGet`…). Une route qu'on ne
sait pas calculer (constante, chaîne interpolée, concaténation) vaut **None** :
le point d'entrée est gardé, sa route est marquée inconnue.
"""

from __future__ import annotations

import re

# Attributs de contrôleur ASP.NET → verbe HTTP.
HTTP_ATTRIBUTES = {
    "HttpGet": "GET",
    "HttpPost": "POST",
    "HttpPut": "PUT",
    "HttpPatch": "PATCH",
    "HttpDelete": "DELETE",
    "HttpHead": "HEAD",
    "HttpOptions": "OPTIONS",
}
# Minimal API → verbe HTTP.
MAP_METHODS = {
    "MapGet": "GET",
    "MapPost": "POST",
    "MapPut": "PUT",
    "MapPatch": "PATCH",
    "MapDelete": "DELETE",
}
CONTROLLER_BASES = frozenset({"ControllerBase", "Controller"})
TASK_BASES = frozenset({"BackgroundService", "IHostedService"})

# Marqueur « route lue mais impossible à calculer » (≠ « pas de route »).
UNKNOWN = "\x00?"

_REGULAR = re.compile(r'^"((?:[^"\\]|\\.)*)"$', re.DOTALL)
_VERBATIM = re.compile(r'^@"((?:[^"]|"")*)"$', re.DOTALL)
_NAMED = re.compile(r"^\s*\w+\s*[:=]")
_TOKEN = re.compile(r"\[(controller|action)\]", re.IGNORECASE)


def literal(text: str) -> str | None:
    """La valeur d'un littéral chaîne C# simple (`"…"` ou `@"…"`), sinon None
    (chaîne interpolée, constante, concaténation…)."""
    text = text.strip()
    match = _REGULAR.match(text)
    if match:
        return re.sub(r"\\(.)", r"\1", match.group(1))
    match = _VERBATIM.match(text)
    if match:
        return match.group(1).replace('""', '"')
    return None


def template_of(arguments: list[str]) -> str | None:
    """Le gabarit de route d'un attribut (`[HttpGet("{id}")]`, `[Route("api")]`) :
    le premier argument positionnel. None s'il n'y en a pas ; UNKNOWN s'il n'est
    pas un littéral."""
    positional = [a for a in arguments if not _NAMED.match(a)]
    if not positional:
        return None
    value = literal(positional[0])
    return UNKNOWN if value is None else value


def normalize(route: str) -> str:
    """`/` en tête, pas de `/` doublé ni final (sauf la racine)."""
    route = "/" + route.strip().lstrip("~").strip("/")
    route = re.sub(r"/{2,}", "/", route)
    return route if route == "/" else route.rstrip("/")


def controller_route(
    class_template: str | None,
    method_template: str | None,
    *,
    controller: str,
    action: str,
) -> str | None:
    """Route d'une action de contrôleur : `[Route]` de la classe + gabarit de la
    méthode (un gabarit qui commence par `/` ou `~/` remplace celui de la
    classe) ; `[controller]` et `[action]` remplacés (en minuscules). None si la
    route est inconnue ou absente (routage conventionnel, non lu)."""
    if UNKNOWN in (class_template, method_template):
        return None
    if method_template is not None and method_template.startswith(("/", "~/")):
        parts = [method_template]
    else:
        parts = [p for p in (class_template, method_template) if p is not None]
    if not parts:
        return None
    joined = "/".join(p.strip("/") for p in parts if p.strip("/"))
    values = {"controller": controller.removesuffix("Controller").lower(), "action": action.lower()}
    return normalize(_TOKEN.sub(lambda m: values[m.group(1).lower()], joined))


def join_routes(prefix: str | None, route: str | None) -> str | None:
    """Préfixe de `MapGroup` + route de `MapGet`… ; None si l'un est inconnu."""
    if prefix is None or route is None:
        return None
    return normalize(f"{prefix.strip('/')}/{route.strip('/')}")
