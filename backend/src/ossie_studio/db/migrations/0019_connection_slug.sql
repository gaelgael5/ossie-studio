-- Les connexions (forge et base) gagnent un slug lisible et stable : les
-- manifestes de sources les référencent par ce slug plutôt que par un UUID.
ALTER TABLE connections ADD COLUMN slug text;
ALTER TABLE db_connections ADD COLUMN slug text;

-- Backfill depuis le libellé (installations déjà peuplées). Sur une installation
-- neuve les tables sont vides : sans effet. Le slug applicatif gère les accents
-- et l'unicité à la création ; ici un repli simple suffit.
UPDATE connections
SET slug = trim(both '-' from regexp_replace(lower(label), '[^a-z0-9]+', '-', 'g'));
UPDATE db_connections
SET slug = trim(both '-' from regexp_replace(lower(label), '[^a-z0-9]+', '-', 'g'));
-- Libellé sans caractère exploitable → repli sur l'id, toujours unique.
UPDATE connections SET slug = id::text WHERE slug IS NULL OR slug = '';
UPDATE db_connections SET slug = id::text WHERE slug IS NULL OR slug = '';

ALTER TABLE connections ALTER COLUMN slug SET NOT NULL;
ALTER TABLE db_connections ALTER COLUMN slug SET NOT NULL;
CREATE UNIQUE INDEX uq_connection_slug ON connections (slug);
CREATE UNIQUE INDEX uq_db_connection_slug ON db_connections (slug);
