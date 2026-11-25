# Schémas des documents d'un domaine

- `osi-schema-0.1.1.json` : JSON Schema **officiel** d'Apache Ossie (anciennement
  Open Semantic Interchange), version 0.1.1, copié tel quel depuis
  `core-spec/osi-schema.json` de l'étiquette `osi-0.1.1-rc1` du dépôt
  https://github.com/apache/ossie (seule étiquette publiée au 23/11/2026).
  Épinglé ici : aucune validation ne le télécharge à l'exécution. Ne pas le
  modifier ; une nouvelle version d'Ossie arrive par un nouveau fichier.
- Le schéma du document `<nom>.extend.yaml` (propre à Ossie-studio) est dans
  `domains/documents.py` (`EXTEND_SCHEMA`).
