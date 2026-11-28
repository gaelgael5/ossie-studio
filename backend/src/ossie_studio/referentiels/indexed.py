"""Les référentiels vus par l'index des documents (ticket c854ad1e, « Recherche par
l'index (2/6) »).

Gael (28 novembre 2026) : « oui » à garder les valeurs entières dans l'index, pour
qu'il n'y ait qu'un seul index. Les listes de l'agent (`referentiel_modele`,
`referentiel_valeur`) se calculent sans relire `referentiels/`.

**Faits** (`file_facts`) : fonctions pures du chemin et du contenu.
- le modèle (`modele.schema.yaml`) : le document lu par `load_model`, ou illisible ;
- les valeurs (`valeurs.json`) : la liste lue par `load_values`, ou illisible — la
  clé d'une valeur dépend du modèle (un autre fichier) : elle se calcule à la lecture ;
- chaque déclaration d'import (`imports/<slug>.yaml`) : lisible, ou le refus de
  `parse_declaration` (une déclaration illisible rend le référentiel illisible).

**Référentiels reconstruits** (`IndexedReferentiels`) : `read(name)` lève exactement
comme `read_referentiel` (mêmes codes, même ordre : modèle, valeurs, imports), et
rend un `Referentiel` que les fonctions de `referentiels/published.py` lisent comme
celui des fichiers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from ossie_studio.imports.declaration import parse_declaration
from ossie_studio.imports.errors import ImportsError
from ossie_studio.problems import Problem
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import load_model
from ossie_studio.referentiels.repo import (
    IMPORTS_DIR,
    MODEL_FILE,
    REFERENTIELS_DIR,
    VALUES_FILE,
    Referentiel,
)
from ossie_studio.referentiels.values import load_values
from ossie_studio.slugs import is_valid_slug


def _text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def file_facts(path: str, data: bytes) -> dict[str, Any] | None:
    """Les faits d'un fichier de `referentiels/<nom>/` ; None pour les autres."""
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != REFERENTIELS_DIR:
        return None
    rel = parts[2:]
    text = _text(data)
    if rel == [MODEL_FILE]:
        try:
            model = load_model(text) if text is not None else None
        except ReferentielError:
            return {"referentiel_model": {"ok": False}}
        return {"referentiel_model": {"ok": text is not None, "model": model}}
    if rel == [VALUES_FILE]:
        try:
            values = load_values(text) if text is not None else None
        except ReferentielError:
            return {"referentiel_values": {"ok": False}}
        return {"referentiel_values": {"ok": text is not None, "values": values}}
    if len(rel) == 2 and rel[0] == IMPORTS_DIR and rel[1].endswith(".yaml"):
        slug = rel[1][: -len(".yaml")]
        if not is_valid_slug(slug) or text is None:
            return {"referentiel_import": {"error": None}}
        try:
            parse_declaration(slug, text)
        except ImportsError as exc:
            return {
                "referentiel_import": {
                    "error": {
                        "code": exc.code,
                        "status": exc.status,
                        "problems": [asdict(p) for p in exc.problems],
                        "detail": dict(exc.detail),
                    }
                }
            }
        return {"referentiel_import": {"error": None}}
    return None


Files = Mapping[str, tuple[str, dict[str, Any] | None]]


class IndexedReferentiels:
    """Les référentiels d'une vue de l'index (fichiers sous `referentiels/`)."""

    def __init__(self, files: Files) -> None:
        by_name: dict[str, dict[str, tuple[str, dict[str, Any] | None]]] = {}
        for path, found in files.items():
            parts = path.split("/")
            if len(parts) < 3 or parts[0] != REFERENTIELS_DIR:
                continue
            by_name.setdefault(parts[1], {})["/".join(parts[2:])] = found
        self._by_name = by_name

    def names(self) -> list[str]:
        """Comme `list_referentiels` : les dossiers au nom valide qui ont un modèle."""
        return sorted(
            name
            for name, files in self._by_name.items()
            if is_valid_slug(name) and MODEL_FILE in files
        )

    def _facts(self, name: str, rel: str, role: str) -> Any:
        found = self._by_name.get(name, {}).get(rel)
        return (found[1] or {}).get(role) if found is not None else None

    def read(self, name: str) -> Referentiel:
        """Comme `read_referentiel(root, name)`, depuis l'index."""
        files = self._by_name.get(name, {})
        if MODEL_FILE not in files:
            raise ReferentielError("referentiel_not_found", 404, name=name)
        model_facts = self._facts(name, MODEL_FILE, "referentiel_model") or {}
        model = model_facts.get("model")
        if not model_facts.get("ok") or not isinstance(model, dict):
            raise ReferentielError("model_unreadable", name=name)
        values: Any = []
        if VALUES_FILE in files:
            values_facts = self._facts(name, VALUES_FILE, "referentiel_values") or {}
            values = values_facts.get("values")
            if not values_facts.get("ok") or not isinstance(values, list):
                raise ReferentielError("values_unreadable", name=name)
        prefix = f"{IMPORTS_DIR}/"
        for rel in sorted(files):
            rest = rel[len(prefix) :] if rel.startswith(prefix) else None
            if rest is None or "/" in rest or not rest.endswith(".yaml"):
                continue
            error = (self._facts(name, rel, "referentiel_import") or {}).get("error")
            if error:
                problems = [Problem(**p) for p in error["problems"]]
                raise ReferentielError(error["code"], error["status"], problems, **error["detail"])
        return Referentiel(name, model, values, [])

    def path_version(self, name: str, rel: str) -> str | None:
        found = self._by_name.get(name, {}).get(rel)
        return found[0] if found is not None else None
