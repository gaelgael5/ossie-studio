from __future__ import annotations

from ossie_studio.parsers.conventions import load_schema, validate_schema
from ossie_studio.parsers.kotlin_java import KotlinJavaParser


def _parse(files: dict[str, str]) -> dict[str, dict]:
    objects = KotlinJavaParser().parse({p: c.encode("utf-8") for p, c in files.items()})
    out: dict[str, dict] = {}
    for obj in objects:
        schema = load_schema(obj.schema)
        validate_schema(schema)  # critère 5 : tout passe le validateur des conventions
        out[obj.key] = schema
    return out


def _java(code: str, path: str = "a.java") -> dict[str, dict]:
    return _parse({path: code})


def _kotlin(code: str, path: str = "a.kt") -> dict[str, dict]:
    return _parse({path: code})


# --- Java --------------------------------------------------------------------


def test_java_champs_types_et_obligatoires() -> None:
    schemas = _java(
        "public class C {\n"
        "  private int total;\n"
        "  private Long count;\n"
        "  private String nom;\n"
        "  @NotNull private String tag;\n"
        "  private static int CACHE;\n"
        "  private transient String tmp;\n"
        "}\n"
    )
    c = schemas["c"]
    assert c["x-language"] == "java"
    assert c["properties"]["total"] == {
        "type": "integer", "format": "int32", "x-visibility": "private",
    }
    # static / transient ignorés.
    assert "CACHE" not in c["properties"] and "tmp" not in c["properties"]
    # primitif → obligatoire ; objet → facultatif ; @NotNull → obligatoire.
    assert set(c["required"]) == {"total", "tag"}
    assert "count" not in c.get("required", [])
    assert "nom" not in c.get("required", [])


def test_java_column_nullable_false_et_id() -> None:
    schemas = _java(
        "public class C {\n"
        "  @Id private Long id;\n"
        "  @Column(nullable = false) private String ref;\n"
        "  @Column private String autre;\n"
        "}\n"
    )
    c = schemas["c"]
    assert set(c["required"]) == {"id", "ref"}
    assert "autre" not in c.get("required", [])


def test_java_size_et_annotations() -> None:
    schemas = _java(
        'public class C { @Size(max = 20, min = 2) @Pattern(regexp = "^[A-Z]+$")'
        " private String code; }\n"
    )
    code = schemas["c"]["properties"]["code"]
    assert code["maxLength"] == 20 and code["minLength"] == 2
    assert code["pattern"] == "^[A-Z]+$"
    assert any(a["nom"] == "Size" for a in code["x-annotations"])


def test_java_record() -> None:
    schemas = _java("public record Point(double x, double y) {}\n")
    point = schemas["point"]
    assert point["x-kind"] == "record"
    assert set(point["properties"]) == {"x", "y"}
    assert set(point["required"]) == {"x", "y"}
    assert point["properties"]["x"]["x-visibility"] == "public"


def test_java_enum() -> None:
    schemas = _java("public enum Color { RED, GREEN, BLUE }\n")
    assert schemas["color"]["enum"] == ["RED", "GREEN", "BLUE"]
    assert schemas["color"]["x-kind"] == "enum"


def test_java_collections() -> None:
    schemas = _java(
        "import java.util.*;\n"
        "public class C {\n"
        "  private List<Integer> l;\n"
        "  private java.util.Map<String, Integer> m;\n"
        "  private int[] arr;\n"
        "}\n"
    )
    props = schemas["c"]["properties"]
    assert props["l"]["type"] == "array" and props["l"]["items"]["format"] == "int32"
    assert props["m"]["type"] == "object"
    assert props["m"]["additionalProperties"]["format"] == "int32"
    assert props["arr"]["type"] == "array"


def test_java_heritage_allof() -> None:
    schemas = _parse(
        {
            "Base.java": "public class Base { private int id; }",
            "Child.java": "public class Child extends Base implements Named { private int a; }",
            "Named.java": "public interface Named { String getName(); }",
        }
    )
    child = schemas["child"]
    assert {"$ref": "base.schema.yaml"} in child["allOf"]
    assert {"$ref": "named.schema.yaml"} in child["allOf"]


def test_java_entite_jpa_manytoone_ref() -> None:
    # Critère 2 : @Table + @ManyToOne → annotations présentes, propriété en $ref.
    schemas = _parse(
        {
            "a/Order.java": (
                "package com.ex;\n"
                '@Table(name = "orders")\n'
                "public class Order {\n"
                "  @ManyToOne private Customer customer;\n"
                "}\n"
            ),
            "a/Customer.java": "package com.ex;\npublic class Customer { private Long id; }",
        }
    )
    order = schemas["com-ex-order"]
    assert any(a["nom"] == "Table" for a in order["x-annotations"])
    customer = order["properties"]["customer"]
    assert customer["$ref"] == "com-ex-customer.schema.yaml"
    assert any(a["nom"] == "ManyToOne" for a in customer["x-annotations"])


