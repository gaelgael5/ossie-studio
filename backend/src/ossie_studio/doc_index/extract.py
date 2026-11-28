"""Ce que l'index retient d'un fichier (enabler 15863091) : ses documents (type de
document, identité, nom, champs filtrables), les identités qu'il porte (`keys`) et
celles qu'il cite (`refs`).

Une fonction pure de **(chemin, contenu)** : le type de document et l'identité se
lisent dans le chemin (le pendant des « emplacements » du registre des types de
document, `mcp_surface/document_types.py`), le reste dans le contenu. Un même
contenu au même chemin donne toujours le même résultat : il n'est indexé qu'une fois,
pour toutes les vues qui le contiennent.

**Clés et références** : chaînes `<espèce>:<identité>`, comparées telles quelles.
- tout fichier porte `path:<chemin>` (un modèle cite ses objets par chemin) ;
- un document porte `<type de document>:<identité>`, les champs d'identité dans
  l'ordre du registre, séparés par `/` (`carto_objet:application/APP-01`) ;
- un objet de cartographie porte aussi `carto_objet:*/<CLÉ>` : une extrémité de
  relation écrite en clé seule (forme d'avant le ticket 421832ee) le cite ainsi ;
- les fichiers d'un référentiel portent `referentiel:<nom>` (le lien d'un type de
  cartographie vise le référentiel entier : modèle et valeurs).

Un fichier illisible n'est pas une erreur ici : il est indexé sans document (sa
validation le dira, ticket des règles). Un fichier que rien ne reconnaît non plus.

`INDEXER_VERSION` : à augmenter quand l'extraction change ; un contenu indexé par une
version plus ancienne est réindexé.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ossie_studio.parsers.conventions import SchemaReadError, load_schema

INDEXER_VERSION = 1

ANY_TYPE = "*"


@dataclass(frozen=True)
class IndexedDocument:
    document_type: str
    identity: dict[str, Any]
    name: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "document_type": self.document_type,
            "identity": self.identity,
            "name": self.name,
            "fields": self.fields,
        }


@dataclass(frozen=True)
class IndexedFile:
    documents: list[IndexedDocument] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)


def path_key(path: str) -> str:
    return f"path:{path}"


def doc_key(document_type: str, *parts: Any) -> str:
    return f"{document_type}:{'/'.join(str(p) for p in parts)}"


def carto_ref(value: Any) -> str | None:
    """La clé que cite une extrémité de relation : `type/CLÉ`, ou clé seule."""
    if not isinstance(value, str) or not value:
        return None
    if "/" in value:
        type_slug, _sep, key = value.partition("/")
        return doc_key("carto_objet", type_slug, key)
    return doc_key("carto_objet", ANY_TYPE, value)


# --- Lecture tolérante du contenu ------------------------------------------------------------


def _yaml(data: bytes) -> Any:
    try:
        return load_schema(data)
    except (SchemaReadError, UnicodeDecodeError, ValueError):
        return None


def _json(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# --- Les reconnaissances, par type de document ------------------------------------------------

Extractor = Callable[[re.Match[str], bytes], IndexedFile]


def _carto_type(m: re.Match[str], data: bytes) -> IndexedFile:
    slug = m["slug"]
    doc = _mapping(_yaml(data))
    refs = []
    relations = _mapping(doc.get("x-relations"))
    for side in relations.values():
        for targets in _mapping(side).values():
            if isinstance(targets, list):
                refs += [doc_key("carto_type", t) for t in targets if isinstance(t, str)]
    document = IndexedDocument(
        "carto_type",
        {"slug": slug},
        _text(doc.get("title")) or slug,
        {"archimate": _text(doc.get("x-archimate"))},
    )
    return IndexedFile([document], [doc_key("carto_type", slug)], refs)


def _carto_type_extend(m: re.Match[str], data: bytes) -> IndexedFile:
    """Le fichier d'extension d'un type (liens vers les référentiels, ticket 43478978) :
    pas un document du registre ; il cite son type et ses référentiels."""
    doc = _mapping(_yaml(data))
    refs = [doc_key("carto_type", m["slug"])]
    links = doc.get("referentiels")
    for link in links if isinstance(links, list) else []:
        name = _mapping(link).get("referentiel")
        if isinstance(name, str) and name:
            refs.append(doc_key("referentiel", name))
    return IndexedFile([], [], refs)


def _carto_objet(m: re.Match[str], data: bytes) -> IndexedFile:
    type_slug = m["type"]
    doc = _mapping(_yaml(data))
    key = _text(doc.get("cle")) or m["stem"].upper()
    refs = [doc_key("carto_type", type_slug)]
    for end in ("source", "cible"):
        ref = carto_ref(doc.get(end))
        if ref:
            refs.append(ref)
    fields: dict[str, Any] = {"nom": _text(doc.get("nom"))}
    if "source" in doc or "cible" in doc:
        fields.update(source=_text(doc.get("source")), cible=_text(doc.get("cible")))
    document = IndexedDocument(
        "carto_objet", {"type": type_slug, "key": key}, _text(doc.get("nom")) or key, fields
    )
    keys = [doc_key("carto_objet", type_slug, key), doc_key("carto_objet", ANY_TYPE, key)]
    return IndexedFile([document], keys, refs)


def _carto_modele(m: re.Match[str], data: bytes) -> IndexedFile:
    slug = m["slug"]
    doc = _mapping(_yaml(data))
    refs = []
    documents = doc.get("documents")
    for path in documents if isinstance(documents, list) else []:
        if isinstance(path, str) and path:
            refs.append(path_key(path))
    for name in ("regles", "graphique"):
        if isinstance(doc.get(name), str) and doc[name]:
            refs.append(path_key(doc[name]))
    document = IndexedDocument("carto_modele", {"slug": slug}, _text(doc.get("nom")) or slug)
    return IndexedFile([document], [doc_key("carto_modele", slug)], refs)


def _single(document_type: str, field_name: str, group: str, name_of: str = "") -> Extractor:
    """Un document par fichier, d'identité à un champ lu dans le chemin ; son nom est
    `name_of` dans le contenu YAML, sinon l'identité."""

    def extract(m: re.Match[str], data: bytes) -> IndexedFile:
        value = m[group]
        name = _text(_mapping(_yaml(data)).get(name_of)) if name_of else ""
        document = IndexedDocument(document_type, {field_name: value}, name or value)
        return IndexedFile([document], [doc_key(document_type, value)], [])

    return extract


