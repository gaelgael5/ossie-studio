from __future__ import annotations

import json

from ossie_studio.parsers.conventions import validate_schema
from ossie_studio.parsers.csharp import CSharpParser


def _parse(code: str, path: str = "a.cs") -> dict[str, dict]:
    objects = CSharpParser().parse({path: code.encode("utf-8")})
    out: dict[str, dict] = {}
    for obj in objects:
        schema = json.loads(obj.schema)
        validate_schema(schema)  # critère 6 : tout passe le validateur
        out[obj.key] = schema
    return out


def test_classe_proprietes_et_types() -> None:
    schemas = _parse(
        "namespace App; public class C {"
        " public string Nom { get; set; }"
        " public int Age { get; set; }"
        " public bool Actif { get; init; }"
        " public Guid Id { get; set; }"
        " public DateTime Cree { get; set; }"
        "}"
    )
    c = schemas["app-c"]
    assert c["title"] == "C"
    assert c["x-language"] == "csharp"
    assert c["x-visibility"] == "public"
    assert c["properties"]["Nom"]["type"] == "string"
    # Type référence (string) sans annotation : facultatif et signalé.
    assert c["properties"]["Nom"]["x-nullability"] == "unknown"
    assert c["properties"]["Age"] == {
        "type": "integer",
        "format": "int32",
        "x-visibility": "public",
    }
    assert c["properties"]["Id"]["format"] == "uuid"
    assert c["properties"]["Cree"]["format"] == "date-time"
    # Types valeur non nullables → obligatoires.
    assert set(c["required"]) == {"Age", "Actif", "Id", "Cree"}


def test_membres_prives_et_statiques_ignores() -> None:
    schemas = _parse(
        "namespace App; public class C {"
        " public int A { get; set; }"
        " private int B { get; set; }"
        " public static int C2 { get; set; }"
        " internal int D { get; set; }"
        "}"
    )
    assert set(schemas["app-c"]["properties"]) == {"A"}


def test_nullable_et_reference_sans_annotation() -> None:
    schemas = _parse(
        "namespace App; public class C {"
        " public int? Total { get; set; }"
        " public string Nom { get; set; }"
        "}"
    )
    props = schemas["app-c"]["properties"]
    assert "Total" not in schemas["app-c"].get("required", [])
    # Type référence (string) sans annotation : facultatif et signalé.
    assert props["Nom"]["x-nullability"] == "unknown"
    assert "Nom" not in schemas["app-c"].get("required", [])


def test_collections_et_dictionnaire() -> None:
    schemas = _parse(
        "namespace App; public class C {"
        " public List<int> L { get; set; }"
        " public int[] T { get; set; }"
        " public Dictionary<string,int> M { get; set; }"
        "}"
    )
    props = schemas["app-c"]["properties"]
    assert props["L"]["type"] == "array" and props["L"]["items"]["type"] == "integer"
    assert props["T"]["type"] == "array"
    assert props["M"]["type"] == "object"
    assert props["M"]["additionalProperties"]["type"] == "integer"


def test_record_positionnel() -> None:
    schemas = _parse("namespace App; public record Point(double X, double Y);")
    point = schemas["app-point"]
    assert point["x-kind"] == "record"
    assert set(point["properties"]) == {"X", "Y"}
    assert set(point["required"]) == {"X", "Y"}


def test_enum() -> None:
    schemas = _parse("namespace App; public enum S { A, B, C }")
    assert schemas["app-s"]["enum"] == ["A", "B", "C"]
    assert schemas["app-s"]["x-kind"] == "enum"


def test_heritage_allof_sans_aplatir() -> None:
    schemas = _parse(
        "namespace App;"
        " public class Base { public int Id { get; set; } }"
        " public class Fille : Base { public string Nom { get; set; } }"
    )
    fille = schemas["app-fille"]
    # allOf + $ref vers le parent (résolu, même source) ; pas d'aplatissement.
    assert fille["allOf"] == [{"$ref": "app-base.json"}]
    assert set(fille["properties"]) == {"Nom"}


def test_reference_resolue_vs_externe() -> None:
    schemas = _parse(
        "namespace App;"
        " public class Ligne { public int N { get; set; } }"
        " public class Commande {"
        "   public Ligne L { get; set; }"
        "   public Autre A { get; set; }"
        " }"
    )
    props = schemas["app-commande"]["properties"]
    assert props["L"]["$ref"] == "app-ligne.json"
    assert props["A"]["x-unresolved-type"] == "Autre"


