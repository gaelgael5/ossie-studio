"""Règles de classification fournies par Ossie : jeu RGPD (ticket docflow c7ce55aa).

Gael (24/11/2026) : « propose-moi des règles de classification pour les
propriétés et injecte-les dans l'application ». Comme les règles de tags
fournies : elles font partie du produit, actives par défaut, non modifiables,
désactivables par l'espace (état dans son Git, voir
`classification_rules.read_builtin_disabled`).

Elles visent le schéma d'id `rgpd` et ses valeurs telles que Gael les a nommées
(« Donnée personnelle », « Catégorie particulière », « Infraction pénale »).
Sans ce schéma, elles sont signalées invalides et ne classent rien.

Les motifs ont été mesurés sur colis 21 (2 668 noms de champs) puis resserrés
pour écarter les faux positifs constatés : codes postaux de sites ou de relais,
`InternationalId` de site, `disabilityAccess` (accessibilité d'un relais, pas la
santé d'une personne), `HasGeographicCoordinates`, `MOBILEAPP_USER`,
`TokenUsername`, `IndexSignature`. Les règles marquées « ⚠ » gardent un risque
de faux positif, dit dans leur description.

Changer cette liste change ce que l'explorateur affiche : augmenter
`BUILTIN_CLASSIFICATION_RULES_VERSION` pour que les index se reconstruisent.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from ossie_studio.governance.classification_rules import (
    BUILTIN_PREFIX,
    ClassificationRule,
    parse_rule,
)

BUILTIN_CLASSIFICATION_RULES_VERSION = "1"

_RULES: list[dict[str, Any]] = [
    {
        "id": "rgpd-email",
        "name": "RGPD — adresses email",
        "description": (
            "Adresses électroniques des destinataires, expéditeurs, contacts (Email, "
            "ReceiverMail, SHIPTO_MAIL…)."
        ),
        "match": "all",
        "when": {
            "field": ["*email*", "*e_mail*", "mail", "*mail", "courriel*"],
            "type": ["string"],
        },
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-telephone",
        "name": "RGPD — téléphones",
        "description": (
            "Numéros de téléphone et de fax d'une personne (Phone, ReceiverPhone1, MobilePhone…)."
        ),
        "match": "all",
        "when": {
            "field": ["*phone*", "*telephone*", "tel", "mobile", "portable", "fax"],
            "type": ["string"],
        },
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-identite",
        "name": "RGPD — nom, prénom, civilité",
        "description": (
            "Nom, prénom et civilité d'une personne. « Name » seul n'est pas visé : il "
            "désigne aussi bien un produit qu'un site."
        ),
        "match": "all",
        "when": {
            "field": [
                "*firstname*",
                "*lastname*",
                "*prenom*",
                "nomfamille*",
                "civility",
                "civilite",
                "*receivername",
                "*sendername",
                "pickername",
                "*customername",
                "*recipientname",
                "*contactname",
                "contactpers",
                "*fullname*",
            ],
            "type": ["string"],
        },
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-naissance",
        "name": "RGPD — naissance",
        "description": "Date ou lieu de naissance.",
        "match": "all",
        "when": {"field": ["*birth*", "*naissance*"]},
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-piece-identite",
        "name": "RGPD — pièce d'identité",
        "description": "Numéro de pièce d'identité, de passeport ou de sécurité sociale.",
        "match": "all",
        "when": {
            "field": [
                "*idcard*",
                "*identitycard*",
                "*passport*",
                "*numerocni*",
                "nationalid",
                "*socialsecurity*",
                "nir",
                "*numerosecu*",
            ]
        },
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-adresse",
        "name": "RGPD — adresse postale d'une personne",
        "description": (
            "Adresse postale. ⚠ City, ZipCode ou Street décrivent aussi l'adresse "
            "d'un site ou d'un relais : désactiver la règle si ces faux positifs gênent."
        ),
        "match": "all",
        "when": {
            "field": [
                "street",
                "street?",
                "streetname",
                "streetnumber",
                "streetcomplement",
                "*senderstreet",
                "*receiverstreet",
                "address",
                "address?",
                "receiveraddress?",
                "senderaddress?",
                "adresse",
                "adresse?",
                "zipcode",
                "postalcode",
                "codepostal",
                "codepostaldestinataire",
                "codepostalexpediteur",
                "consigneezipcode",
                "receiver*zipcode",
                "sender*zipcode",
                "city",
                "ville",
                "doorcode",
                "floornb",
                "builiding",
                "building",
                "interphone",
            ],
            "type": ["string"],
        },
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-geolocalisation",
        "name": "RGPD — géolocalisation",
        "description": (
            "Position géographique. ⚠ Celle d'un relais n'est pas une donnée personnelle ; "
            "celle d'un livreur ou d'un client l'est."
        ),
        "match": "all",
        "when": {"field": ["latitude", "longitude", "gps"]},
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-identifiant-connexion",
        "name": "RGPD — identifiant de connexion d'une personne",
        "description": (
            "Identifiant de connexion d'une personne (login, username). ⚠ Un compte de "
            "service technique peut porter le même nom de champ."
        ),
        "match": "all",
        "when": {"field": ["login", "username", "userlogin"], "type": ["string"]},
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-signature-photo",
        "name": "RGPD — signature et photo",
        "description": "Signature (preuve de remise) et photo d'une personne.",
        "match": "all",
        "when": {
            "field": [
                "signature",
                "*receiversignature",
                "*customersignature",
                "photo",
                "*photo",
                "*picture",
            ]
        },
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-appareil-fidelite",
        "name": "RGPD — appareil et carte de fidélité",
        "description": (
            "Identifiant d'appareil (IMEI) et carte de fidélité, rattachables à une personne."
        ),
        "match": "all",
        "when": {"field": ["imei", "*imei", "loyaltycard*", "*loyaltycard"]},
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-paiement",
        "name": "RGPD — données bancaires",
        "description": "Coordonnées bancaires (IBAN, BIC, RIB, carte).",
        "match": "all",
        "when": {
            "field": [
                "iban",
                "*iban",
                "bic",
                "*bankaccount*",
                "*creditcard*",
                "*creditcardnumber",
                "rib",
            ]
        },
        "scheme": "rgpd",
        "values": ["Donnée personnelle"],
    },
    {
        "id": "rgpd-categorie-particuliere",
        "name": "RGPD — catégories particulières (santé, opinions…)",
        "description": (
            "Article 9 du RGPD : santé, opinions religieuses ou syndicales, origine "
            "ethnique, orientation sexuelle, biométrie, génétique."
        ),
        "match": "all",
        "when": {
            "field": [
                "*health*",
                "*sante*",
                "*religion*",
                "*ethni*",
                "*sexualorient*",
                "*syndica*",
                "*biometri*",
                "*genetic*",
            ]
        },
        "scheme": "rgpd",
        "values": ["Catégorie particulière"],
    },
    {
        "id": "rgpd-infraction",
        "name": "RGPD — infractions et condamnations",
        "description": "Article 10 du RGPD : infractions, condamnations, casier judiciaire.",
        "match": "all",
        "when": {
            "field": [
                "*infraction*",
                "*condamnation*",
                "*criminal*",
                "*casier*",
                "*offence*",
                "*offense*",
            ]
        },
        "scheme": "rgpd",
        "values": ["Infraction pénale"],
    },
]


@cache
def builtin_classification_rules() -> tuple[ClassificationRule, ...]:
    """Les règles fournies, validées une fois (une règle invalide est un défaut
    du produit : on lève tout de suite plutôt que de l'ignorer en silence)."""
    out: list[ClassificationRule] = []
    for data in _RULES:
        rule_id = BUILTIN_PREFIX + data["id"]
        rule, errors = parse_rule(rule_id, {**data, "id": rule_id}, builtin=True)
        if rule is None:
            raise ValueError(f"règle fournie invalide {rule_id} : {errors}")
        out.append(rule)
    return tuple(out)
