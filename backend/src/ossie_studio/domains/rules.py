"""Règle de publication des domaines (ticket 661efd1c).

Gael, 28/11/2026, à propos du bandeau « 8 assets référencés absents de la
Curation » : « il faut que dans la page de publication on voie le problème.
Assure-toi que dans la page de publication on fasse bien tous les tests pour que
ce que l'on pousse soit cohérent. »

Les contrôles existaient (`read_domain`, `check_domain`) mais ne servaient qu'à
l'écriture d'un domaine ; ils passent dans le journal de validation :

- document Ossie ou `.extend.yaml` invalide, incohérence interne (relation vers un
  dataset ou une colonne absente…) : **erreur** — un domaine faux ne se publie pas ;
- entrée orpheline du `.extend.yaml` : **avertissement** ;
- asset référencé par un dataset et absent de `sources/<source>/assets/` :
  **avertissement**, jamais un blocage — choix de Gael (bug 206d434c : « seulement
  le dire »). Le message dit comment résoudre.

Une ligne par domaine et par nature : la clé d'une ligne du journal est
`règle:chemin:code`, plusieurs lignes de même code sur un même fichier se
confondraient (décision acceptée, « préexistant »). Les éléments sont donc
listés dans les arguments.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ossie_studio.domains.documents import (
    DOMAINS_DIR,
    EXTEND_SUFFIX,
    DomainError,
    check_domain,
    list_domains,
)
from ossie_studio.drafts.validation import VALIDATION_RULES, Finding, _digest
from ossie_studio.sources.paths import ASSETS_DIR, SOURCES_DIR

# Préfixes des avertissements de lecture (`Domain.warnings`) qui sont des
# entrées orphelines du `.extend.yaml`, et non une invalidité du document.
_ORPHANS = ("extend_orphan_dataset", "extend_orphan_reference")


class DomainRule:
    name = "domain"

    def check(self, view: Path) -> list[Finding]:
        out: list[Finding] = []
        for domain in list_domains(view):
            rel = f"{DOMAINS_DIR}/{domain.name}{EXTEND_SUFFIX}"
            digest = _digest(view / rel)
            orphans = [w.split(":", 1)[1] for w in domain.warnings if w.startswith(_ORPHANS)]
            invalid = [w for w in domain.warnings if not w.startswith(_ORPHANS)]
            if invalid:
                # Domaine illisible : rendu vide par list_domains, rien d'autre à vérifier.
                code, _, detail = invalid[0].partition(":")
                out.append(
                    Finding("error", rel, self.name, "domain_unreadable",
                            {"domain": domain.name, "code": code, "detail": detail}, digest)
                )  # fmt: skip
                continue
            try:
                check_domain(domain)
            except DomainError as exc:
                # check_domain s'arrête au premier problème : une ligne, la première.
                out.append(
                    Finding("error", rel, self.name, "domain_incoherent",
                            {"domain": domain.name, "code": exc.code, "detail": exc.detail or ""},
                            digest)
                )  # fmt: skip
            if orphans:
                out.append(
                    Finding("warning", rel, self.name, "domain_extend_orphans",
                            {"domain": domain.name, "names": ", ".join(orphans)}, digest)
                )  # fmt: skip
            assets = view / SOURCES_DIR
            missing = sorted(
                f"{ds.asset.source}/{ds.asset.path}"
                for ds in domain.datasets
                if ds.asset is not None
                and not (assets / ds.asset.source / ASSETS_DIR / ds.asset.path).is_file()
            )
            if missing:
                # L'acceptation vaut pour ce fichier ET cette liste : un nouvel
                # asset disparu la fait retomber.
                listed = "\n".join(missing)
                fingerprint = hashlib.sha256(f"{digest}\n{listed}".encode()).hexdigest()
                out.append(
                    Finding("warning", rel, self.name, "domain_assets_missing",
                            {"domain": domain.label or domain.name, "count": str(len(missing)),
                             "assets": ", ".join(missing)}, fingerprint)
                )  # fmt: skip
        return out


def register() -> None:
    """Ajoute la règle des domaines au journal du brouillon (idempotent)."""
    if not any(isinstance(r, DomainRule) for r in VALIDATION_RULES):
        VALIDATION_RULES.append(DomainRule())


register()
