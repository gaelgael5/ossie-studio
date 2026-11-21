-- Vocabulaire de tags gouvernés (gouvernance des données, principe repris
-- d'Entropy Data). Les *définitions* des tags gérés vivent ici, en base de
-- l'installation ; les tags *posés* vivent dans les fichiers d'assets
-- versionnés (hors de cette table, à venir avec la curation).
--
-- L'id est normalisé en minuscules à l'écriture : « Gouvernance/PII » et
-- « gouvernance/pii » désignent le même tag (clé primaire sur l'id normalisé).
-- La hiérarchie est portée par le séparateur « / » dans l'id ; les niveaux
-- parents n'ont pas besoin d'exister comme lignes.
CREATE TABLE governed_tags (
    id          text PRIMARY KEY,
    owner       text NOT NULL DEFAULT '',
    description text NOT NULL DEFAULT '',
    links       jsonb NOT NULL DEFAULT '{}'::jsonb,
    properties  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
