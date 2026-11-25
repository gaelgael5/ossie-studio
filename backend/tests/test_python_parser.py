from __future__ import annotations

from ossie_studio.parsers.conventions import load_schema, validate_schema
from ossie_studio.parsers.python import PythonParser


def _parse(code: str, path: str = "a.py") -> dict[str, dict]:
    objects = PythonParser().parse({path: code.encode("utf-8")})
    out: dict[str, dict] = {}
    for obj in objects:
        schema = load_schema(obj.schema)
        validate_schema(schema)  # critère : tout passe le validateur des conventions
        out[obj.key] = schema
    return out


def test_dataclass_types_et_required() -> None:
    schemas = _parse(
        "from dataclasses import dataclass\n"
        "from uuid import UUID\n"
        "from datetime import datetime\n"
        "from decimal import Decimal\n"
        "@dataclass\n"
        "class Point:\n"
        "    x: int\n"
        "    y: float = 0.0\n"
        "    nom: str\n"
        "    ident: UUID\n"
        "    cree: datetime\n"
        "    prix: Decimal\n"
    )
    point = schemas["a-point"]
    assert point["title"] == "Point"
    assert point["x-language"] == "python"
    assert point["x-kind"] == "dataclass"
    assert point["x-visibility"] == "public"
    assert point["properties"]["x"] == {"type": "integer", "x-visibility": "public"}
    assert point["properties"]["ident"]["format"] == "uuid"
    assert point["properties"]["cree"]["format"] == "date-time"
    assert point["properties"]["prix"]["type"] == "number"
    # y a une valeur par défaut → facultatif ; les autres sans défaut → obligatoires.
    assert set(point["required"]) == {"x", "nom", "ident", "cree", "prix"}


def test_methodes_et_non_annotes_ignores() -> None:
    schemas = _parse(
        "class C:\n"
        "    a: int\n"
        "    b = 5\n"  # non annoté → ignoré
        "    __x: int = 0\n"  # dunder → ignoré
        "    def m(self):\n        pass\n"
    )
    assert set(schemas["a-c"]["properties"]) == {"a"}


def test_optional_rend_facultatif() -> None:
    schemas = _parse(
        "from typing import Optional\n"
        "class C:\n"
        "    a: Optional[int]\n"
        "    b: int | None\n"
        "    c: int\n"
    )
    c = schemas["a-c"]
    assert c["properties"]["a"]["type"] == "integer"
    assert c["required"] == ["c"]


def test_pydantic_field_contraintes() -> None:
    schemas = _parse(
        "from pydantic import BaseModel, Field\n"
        "class User(BaseModel):\n"
        "    nom: str = Field(..., max_length=20, min_length=2, description='Le nom')\n"
        "    age: int = Field(default=0, ge=0, le=150)\n"
        "    code: str = Field(pattern='^[A-Z]+$')\n"
        "    ref: str = Field(..., alias='refExterne')\n"
    )
    user = schemas["a-user"]
    assert user["x-kind"] == "model"
    nom = user["properties"]["nom"]
    assert nom["maxLength"] == 20 and nom["minLength"] == 2
    assert nom["description"] == "Le nom"
    age = user["properties"]["age"]
    assert age["minimum"] == 0 and age["maximum"] == 150
    assert user["properties"]["code"]["pattern"] == "^[A-Z]+$"
    assert user["properties"]["ref"]["x-alias"] == "refExterne"
    # ... (ellipsis) ou aucune valeur → obligatoire ; default=0 → facultatif.
    assert set(user["required"]) == {"nom", "code", "ref"}


def test_enum() -> None:
    schemas = _parse("from enum import Enum\nclass S(Enum):\n    A = 1\n    B = 2\n")
    assert schemas["a-s"]["enum"] == ["A", "B"]
    assert schemas["a-s"]["x-kind"] == "enum"


def test_typeddict_total_false_et_required() -> None:
    schemas = _parse(
        "from typing import TypedDict, Required\n"
        "class T(TypedDict, total=False):\n"
        "    a: int\n"
        "    b: Required[int]\n"
    )
    t = schemas["a-t"]
    assert t["x-kind"] == "typeddict"
    assert set(t["properties"]) == {"a", "b"}
    # total=False : rien n'est obligatoire sauf ce qui est Required[...].
    assert t["required"] == ["b"]


def test_typeddict_total_true_notrequired() -> None:
    schemas = _parse(
        "from typing import TypedDict, NotRequired\n"
        "class T(TypedDict):\n"
        "    a: int\n"
        "    b: NotRequired[int]\n"
    )
    assert schemas["a-t"]["required"] == ["a"]


