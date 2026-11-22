from __future__ import annotations

import json

from ossie_studio.parsers.conventions import validate_schema
from ossie_studio.parsers.typescript import TypeScriptParser


def _parse(code: str, path: str = "a.ts") -> dict[str, dict]:
    objects = TypeScriptParser().parse({path: code.encode("utf-8")})
    out: dict[str, dict] = {}
    for obj in objects:
        schema = json.loads(obj.schema)
        validate_schema(schema)  # critère 4 : tout passe le validateur des conventions
        out[obj.key] = schema
    return out


def test_interface_types_optionnel_et_nullable() -> None:
    schemas = _parse(
        "export interface Point {\n"
        "  x: number;\n"
        "  y?: number;\n"
        "  label: string | null;\n"
        "  actif: boolean;\n"
        "}\n"
    )
    point = schemas["a-point"]
    assert point["x-language"] == "typescript"
    assert point["x-kind"] == "interface"
    assert point["x-visibility"] == "public"
    assert point["properties"]["x"] == {"type": "number", "x-visibility": "public"}
    # `| null` : null admis dans le type, et facultatif.
    assert point["properties"]["label"]["type"] == ["string", "null"]
    # y? facultatif, label nullable → hors required.
    assert set(point["required"]) == {"x", "actif"}


def test_alias_objet_vs_non_objet() -> None:
    schemas = _parse(
        "export type Alias = { a: number };\n"
        "export type Id = string;\n"
        "export interface C { code: Id; }\n"
    )
    # Alias objet → schéma propre ; alias non objet (Id) → pas de schéma propre.
    assert "a-alias" in schemas
    assert "a-id" not in schemas
    # Une propriété de type Id reçoit le type résolu (alias dans le même fichier).
    assert schemas["a-c"]["properties"]["code"]["type"] == "string"


def test_enum_valeurs() -> None:
    schemas = _parse("export enum Color { Red, Green = 2, Blue }\n")
    assert schemas["a-color"]["enum"] == [0, 2, 3]
    assert schemas["a-color"]["x-kind"] == "enum"


def test_enum_chaine() -> None:
    schemas = _parse("export enum E { A = 'a', B = 'b' }\n")
    assert schemas["a-e"]["enum"] == ["a", "b"]


def test_union_de_litteraux_devient_enum() -> None:
    schemas = _parse("export interface C { k: 'a' | 'b'; }\n")
    assert schemas["a-c"]["properties"]["k"]["enum"] == ["a", "b"]


def test_union_objets_oneof() -> None:
    schemas = _parse(
        "export interface A { a: number; }\n"
        "export interface B { b: number; }\n"
        "export interface C { v: A | B; }\n"
    )
    v = schemas["a-c"]["properties"]["v"]
    assert v["oneOf"] == [{"$ref": "a-a.json"}, {"$ref": "a-b.json"}]


def test_intersection_allof() -> None:
    schemas = _parse(
        "export interface A { a: number; }\n"
        "export interface B { b: number; }\n"
        "export interface C { v: A & B; }\n"
    )
    v = schemas["a-c"]["properties"]["v"]
    assert v["allOf"] == [{"$ref": "a-a.json"}, {"$ref": "a-b.json"}]


def test_heritage_allof_resolu_et_externe() -> None:
    schemas = _parse(
        "export interface Base { id: number; }\n"
        "export interface Fille extends Base { nom: string; }\n"
        "export interface Autre extends Externe { a: number; }\n"
    )
    assert schemas["a-fille"]["allOf"] == [{"$ref": "a-base.json"}]
    assert set(schemas["a-fille"]["properties"]) == {"nom"}
    assert schemas["a-autre"]["allOf"] == [{"x-unresolved-type": "Externe"}]


def test_reference_resolue_vs_externe() -> None:
    schemas = _parse(
        "export interface Ligne { n: number; }\n"
        "export interface Commande { ligne: Ligne; autre: Autre; }\n"
    )
    props = schemas["a-commande"]["properties"]
    assert props["ligne"]["$ref"] == "a-ligne.json"
    assert props["autre"]["x-unresolved-type"] == "Autre"


