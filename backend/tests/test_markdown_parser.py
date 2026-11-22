from __future__ import annotations

from ossie_studio.parsers.base import (
    STATUT_ERREUR,
    STATUT_IGNORE,
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
    parse_docs_index,
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
        branch="principale",
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
    assert doc.title == "API boutique"
    assert doc.sections == ["Commandes"]
    # Mentions : code inline identifiant + PascalCase dans le bloc de code.
    assert "Commande" in doc.mentions
    assert "Ligne" in doc.mentions
    assert "Remise" in doc.mentions
    # Ressources : relatives seulement (pas l'URL http).
    assert "images/flux.png" in doc.resources
    assert "./autre.md" in doc.resources
    assert "https://example.org/spec" not in doc.resources


def test_titre_tombe_sur_le_nom_de_fichier() -> None:
    doc = analyze_markdown("docs/guide.md", "pas de titre de niveau 1\n", "c")
    assert doc.title == "guide.md"


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


def test_vide_ignore_sans_erreur() -> None:
    result = MarkdownPlugin().run(_ctx({"vide.md": b"   \n"}))
    assert result.errors == []
    assert result.statuses["vide.md"] == STATUT_IGNORE
    assert "docs/vide.md" not in _writes(result)


def test_document_vide_apres_coup_retire_sa_copie() -> None:
    plugin = MarkdownPlugin()
    index = _writes(plugin.run(_ctx({"notes.md": b"# Notes\n"})))[DOCS_INDEX]
    result = plugin.run(_ctx({"notes.md": b"\n"}, raw={DOCS_INDEX: index}))
    assert result.errors == []
    assert "docs/notes.md" in result.deletes and result.deleted == 1
    assert "notes.md" not in _writes(result)[DOCS_INDEX]


def test_trop_gros_et_illisible_disent_pourquoi() -> None:
    result = MarkdownPlugin().run(
        _ctx({"gros.md": b"# T\n" + b"x" * 5000, "bin.md": b"# ok\xff"}, params={PARAM_MAX_KB: "1"})
    )
    details = {e.source: e.detail for e in result.errors}
    assert details["gros.md"] == "5 KB > 1 KB (taille_max_ko)"
    assert details["bin.md"] == "byte 0xff at offset 4 (not UTF-8)"


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


def test_index_ecrit_en_anglais_et_version_1_relue() -> None:
    files = {"README.md": b"# T\n\n![x](img.png)\n"}
    index = _writes(MarkdownPlugin().run(_ctx(files)))[DOCS_INDEX]
    assert index.startswith("version: 2\n")
    assert "title: T" in index and "resources:" in index
    assert "titre:" not in index and "ressources:" not in index
    # Un index écrit avant le passage aux clés anglaises reste lisible.
    legacy = (
        "version: 1\ndocuments:\n- path: README.md\n  crc: c\n  titre: Ancien\n"
        "  sections: []\n  mentions: []\n  ressources: [img.png]\n"
    )
    doc = parse_docs_index(legacy)["README.md"]
    assert (doc.title, doc.resources) == ("Ancien", ["img.png"])
