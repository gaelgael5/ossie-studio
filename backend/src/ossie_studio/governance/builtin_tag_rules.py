"""Règles de tags fournies par Ossie, par technologie.

Proposées dans le ticket « jeux de règles proposés par technologie » et livrées
à la demande de Gael (22/11/2026). Elles font partie du produit : actives par
défaut, non modifiables, désactivables par l'espace (état dans son Git, voir
`tag_rules.read_builtin_disabled`). Les règles marquées « ⚠ » dans leur
description risquent des faux positifs : elles sont livrées actives comme les
autres, la description le dit, et l'espace les désactive au besoin.

Changer cette liste change ce que l'explorateur affiche : augmenter
`BUILTIN_RULES_VERSION` pour que les index se reconstruisent.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from ossie_studio.governance.tag_rules import BUILTIN_PREFIX, TagRule, parse_rule

BUILTIN_RULES_VERSION = "1"

CODE = ["*.cs", "*.java", "*.kt", "*.ts", "*.py"]
DATA = [*CODE, "*.sql"]

_RULES: list[dict[str, Any]] = [
    # --- C# ---------------------------------------------------------------------
    {
        "id": "csharp-aspnet-controllers",
        "name": "C# — contrôleurs ASP.NET",
        "description": "Bases et attribut qui n'existent que pour exposer des points d'API.",
        "files": ["*.cs"],
        "when": {
            "inherits": ["ControllerBase", "Controller", "ODataController"],
            "annotation": ["ApiController", "ApiControllerAttribute"],
        },
        "tags": ["technique/service"],
        "inherit_tags": True,
    },
    {
        "id": "csharp-signalr-hubs",
        "name": "C# — hubs SignalR",
        "description": "Point d'entrée temps réel, base dédiée.",
        "files": ["*.cs"],
        "when": {"inherits": ["Hub"]},
        "tags": ["technique/service"],
        "inherit_tags": True,
    },
    {
        "id": "csharp-wcf-services",
        "name": "C# — services WCF",
        "description": "Contrat de service WCF explicite.",
        "files": ["*.cs"],
        "when": {"annotation": ["ServiceContract", "*.ServiceContractAttribute"]},
        "tags": ["technique/service"],
        "inherit_tags": True,
    },
    {
        "id": "csharp-ef-dbcontext",
        "name": "C# — contexte Entity Framework",
        "description": "Base Entity Framework sans ambiguïté.",
        "files": ["*.cs"],
        "when": {"inherits": ["DbContext", "IdentityDbContext*"]},
        "tags": ["technique/acces-donnees"],
        "inherit_tags": True,
    },
    {
        "id": "csharp-ef-entities",
        "name": "C# — entités Entity Framework",
        "description": "Attributs de mapping vers une table.",
        "files": ["*.cs"],
        "when": {"annotation": ["Table", "Owned"]},
        "tags": ["technique/entite"],
        "inherit_tags": True,
    },
    {
        "id": "csharp-repositories",
        "name": "C# — dépôts",
        "description": "Convention de nommage très suivie. ⚠ Un dépôt de cache peut s'y glisser.",
        "files": ["*.cs"],
        "when": {"name": ["*Repository"], "inherits": ["I*Repository"]},
        "tags": ["technique/acces-donnees"],
    },
    {
        "id": "csharp-dto",
        "name": "C# — DTO et messages",
        "description": "Suffixes conventionnels. ⚠ *Request peut viser une classe métier.",
        "files": ["*.cs"],
        "when": {"name": ["*Dto", "*DTO", "*Request", "*Response", "*ViewModel"]},
        "tags": ["technique/dto"],
    },
    {
        "id": "csharp-data-contracts",
        "name": "C# — contrats de données",
        "description": "Classes de sérialisation (WCF, XML).",
        "files": ["*.cs"],
        "when": {
            "annotation": [
                "DataContract",
                "MessageContract",
                "XmlRoot",
                "*.DataContractAttribute",
                "*.MessageContractAttribute",
            ]
        },
        "tags": ["technique/dto"],
    },
    {
        "id": "csharp-exceptions",
        "name": "C# — exceptions",
        "description": "Hiérarchie standard des exceptions.",
        "files": ["*.cs"],
        "when": {"inherits": ["Exception", "*Exception"]},
        "tags": ["technique/exception"],
        "inherit_tags": True,
    },
    {
        "id": "csharp-hosted-services",
        "name": "C# — tâches de fond",
        "description": "Interfaces d'hébergement dédiées.",
        "files": ["*.cs"],
        "when": {"inherits": ["BackgroundService", "IHostedService"]},
        "tags": ["technique/tache-de-fond"],
        "inherit_tags": True,
    },
    {
        "id": "csharp-cqrs-handlers",
        "name": "C# — gestionnaires CQRS",
        "description": "Interfaces MediatR.",
        "files": ["*.cs"],
        "when": {"inherits": ["IRequestHandler*", "INotificationHandler*"]},
        "tags": ["technique/handler"],
    },
    {
        "id": "csharp-configuration",
        "name": "C# — configuration",
        "description": "Convention du modèle Options. ⚠ Quelques faux positifs possibles.",
        "files": ["*.cs"],
        "when": {"name": ["*Options", "*Settings"]},
        "tags": ["technique/configuration"],
    },
    {
        "id": "csharp-generated",
        "name": "C# — code généré",
        "description": "Proxies et code produits par un outil : utile pour les écarter.",
        "files": ["*.cs"],
        "when": {"annotation": ["GeneratedCode", "*GeneratedCodeAttribute"]},
        "tags": ["technique/genere"],
    },
    {
        "id": "csharp-tests",
        "name": "C# — tests",
        "description": "Attributs MSTest, NUnit, xUnit et suffixe *Tests.",
        "files": ["*.cs"],
        "when": {"annotation": ["TestClass", "TestFixture", "Fact"], "name": ["*Tests"]},
        "tags": ["technique/test"],
    },
    # --- Java / Kotlin ------------------------------------------------------------
    {
        "id": "java-spring-controllers",
        "name": "Java/Kotlin — contrôleurs Spring",
        "description": "Stéréotypes Spring d'exposition.",
        "files": ["*.java", "*.kt"],
        "when": {"annotation": ["RestController", "Controller"]},
        "tags": ["technique/service"],
    },
    {
        "id": "java-spring-services",
        "name": "Java/Kotlin — services Spring",
        "description": "Stéréotype Spring @Service.",
        "files": ["*.java", "*.kt"],
        "when": {"annotation": ["Service"]},
        "tags": ["technique/service-metier"],
    },
    {
        "id": "java-jpa-entities",
        "name": "Java/Kotlin — entités JPA",
        "description": "Annotations de mapping JPA.",
        "files": ["*.java", "*.kt"],
        "when": {"annotation": ["Entity", "Table", "Embeddable"]},
        "tags": ["technique/entite"],
        "inherit_tags": True,
    },
    {
        "id": "java-repositories",
        "name": "Java/Kotlin — dépôts",
        "description": "Spring Data.",
        "files": ["*.java", "*.kt"],
        "when": {
            "annotation": ["Repository"],
            "inherits": ["JpaRepository", "CrudRepository", "PagingAndSortingRepository"],
        },
        "tags": ["technique/acces-donnees"],
        "inherit_tags": True,
    },
    {
        "id": "java-configuration",
        "name": "Java/Kotlin — configuration",
        "description": "Stéréotypes Spring de configuration.",
        "files": ["*.java", "*.kt"],
        "when": {"annotation": ["Configuration", "ConfigurationProperties"]},
        "tags": ["technique/configuration"],
    },
    {
        "id": "java-exceptions",
        "name": "Java/Kotlin — exceptions",
        "description": "Hiérarchie standard des exceptions.",
        "files": ["*.java", "*.kt"],
        "when": {"inherits": ["Exception", "RuntimeException", "*Exception"]},
        "tags": ["technique/exception"],
        "inherit_tags": True,
    },
    {
        "id": "java-dto",
        "name": "Java/Kotlin — DTO",
        "description": "Suffixes conventionnels. ⚠ *Request peut viser une classe métier.",
        "files": ["*.java", "*.kt"],
        "when": {"name": ["*Dto", "*DTO", "*Request", "*Response"]},
        "tags": ["technique/dto"],
    },
    {
        "id": "java-tests",
        "name": "Java/Kotlin — tests",
        "description": "JUnit et suffixes *Test / *Tests.",
        "files": ["*.java", "*.kt"],
        "when": {"annotation": ["SpringBootTest"], "name": ["*Test", "*Tests"]},
        "tags": ["technique/test"],
    },
    # --- TypeScript ---------------------------------------------------------------
    {
        "id": "ts-nest-controllers",
        "name": "TypeScript — contrôleurs NestJS",
        "description": "Décorateur NestJS @Controller.",
        "files": ["*.ts"],
        "when": {"annotation": ["Controller"]},
        "tags": ["technique/service"],
    },
    {
        "id": "ts-typeorm-entities",
        "name": "TypeScript — entités TypeORM",
        "description": "Décorateur TypeORM @Entity.",
        "files": ["*.ts"],
        "when": {"annotation": ["Entity"]},
        "tags": ["technique/entite"],
        "inherit_tags": True,
    },
    {
        "id": "ts-angular-components",
        "name": "TypeScript — composants Angular",
        "description": "Décorateur Angular @Component.",
        "files": ["*.ts"],
        "when": {"annotation": ["Component"]},
        "tags": ["technique/ui"],
    },
    {
        "id": "ts-dto",
        "name": "TypeScript — DTO",
        "description": "Convention NestJS.",
        "files": ["*.ts"],
        "when": {"name": ["*Dto", "*DTO"]},
        "tags": ["technique/dto"],
    },
    # --- Python -------------------------------------------------------------------
    {
        "id": "python-pydantic-models",
        "name": "Python — modèles pydantic",
        "description": "Le parseur reconnaît BaseModel (nature « model »).",
        "files": ["*.py"],
        "when": {"kind": ["model"]},
        "tags": ["technique/dto"],
    },
    {
        "id": "python-django-models",
        "name": "Python — modèles Django",
        "description": "Base Django models.Model.",
        "files": ["*.py"],
        "when": {"inherits": ["models.Model"]},
        "tags": ["technique/entite"],
        "inherit_tags": True,
    },
    {
        "id": "python-sqlalchemy-models",
        "name": "Python — modèles SQLAlchemy",
        "description": "Base déclarative SQLAlchemy 2 (DeclarativeBase).",
        "files": ["*.py"],
        "when": {"inherits": ["DeclarativeBase"]},
        "tags": ["technique/entite"],
        "inherit_tags": True,
    },
    {
        "id": "python-exceptions",
        "name": "Python — exceptions",
        "description": "Hiérarchie standard des exceptions.",
        "files": ["*.py"],
        "when": {"inherits": ["Exception", "*Error"]},
        "tags": ["technique/exception"],
        "inherit_tags": True,
    },
    # --- Gouvernance des données (tous langages et tables SQL) --------------------
    {
        "id": "data-contact",
        "name": "Données — contact",
        "description": "Champs e-mail et téléphone. ⚠ *mail attrape aussi MailServer.",
        "files": DATA,
        "when": {"field": ["*email*", "*mail", "*phone*", "*telephone*", "num_tel*"]},
        "tags": ["donnees/personnelles/contact"],
    },
    {
        "id": "data-identity",
        "name": "Données — identité",
        "description": "Prénom, nom, date de naissance.",
        "files": DATA,
        "when": {
            "field": [
                "firstname",
                "first_name",
                "lastname",
                "last_name",
                "prenom",
                "birthdate",
                "birth_date",
                "dateofbirth",
                "date_naissance",
                "datenaissance",
            ]
        },
        "tags": ["donnees/personnelles/identite"],
    },
    {
        "id": "data-address",
        "name": "Données — adresse postale",
        "description": "⚠ *address* attrape aussi IpAddress ou EmailAddress.",
        "files": DATA,
        "when": {
            "field": ["*address*", "*adresse*", "street", "zipcode", "postalcode", "code_postal"]
        },
        "tags": ["donnees/personnelles/adresse"],
    },
    {
        "id": "data-financial",
        "name": "Données — financières",
        "description": "IBAN, BIC, numéro de carte.",
        "files": DATA,
        "when": {"field": ["iban", "bic", "bic*", "cardnumber", "*creditcard*"]},
        "tags": ["donnees/sensibles/financier"],
    },
    {
        "id": "data-secrets",
        "name": "Données — secrets",
        "description": "Mots de passe, secrets, jetons, clés d'API.",
        "files": DATA,
        "when": {"field": ["*password*", "*secret*", "*token", "apikey", "api_key"]},
        "tags": ["donnees/sensibles/secret"],
    },
    {
        "id": "data-national-id",
        "name": "Données — identifiant national",
        "description": "Numéro de sécurité sociale (NIR, SSN).",
        "files": DATA,
        "when": {"field": ["ssn", "nir", "*socialsecurity*", "num_secu*"]},
        "tags": ["donnees/sensibles/identifiant-national"],
    },
]


@cache
def builtin_rules() -> tuple[TagRule, ...]:
    """Les règles fournies, validées une fois (une règle invalide est un défaut
    du produit : on lève tout de suite plutôt que de l'ignorer en silence)."""
    out: list[TagRule] = []
    for data in _RULES:
        rule_id = BUILTIN_PREFIX + data["id"]
        rule, errors = parse_rule(rule_id, {**data, "id": rule_id}, builtin=True)
        if rule is None:
            raise ValueError(f"règle fournie invalide {rule_id} : {errors}")
        out.append(rule)
    return tuple(out)


def builtin_ids() -> set[str]:
    return {r.id for r in builtin_rules()}