def _processus(m: re.Match[str], data: bytes) -> IndexedFile:
    """Un processus BPMN : il cite les objets de la cartographie auxquels ses éléments
    sont rattachés (extension d'urban-bpmn), dont le processus métier qu'il détaille."""
    # Import local : le module des processus lit aussi toute la cartographie.
    from ossie_studio.cartographie.errors import CartographieError
    from ossie_studio.cartographie.process import parse_process

    key = m["stem"].upper()
    try:
        process = parse_process(data.decode("utf-8"))
    except (CartographieError, UnicodeDecodeError):
        return IndexedFile(
            [IndexedDocument("processus", {"key": key}, key)], [doc_key("processus", key)]
        )
    refs = [ref for r in process.references if (ref := carto_ref(r.designation))]
    document = IndexedDocument("processus", {"key": key}, process.name or key)
    return IndexedFile([document], [doc_key("processus", key)], refs)


def _referentiel_modele(m: re.Match[str], data: bytes) -> IndexedFile:
    name = m["name"]
    doc = _mapping(_yaml(data))
    tags = doc.get("x-tags")
    document = IndexedDocument(
        "referentiel_modele",
        {"referentiel": name},
        _text(doc.get("title")) or name,
        {"tags": [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []},
    )
    keys = [doc_key("referentiel_modele", name), doc_key("referentiel", name)]
    return IndexedFile([document], keys, [])


def _referentiel_valeurs(m: re.Match[str], data: bytes) -> IndexedFile:
    """Les valeurs d'un référentiel : leur clé dépend du modèle (un autre fichier) ; le
    fichier porte le référentiel entier, les valeurs elles-mêmes se lisent dans Git."""
    name = m["name"]
    return IndexedFile([], [doc_key("referentiel", name)], [doc_key("referentiel_modele", name)])


def _source(m: re.Match[str], data: bytes) -> IndexedFile:
    source = m["source"]
    doc = _mapping(_yaml(data))
    name = _text(doc.get("label")) or _text(doc.get("name")) or source
    documents = [
        IndexedDocument("source", {"source": source}, name, {"type": _text(doc.get("type"))}),
        IndexedDocument("regles_tri", {"source": source}, name),
    ]
    return IndexedFile(documents, [doc_key("source", source)], [])


def _schema_file(document_type: str) -> Extractor:
    def extract(m: re.Match[str], data: bytes) -> IndexedFile:
        source, name = m["source"], m["name"]
        doc = _mapping(_yaml(data) if not name.endswith(".json") else _json(data))
        title = _text(doc.get("title")) or name
        document = IndexedDocument(document_type, {"source": source, "name": name}, title)
        keys = [doc_key(document_type, source, name)]
        return IndexedFile([document], keys, [doc_key("source", source)])

    return extract


def _raw_extend(m: re.Match[str], data: bytes) -> IndexedFile:
    """L'extension d'un objet raw (fonctions, points d'entrée) : elle cite son schéma
    (`<clé>.schema.yaml` du même dossier)."""
    source, stem = m["source"], m["stem"]
    schema = f"{m['folder']}{stem}.schema.yaml"
    document = IndexedDocument("extension_raw", {"source": source, "name": schema}, stem)
    keys = [doc_key("extension_raw", source, schema)]
    refs = [doc_key("schema_raw", source, schema)]
    return IndexedFile([document], keys, refs)


def _governance(document_type: str, name_of: str) -> Extractor:
    def extract(m: re.Match[str], data: bytes) -> IndexedFile:
        doc = _mapping(_yaml(data))
        ident = _text(doc.get("id")) or m["id"]
        name = _text(doc.get(name_of)) or ident
        document = IndexedDocument(document_type, {"id": ident}, name)
        return IndexedFile([document], [doc_key(document_type, ident)], [])

    return extract


def _certification(m: re.Match[str], data: bytes) -> IndexedFile:
    doc = _mapping(_yaml(data))
    slug = _text(doc.get("slug")) or m["id"]
    document = IndexedDocument("certification", {"slug": slug}, _text(doc.get("label")) or slug)
    return IndexedFile([document], [doc_key("certification", slug)], [])


def _domaine(m: re.Match[str], data: bytes) -> IndexedFile:
    """Le fichier d'extension d'un domaine : le domaine, et ses références entre
    datasets (`references`, chacune citant ses deux datasets)."""
    domain = m["name"]
    doc = _mapping(_yaml(data))
    documents = [IndexedDocument("domaine", {"name": domain}, _text(doc.get("label")) or domain)]
    keys = [doc_key("domaine", domain)]
    refs: list[str] = []
    references = doc.get("references")
    for item in references if isinstance(references, list) else []:
        entry = _mapping(item)
        name = _text(entry.get("name"))
        if not name:
            continue
        documents.append(
            IndexedDocument("reference_dataset", {"domain": domain, "name": name}, name)
        )
        keys.append(doc_key("reference_dataset", domain, name))
        refs += [
            doc_key("dataset", domain, entry[s]) for s in ("from", "to") if _text(entry.get(s))
        ]
    return IndexedFile(documents, keys, refs)


def _domain_datasets(m: re.Match[str], data: bytes) -> IndexedFile:
    """Le document Ossie d'un domaine (consultable ou virtuel) : ses datasets et ses
    relations (chacune citant ses deux datasets)."""
    domain = m["name"]
    doc = _mapping(_yaml(data))
    documents: list[IndexedDocument] = []
    keys: list[str] = []
    refs = [doc_key("domaine", domain)]
    models = doc.get("semantic_model")
    for model in models if isinstance(models, list) else []:
        model = _mapping(model)
        datasets = model.get("datasets")
        for item in datasets if isinstance(datasets, list) else []:
            name = _text(_mapping(item).get("name"))
            if name:
                documents.append(IndexedDocument("dataset", {"domain": domain, "name": name}, name))
                keys.append(doc_key("dataset", domain, name))
        relationships = model.get("relationships")
        for item in relationships if isinstance(relationships, list) else []:
            entry = _mapping(item)
            name = _text(entry.get("name"))
            if not name:
                continue
            documents.append(
                IndexedDocument("relation_dataset", {"domain": domain, "name": name}, name)
            )
            keys.append(doc_key("relation_dataset", domain, name))
            refs += [
                doc_key("dataset", domain, entry[s]) for s in ("from", "to") if _text(entry.get(s))
            ]
    return IndexedFile(documents, keys, refs)


_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_ID = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_SCHEMA = r"(?:\.schema\.yaml|\.schema\.json|\.json)"

# Dans l'ordre : la première reconnaissance qui correspond au chemin l'emporte.
_RECOGNIZERS: list[tuple[re.Pattern[str], Extractor]] = [
    (re.compile(rf"^cartographie/types/(?P<slug>{_SLUG})\.schema\.yaml$"), _carto_type),
    (re.compile(rf"^cartographie/types/(?P<slug>{_SLUG})\.extend\.yaml$"), _carto_type_extend),
    (
        re.compile(rf"^cartographie/objets/(?P<type>{_SLUG})/(?P<stem>[a-z0-9-]+)\.yaml$"),
        _carto_objet,
    ),
    (re.compile(rf"^cartographie/modeles/(?P<slug>{_SLUG})\.modele\.yaml$"), _carto_modele),
    (
        re.compile(rf"^cartographie/modeles/(?P<slug>{_SLUG})\.graphique\.yaml$"),
        _single("carto_graphique", "slug", "slug"),
    ),
    (
        re.compile(rf"^cartographie/regles/(?P<slug>{_SLUG})\.rules\.yaml$"),
        _single("carto_regles", "slug", "slug"),
    ),
    (re.compile(r"^cartographie/processus/(?P<stem>[a-z0-9-]+)\.bpmn$"), _processus),
    (
        re.compile(rf"^referentiels/(?P<name>{_SLUG})/modele\.schema\.yaml$"),
        _referentiel_modele,
    ),
    (re.compile(rf"^referentiels/(?P<name>{_SLUG})/valeurs\.json$"), _referentiel_valeurs),
    (re.compile(rf"^sources/(?P<source>{_SLUG})/manifest\.yaml$"), _source),
    (
        re.compile(
            rf"^sources/(?P<source>{_SLUG})/raw/(?P<folder>(?:[^/]+/)*)(?P<stem>[^/]+)\.extend\.yaml$"
        ),
        _raw_extend,
    ),
    (
        re.compile(
            rf"^sources/(?P<source>{_SLUG})/raw/(?P<name>(?!docs/)(?:[^/]+/)*[^/]+{_SCHEMA})$"
        ),
        _schema_file("schema_raw"),
    ),
    (
        re.compile(
            rf"^sources/(?P<source>{_SLUG})/assets/(?P<name>(?:[^/]+/)*(?![^/]*\.extend\.yaml$)[^/]+{_SCHEMA})$"
        ),
        _schema_file("schema_asset"),
    ),
    (re.compile(rf"^governance/tags/(?P<id>{_ID}(?:/{_ID})*)\.yaml$"), _governance("tag", "label")),
    (
        re.compile(rf"^governance/classifications/(?P<id>{_ID})\.yaml$"),
        _governance("classification", "label"),
    ),
    (re.compile(rf"^governance/certifications/(?P<id>{_ID})\.yaml$"), _certification),
    (
        re.compile(rf"^governance/tag-rules/(?P<id>{_ID})\.yaml$"),
        _governance("regle_tag", "label"),
    ),
    (
        re.compile(rf"^governance/classification-rules/(?P<id>{_ID})\.yaml$"),
        _governance("regle_classification", "label"),
    ),
    (
        re.compile(rf"^governance/relation-profiles/(?P<id>{_ID})\.yaml$"),
        _governance("profil_relation", "label"),
    ),
    (re.compile(rf"^domaines/(?P<name>{_SLUG})\.extend\.yaml$"), _domaine),
    (
        re.compile(rf"^domaines/(?P<name>{_SLUG})(?:\.virtuel)?\.ossie\.yaml$"),
        _domain_datasets,
    ),
]


def index_file(path: str, data: bytes) -> IndexedFile:
    """Ce que l'index retient du fichier `path` de contenu `data`."""
    found = IndexedFile()
    for pattern, extract in _RECOGNIZERS:
        match = pattern.match(path)
        if match is not None:
            found = extract(match, data)
            break
    keys = [path_key(path), *found.keys]
    refs = [r for r in dict.fromkeys(found.refs) if r not in keys]
    return IndexedFile(found.documents, list(dict.fromkeys(keys)), refs)