def test_java_reference_externe() -> None:
    schemas = _java("public class C { private Autre a; }\n")
    assert schemas["c"]["properties"]["a"]["x-unresolved-type"] == "Autre"


def test_java_visibilite_package_private() -> None:
    schemas = _java("class C { int a; }\n")
    assert schemas["c"]["x-visibility"] == "internal"
    assert schemas["c"]["properties"]["a"]["x-visibility"] == "internal"


def test_java_javadoc_description() -> None:
    schemas = _java("/** Doc de C. */\npublic class C { private int a; }\n")
    assert schemas["c"]["description"] == "Doc de C."


def test_java_generiques() -> None:
    schemas = _java("public class Box<T> { private T v; }\n")
    box = schemas["box"]
    assert box["x-generic-parameters"] == ["T"]
    assert box["properties"]["v"]["x-generic-type"] == "T"


# --- Kotlin ------------------------------------------------------------------


def test_kotlin_data_class_nullabilite() -> None:
    schemas = _kotlin(
        "data class P(\n"
        "  val id: Long,\n"
        "  val nom: String,\n"
        "  val note: String?,\n"
        '  val code: String = "x"\n'
        ")\n"
    )
    p = schemas["p"]
    assert p["x-kind"] == "data"
    assert p["x-language"] == "kotlin"
    # T → obligatoire (même avec valeur par défaut) ; T? → facultatif.
    assert set(p["required"]) == {"id", "nom", "code"}
    assert "note" not in p.get("required", [])


def test_kotlin_proprietes_de_corps() -> None:
    schemas = _kotlin(
        "class C {\n"
        "  val x: Int = 0\n"
        "  var y: String? = null\n"
        "  private val z: Int = 2\n"
        "}\n"
    )
    c = schemas["c"]
    assert set(c["properties"]) == {"x", "y", "z"}
    assert c["properties"]["z"]["x-visibility"] == "private"
    assert set(c["required"]) == {"x", "z"}


def test_kotlin_enum() -> None:
    schemas = _kotlin("enum class Etat { ACTIF, INACTIF }\n")
    assert schemas["etat"]["enum"] == ["ACTIF", "INACTIF"]
    assert schemas["etat"]["x-kind"] == "enum"


def test_kotlin_collections_et_ref() -> None:
    schemas = _kotlin(
        "data class C(\n"
        "  val lignes: List<Ligne>,\n"
        "  val index: Map<String, Int>\n"
        ")\n"
    )
    props = schemas["c"]["properties"]
    assert props["lignes"]["type"] == "array"
    assert props["lignes"]["items"]["x-unresolved-type"] == "Ligne"
    assert props["index"]["additionalProperties"]["format"] == "int32"


def test_kotlin_heritage_allof() -> None:
    schemas = _parse(
        {
            "Base.kt": "open class Base(val id: Int)",
            "Child.kt": "data class Child(val nom: String) : Base(0), Named",
        }
    )
    child = schemas["child"]
    assert {"$ref": "base.schema.yaml"} in child["allOf"]
    assert {"x-unresolved-type": "Named"} in child["allOf"]


def test_kotlin_kdoc_description() -> None:
    schemas = _kotlin("/** Doc de C. */\ndata class C(val a: Int)\n")
    assert schemas["c"]["description"] == "Doc de C."


def test_kotlin_annotation_jpa() -> None:
    schemas = _kotlin(
        'data class O(@Id val id: Long, @ManyToOne val client: Client)\n'
    )
    o = schemas["o"]
    assert any(a["nom"] == "Id" for a in o["properties"]["id"]["x-annotations"])
    assert o["properties"]["client"]["x-unresolved-type"] == "Client"


def test_kotlin_object_et_sealed() -> None:
    assert _kotlin("object S { val a: Int = 1 }\n")["s"]["x-kind"] == "object"
    assert _kotlin("sealed class Shape\n")["shape"]["x-kind"] == "sealed"


# --- Commun ------------------------------------------------------------------


def test_package_dans_la_cle() -> None:
    schemas = _java("package com.ex.model;\npublic class User { private int a; }\n")
    assert "com-ex-model-user" in schemas


def test_fichier_invalide_tolere() -> None:
    objects = KotlinJavaParser().parse(
        {"a.java": b"public class Good { private int a; }\npublic class Bad { private ; }\n"}
    )
    assert "good" in {o.key for o in objects}


def test_version_inclut_les_grammaires() -> None:
    version = KotlinJavaParser().version
    assert version.startswith("1.2-java") and "-kt" in version


def test_paquet_comme_espace_de_noms() -> None:
    java = _java("package com.acme.orders;\npublic class Order { public int id; }")
    assert java["com-acme-orders-order"]["x-namespace"] == "com.acme.orders"
    kt = _parse({"a.kt": "package com.acme.kt\ndata class Line(val id: Int)"})
    assert next(iter(kt.values()))["x-namespace"] == "com.acme.kt"