def test_namedtuple() -> None:
    schemas = _parse(
        "from typing import NamedTuple\n"
        "class N(NamedTuple):\n"
        "    a: int\n"
        "    b: str = 'x'\n"
    )
    n = schemas["a-n"]
    assert n["x-kind"] == "namedtuple"
    assert n["required"] == ["a"]


def test_heritage_allof_sans_aplatir() -> None:
    schemas = _parse(
        "class Base:\n    id: int\n"
        "class Fille(Base):\n    nom: str\n"
    )
    fille = schemas["a-fille"]
    assert fille["allOf"] == [{"$ref": "a-base.schema.yaml"}]
    assert set(fille["properties"]) == {"nom"}


def test_base_de_cadre_ne_donne_pas_de_parent() -> None:
    schemas = _parse("from pydantic import BaseModel\nclass M(BaseModel):\n    a: int\n")
    assert "allOf" not in schemas["a-m"]
    assert schemas["a-m"]["x-kind"] == "model"


def test_reference_resolue_vs_externe() -> None:
    schemas = _parse(
        "class Ligne:\n    n: int\n"
        "class Commande:\n    ligne: Ligne\n    autre: Autre\n"
    )
    props = schemas["a-commande"]["properties"]
    assert props["ligne"]["$ref"] == "a-ligne.schema.yaml"
    assert props["autre"]["x-unresolved-type"] == "Autre"


def test_annotation_en_chaine_resolue() -> None:
    schemas = _parse(
        "from __future__ import annotations\n"
        "class Autre:\n    n: int\n"
        "class C:\n    o: 'Autre'\n"
    )
    assert schemas["a-c"]["properties"]["o"]["$ref"] == "a-autre.schema.yaml"


def test_collections_et_dictionnaire() -> None:
    schemas = _parse(
        "class C:\n"
        "    l: list[int]\n"
        "    s: set[str]\n"
        "    m: dict[str, int]\n"
    )
    props = schemas["a-c"]["properties"]
    assert props["l"]["type"] == "array" and props["l"]["items"]["type"] == "integer"
    assert props["s"]["type"] == "array"
    assert props["m"]["type"] == "object"
    assert props["m"]["additionalProperties"]["type"] == "integer"


def test_union_oneof() -> None:
    schemas = _parse("from typing import Union\nclass C:\n    v: Union[int, str]\n")
    v = schemas["a-c"]["properties"]["v"]
    assert v["oneOf"] == [{"type": "integer"}, {"type": "string"}]
    assert schemas["a-c"]["required"] == ["v"]


def test_literal_devient_enum() -> None:
    schemas = _parse("from typing import Literal\nclass C:\n    k: Literal['a', 'b']\n")
    assert schemas["a-c"]["properties"]["k"]["enum"] == ["a", "b"]


def test_docstring_description() -> None:
    schemas = _parse('class C:\n    """Doc de C."""\n    a: int\n')
    assert schemas["a-c"]["description"] == "Doc de C."


def test_classe_sans_attribut_annote_ne_produit_rien() -> None:
    # Ni attribut annoté, ni méthode publique : pas d'objet.
    schemas = _parse("class Service:\n    x = 5\n    def _do(self):\n        pass\n")
    assert schemas == {}


def test_classe_a_methode_publique_produit_un_objet() -> None:
    # Cartographie applicative (ticket 3343afe4) : ses méthodes ont besoin d'un objet.
    schemas = _parse("class Service:\n    x = 5\n    def do(self):\n        pass\n")
    assert schemas["a-service"]["x-kind"] == "class"
    assert "properties" not in schemas["a-service"]


def test_classe_imbriquee() -> None:
    schemas = _parse("class Outer:\n    a: int\n    class Inner:\n        b: int\n")
    assert "a-outer" in schemas
    assert "a-outer-inner" in schemas
    assert schemas["a-outer-inner"]["title"] == "Inner"


def test_module_dans_la_cle() -> None:
    schemas = _parse("class User:\n    a: int\n", path="models/user.py")
    assert "models-user-user" in schemas


def test_fichier_invalide_lit_les_classes_lisibles() -> None:
    # tree-sitter est tolérant : les types lisibles sortent malgré une erreur.
    objects = PythonParser().parse(
        {"a.py": b"class Good:\n    a: int\n\nclass Bad(:\n    pass\n"}
    )
    keys = {o.key for o in objects}
    assert "a-good" in keys


def test_fichier_sans_classe_ne_produit_rien() -> None:
    objects = PythonParser().parse({"a.py": b"x = 1\ndef f():\n    return 2\n"})
    assert objects == []


def test_version_inclut_la_grammaire() -> None:
    assert PythonParser().version.startswith("1.2-ts")


def test_module_comme_espace_de_noms() -> None:
    schemas = _parse("class Order:\n    id: int\n", path="app/models/order.py")
    assert next(iter(schemas.values()))["x-namespace"] == "app.models.order"
