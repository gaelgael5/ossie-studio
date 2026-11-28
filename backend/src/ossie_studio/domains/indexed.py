"""Les domaines vus par l'index des documents (ticket fc5bb3b4, « Recherche par l'index
(3/6) »).

Les listes `domaine`, `dataset`, `relation_dataset` et `reference_dataset` montrent
presque tout le contenu d'un domaine (ses documents Ossie et son `.extend.yaml`) :
l'index en garde le contenu lu, comme pour les valeurs d'un référentiel (Gael, ticket
c854ad1e : « oui » aux contenus entiers dans l'index, pour un seul index).

**Faits** (`file_facts`) : pour chaque fichier d'un domaine (`<nom>.extend.yaml`,
`<nom>.ossie.yaml`, `<nom>.virtuel.ossie.yaml`), le document lu, ou le motif de
l'échec de lecture. **Domaines reconstruits** (`IndexedDomains`) : `domain_from`
assemble le domaine depuis ces documents, exactement comme `read_domain` depuis les
fichiers (mêmes validations, même ordre, mêmes erreurs).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ossie_studio.domains.documents import (
    DOMAINS_DIR,
    EXTEND_SUFFIX,
    MISSING,
    OSSIE_SUFFIX,
    VIRTUAL_SUFFIX,
    Domain,
    DomainError,
    domain_from,
    domains_of,
    is_valid_domain_name,
)
from ossie_studio.parsers.conventions import SchemaReadError, load_schema

_SUFFIXES = (VIRTUAL_SUFFIX, OSSIE_SUFFIX, EXTEND_SUFFIX)


def file_facts(path: str, data: bytes) -> dict[str, Any] | None:
    """Les faits d'un fichier posé directement dans `domaines/` ; None pour les autres."""
    parts = path.split("/")
    if len(parts) != 2 or parts[0] != DOMAINS_DIR:
        return None
    if not any(parts[1].endswith(suffix) for suffix in _SUFFIXES):
        return None
    try:
        doc = load_schema(data.decode("utf-8"))
    except (UnicodeDecodeError, SchemaReadError) as exc:
        return {"domain_file": {"ok": False, "error": str(exc)}}
    return {"domain_file": {"ok": True, "doc": doc}}


Files = Mapping[str, tuple[str, dict[str, Any] | None]]


class IndexedDomains:
    """Les domaines d'une vue de l'index (fichiers posés dans `domaines/`)."""

    def __init__(self, files: Files) -> None:
        self._files = {
            path.split("/", 1)[1]: found
            for path, found in files.items()
            if path.startswith(f"{DOMAINS_DIR}/") and path.count("/") == 1
        }

    def names(self) -> list[str]:
        """Comme `list_domain_names` : les domaines au nom valide qui ont une extension."""
        return sorted(
            name[: -len(EXTEND_SUFFIX)]
            for name in self._files
            if name.endswith(EXTEND_SUFFIX) and is_valid_domain_name(name[: -len(EXTEND_SUFFIX)])
        )

    def read(self, name: str) -> Domain | None:
        """Comme `read_domain(root, name)`, depuis l'index."""
        if not is_valid_domain_name(name):
            return None

        def load(suffix: str) -> Any:
            file = f"{name}{suffix}"
            found = self._files.get(file)
            if found is None:
                return MISSING
            facts = (found[1] or {}).get("domain_file") or {}
            if not facts.get("ok"):
                raise DomainError("document_unreadable", f"{file} : {facts.get('error')}")
            return facts.get("doc")

        return domain_from(name, load)

    def domains(self) -> list[Domain]:
        """Comme `list_domains(root)`, depuis l'index."""
        return domains_of(self.names(), self.read)
