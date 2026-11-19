from __future__ import annotations

from ossie_studio.parsers.base import (
    STATUT_ERREUR,
    STATUT_INCHANGE,
    FileRow,
    PluginContext,
    PluginResult,
)
from ossie_studio.parsers.markdown import (
    DOCS_INDEX,
    PARAM_MAX_KB,
    MarkdownPlugin,
    analyze_markdown,
)
from ossie_studio.sources.collect_core import checksum


class FakeRaw:
    def __init__(self, files: dict[str, str] | None = None) -> None:
        self._files = files or {}

    def read_index(self, name: str) -> str | None:
        return self._files.get(name)

    def read_output(self, path: str) -> str | None:
        return self._files.get(path)

    def list_outputs(self) -> dict[str, str]:
        return dict(self._files)

    def find_schema(self, name: str) -> str | None:
        return None


def _ctx(
    files: dict[str, bytes],
    *,
    raw: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    force: bool = False,
) -> PluginContext:
    return PluginContext(
        files=[FileRow(path=p, crc=checksum(c)) for p, c in sorted(files.items())],
        contents=files,
        raw=FakeRaw(raw),
        params=params or {},
        force=force,
        branche="principale",
    )


def _writes(result: PluginResult) -> dict[str, str]:
    return {w.path: w.content for w in result.writes}


# --- Analyse -----------------------------------------------------------------


def test_analyse_titre_sections_mentions_ressources() -> None:
    text = (
        "# API boutique\n\n"
        "## Commandes\n\n"
        "La classe `Commande` gère le panier. Voir aussi `Ligne`.\n\n"
        "```csharp\npublic class Remise { }\n```\n\n"
        "![schéma](images/flux.png)\n"
        "[spec](https://example.org/spec)\n"
        "[interne](./autre.md)\n"
    )
    doc = analyze_markdown("docs/README.md", text, "crc1")
    assert doc.titre == "API boutique"
    assert doc.sections == ["Commandes"]
    # Mentions : code inline identifiant + PascalCase dans le bloc de code.
    assert "Commande" in doc.mentions
    assert "Ligne" in doc.mentions
    assert "Remise" in doc.mentions
    # Ressources : relatives seulement (pas l'URL http).
    assert "images/flux.png" in doc.ressources
    assert "./autre.md" in doc.ressources
    assert "https://example.org/spec" not in doc.ressources


def test_titre_tombe_sur_le_nom_de_fichier() -> None:
    doc = analyze_markdown("docs/guide.md", "pas de titre de niveau 1\n", "c")
    assert doc.titre == "guide.md"


# --- Exécution du plugin -----------------------------------------------------


def test_copie_et_indexe() -> None:
    files = {"README.md": b"# Titre\n\ntexte", "docs/a.md": b"# A\n"}
    result = MarkdownPlugin().run(_ctx(files))
    writes = _writes(result)
    assert writes["docs/README.md"] == "# Titre\n\ntexte"
    assert writes["docs/docs/a.md"] == "# A\n"
    assert DOCS_INDEX in writes
    assert result.added == 2
    assert result.retained == 2


def test_idempotent_puis_modifie() -> None:
    plugin = MarkdownPlugin()
    files = {"README.md": b"# Titre\n\ntexte"}
    first = plugin.run(_ctx(files))
    index = _writes(first)[DOCS_INDEX]
    # Second passage, même contenu : inchangé, aucune copie de document.
    second = plugin.run(_ctx(files, raw={DOCS_INDEX: index}))
    assert second.unchanged == 1
    assert "docs/README.md" not in _writes(second)
    assert second.statuses["README.md"] == STATUT_INCHANGE
    # Modifié : recopié et réindexé.
    changed = {"README.md": b"# Titre\n\nautre texte"}
    third = plugin.run(_ctx(changed, raw={DOCS_INDEX: index}))
    assert third.modified == 1
    assert "docs/README.md" in _writes(third)


def test_document_supprime() -> None:
    plugin = MarkdownPlugin()
    files = {"README.md": b"# T\n", "vieux.md": b"# V\n"}
    index = _writes(plugin.run(_ctx(files)))[DOCS_INDEX]
    # vieux.md a disparu du dépôt.
    result = plugin.run(_ctx({"README.md": b"# T\n"}, raw={DOCS_INDEX: index}))
    assert "docs/vieux.md" in result.deletes
    assert result.deleted == 1


def test_trop_gros_signale_non_copie() -> None:
    files = {"gros.md": b"# T\n" + b"x" * 5000}
    result = MarkdownPlugin().run(_ctx(files, params={PARAM_MAX_KB: "1"}))
    assert any(e.source == "gros.md" and e.cause == "too_large" for e in result.errors)
    assert "docs/gros.md" not in _writes(result)
    assert result.statuses["gros.md"] == STATUT_ERREUR


def test_vide_signale() -> None:
    result = MarkdownPlugin().run(_ctx({"vide.md": b"   \n"}))
    assert any(e.source == "vide.md" and e.cause == "empty" for e in result.errors)


def test_illisible_signale() -> None:
    result = MarkdownPlugin().run(_ctx({"bin.md": b"\xff\xfe\x00bad"}))
    assert any(e.source == "bin.md" and e.cause == "unreadable" for e in result.errors)


def test_force_recopie_malgre_crc_identique() -> None:
    plugin = MarkdownPlugin()
    files = {"README.md": b"# T\n"}
    index = _writes(plugin.run(_ctx(files)))[DOCS_INDEX]
    forced = plugin.run(_ctx(files, raw={DOCS_INDEX: index}, force=True))
    assert "docs/README.md" in _writes(forced)
    assert forced.unchanged == 0
