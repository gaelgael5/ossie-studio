"""Référentiels : données de référence tenues par Ossie-studio dans l'espace Git.

Un référentiel `<nom>` vit dans `referentiels/<nom>/`, à côté des sources et
des modèles :

    referentiels/<nom>/modele.schema.yaml       le modèle (JSON Schema 2020-12 en YAML, clé `x-key`)
    referentiels/<nom>/valeurs.json             les valeurs (tableau d'objets conformes)
    referentiels/<nom>/imports/<slug>.yaml      la déclaration d'un import
    referentiels/<nom>/imports/<slug>.jslt      son template JSLT
    referentiels/<nom>/imports/<slug>.exemple.csv|json   son fichier d'exemple

Modules : `model` (modèle et sa validation), `values` (valeurs, validation,
sortie déterministe), `imports` (déclarations d'import), `repo` (lecture et
écriture dans la vue d'une branche), `service` (écriture dans le brouillon de
la personne, jamais sur la branche racine).
"""
