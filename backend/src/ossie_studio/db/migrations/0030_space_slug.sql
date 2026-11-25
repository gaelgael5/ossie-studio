-- Un espace gagne un slug lisible et stable (règle libellé → slug de
-- l'interface). Backfill depuis le nom ; repli sur l'id si le nom ne donne rien.
ALTER TABLE git_spaces ADD COLUMN slug text;
UPDATE git_spaces
SET slug = trim(both '-' from regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g'));
UPDATE git_spaces SET slug = id::text WHERE slug IS NULL OR slug = '';
-- Deux noms qui donnent le même slug : le second prend l'id (toujours unique).
UPDATE git_spaces g SET slug = g.id::text
WHERE EXISTS (
    SELECT 1 FROM git_spaces o WHERE o.slug = g.slug AND o.created_at < g.created_at
);
ALTER TABLE git_spaces ALTER COLUMN slug SET NOT NULL;
-- Une insertion sans slug (outils, reprises) reçoit un slug opaque et unique ;
-- l'API en fournit toujours un.
ALTER TABLE git_spaces ALTER COLUMN slug SET DEFAULT gen_random_uuid()::text;
CREATE UNIQUE INDEX uq_space_slug ON git_spaces (slug);