def test_generiques() -> None:
    schemas = _parse("namespace App; public class Boite<T> { public T V { get; set; } }")
    boite = schemas["app-boite"]
    assert boite["x-generic-parameters"] == ["T"]
    assert boite["properties"]["V"]["x-generic-type"] == "T"


def test_classe_imbriquee() -> None:
    schemas = _parse(
        "namespace App; public class Outer { public class Inner { public int A { get; set; } } }"
    )
    assert "app-outer" in schemas
    assert "app-outer-inner" in schemas
    assert schemas["app-outer-inner"]["title"] == "Inner"


def test_attributs() -> None:
    schemas = _parse(
        "namespace App;"
        ' [Table("t")] public class C {'
        " [Required][MaxLength(20)] public string Nom { get; set; }"
        " }"
    )
    c = schemas["app-c"]
    assert any(a["nom"] == "Table" for a in c["x-annotations"])
    nom = c["properties"]["Nom"]
    assert nom["maxLength"] == 20
    assert "Nom" in c["required"]  # [Required]


def test_commentaire_xml_description() -> None:
    schemas = _parse(
        "namespace App;\n/// <summary>Doc de C.</summary>\n"
        "public class C { public int A {get;set;} }"
    )
    assert schemas["app-c"]["description"] == "Doc de C."


def test_visibilite_internal() -> None:
    schemas = _parse("namespace App; internal class C { public int A { get; set; } }")
    assert schemas["app-c"]["x-visibility"] == "internal"


def test_classes_partielles_fusionnees() -> None:
    f1 = "namespace App; public partial class Foo { public int A { get; set; } }"
    f2 = "namespace App; public partial class Foo { public int B { get; set; } }"
    objects = CSharpParser().parse({"f1.cs": f1.encode(), "f2.cs": f2.encode()})
    assert len(objects) == 1
    obj = objects[0]
    assert obj.key == "app-foo"
    assert sorted(obj.sources) == ["f1.cs", "f2.cs"]
    schema = json.loads(obj.schema)
    assert set(schema["properties"]) == {"A", "B"}


def test_fichier_sans_type_ne_produit_rien() -> None:
    objects = CSharpParser().parse({"a.cs": b"using System;\n// rien\n"})
    assert objects == []


def test_version_inclut_la_grammaire() -> None:
    assert CSharpParser().version.startswith("1.2-ts")


def test_namespace_d_origine() -> None:
    schemas = _parse(
        "namespace Colis.Api.Models {\n public class Outer { public class Inner {} } }\n"
        "namespace Colis.Core;\n"
    )
    assert schemas["colis-api-models-outer"]["x-namespace"] == "Colis.Api.Models"
    # Une classe imbriquée garde le namespace, pas la classe englobante.
    assert schemas["colis-api-models-outer-inner"]["x-namespace"] == "Colis.Api.Models"
    sans = _parse("public class Libre { public int A { get; set; } }")
    assert "x-namespace" not in sans["libre"]


def test_commentaire_xml_balises_internes_nettoyees() -> None:
    schemas = _parse(
        "namespace App;\n"
        "/// <summary>\n"
        '/// Voir <see cref="Parcel"/> et <c>Track</c>, <see cref="T:App.Other"/>.\n'
        '/// <para>Vaut <see langword="null"/> si &lt;vide&gt;.</para>\n'
        "/// </summary>\n"
        "/// <remarks>Ignoré.</remarks>\n"
        "public class C {\n"
        "  /// <summary>Poids en <b>grammes</b> (<paramref name=\"x\"/>).</summary>\n"
        "  public int Weight { get; set; }\n"
        "}\n"
    )
    c = schemas["app-c"]
    assert c["description"] == "Voir Parcel et Track, App.Other. Vaut null si <vide>."
    assert c["properties"]["Weight"]["description"] == "Poids en grammes (x)."


def test_record_positionnel_doc_des_parametres() -> None:
    schemas = _parse(
        "namespace App;\n"
        "/// <summary>Adresse.</summary>\n"
        '/// <param name="Street">La <c>rue</c>.</param>\n'
        '/// <param name="City">La ville.</param>\n'
        "public record Address(string Street, string City, string Zip);\n"
    )
    props = schemas["app-address"]["properties"]
    assert props["Street"]["description"] == "La rue."
    assert props["City"]["description"] == "La ville."
    assert "description" not in props["Zip"]
    assert schemas["app-address"]["description"] == "Adresse."