def test_type_utilitaire_non_resolu() -> None:
    schemas = _parse(
        "export interface Point { x: number; }\n"
        "export interface C { p: Partial<Point>; o: Omit<Point, 'x'>; }\n"
    )
    props = schemas["a-c"]["properties"]
    assert props["p"]["x-unresolved-type"] == "Partial<Point>"
    assert props["o"]["x-unresolved-type"] == "Omit<Point, 'x'>"


def test_collections_record_et_index() -> None:
    schemas = _parse(
        "export interface C {\n"
        "  tags: string[];\n"
        "  arr: Array<number>;\n"
        "  meta: Record<string, number>;\n"
        "}\n"
        "export interface M { [k: string]: number; }\n"
    )
    props = schemas["a-c"]["properties"]
    assert props["tags"]["type"] == "array" and props["tags"]["items"]["type"] == "string"
    assert props["arr"]["type"] == "array"
    assert props["meta"]["additionalProperties"]["type"] == "number"
    assert schemas["a-m"]["additionalProperties"]["type"] == "number"


def test_classe_decorateurs_et_parametres_constructeur() -> None:
    schemas = _parse(
        "export class Order {\n"
        "  @Column() name: string;\n"
        "  private secret: number;\n"
        "  constructor(public tag: string, plain: number, readonly ro: boolean) {}\n"
        "  compute(): number { return 1; }\n"
        "}\n"
    )
    order = schemas["a-order"]
    assert order["x-kind"] == "class"
    assert any(a["nom"] == "Column" for a in order["properties"]["name"]["x-annotations"])
    assert order["properties"]["secret"]["x-visibility"] == "private"
    # Paramètres de constructeur public / readonly → propriétés ; `plain` exclu.
    assert "tag" in order["properties"] and "ro" in order["properties"]
    assert "plain" not in order["properties"]
    # La méthode est ignorée.
    assert "compute" not in order["properties"]


def test_jsdoc_description() -> None:
    schemas = _parse("/** Doc de C. */\nexport interface C { a: number; }\n")
    assert schemas["a-c"]["description"] == "Doc de C."


def test_tuple() -> None:
    schemas = _parse("export interface C { t: [number, string]; }\n")
    t = schemas["a-c"]["properties"]["t"]
    assert t["type"] == "array"
    assert t["prefixItems"] == [{"type": "number"}, {"type": "string"}]


def test_non_exporte_est_interne() -> None:
    schemas = _parse("interface C { a: number; }\n")
    assert schemas["a-c"]["x-visibility"] == "internal"


def test_tsx_est_lu() -> None:
    schemas = _parse("export interface Props { title: string; }\n", path="a.tsx")
    assert schemas["a-props"]["properties"]["title"]["type"] == "string"


def test_d_ts_est_exclu() -> None:
    parser = TypeScriptParser()
    assert parser.handles("a.ts") is True
    assert parser.handles("a.tsx") is True
    assert parser.handles("a.d.ts") is False


def test_module_dans_la_cle() -> None:
    schemas = _parse("export interface User { a: number; }\n", path="models/user.ts")
    assert "models-user-user" in schemas


def test_fichier_invalide_lit_les_types_lisibles() -> None:
    objects = TypeScriptParser().parse(
        {"a.ts": b"export interface Good { a: number; }\ninterface Bad { a: ; \n"}
    )
    keys = {o.key for o in objects}
    assert "a-good" in keys


def test_fichier_sans_type_ne_produit_rien() -> None:
    objects = TypeScriptParser().parse({"a.ts": b"const x = 1;\nfunction f() { return 2; }\n"})
    assert objects == []


def test_version_inclut_la_grammaire() -> None:
    assert TypeScriptParser().version.startswith("1.1-ts")


def test_module_comme_espace_de_noms() -> None:
    schemas = _parse("export interface Order { id: number }", path="src/api/order.ts")
    ns = next(iter(schemas.values()))["x-namespace"]
    assert ns and "order" in ns
