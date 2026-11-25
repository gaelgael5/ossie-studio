-- La gouvernance (tags gouvernés, certifications, schémas et niveaux de
-- classification) vit désormais dans le dépôt Git de chaque espace, par
-- branche (governance/…). Les tables globales sont supprimées sans reprise :
-- elles ne portaient que des données d'essai (décision de Gael, 21/11/2026).
DROP TABLE IF EXISTS classification_levels;
DROP TABLE IF EXISTS classification_schemes;
DROP TABLE IF EXISTS certifications;
DROP TABLE IF EXISTS governed_tags;
