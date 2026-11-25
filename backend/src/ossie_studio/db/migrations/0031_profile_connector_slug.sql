-- Règle libellé → slug : les profils et les connecteurs OIDC gagnent un slug
-- lisible et stable. Backfill depuis le nom / libellé ; un doublon ou un nom
-- inexploitable reçoit l'id (toujours unique). Une insertion sans slug reçoit
-- un slug opaque unique (l'API en fournit toujours un).
ALTER TABLE profiles ADD COLUMN slug text;
UPDATE profiles
SET slug = trim(both '-' from regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g'));
UPDATE profiles SET slug = id::text WHERE slug IS NULL OR slug = '';
UPDATE profiles p SET slug = p.id::text
WHERE EXISTS (SELECT 1 FROM profiles o WHERE o.slug = p.slug AND o.created_at < p.created_at);
ALTER TABLE profiles ALTER COLUMN slug SET NOT NULL;
ALTER TABLE profiles ALTER COLUMN slug SET DEFAULT gen_random_uuid()::text;
CREATE UNIQUE INDEX uq_profile_slug ON profiles (slug);

ALTER TABLE oidc_connectors ADD COLUMN slug text;
UPDATE oidc_connectors
SET slug = trim(both '-' from regexp_replace(lower(label), '[^a-z0-9]+', '-', 'g'));
UPDATE oidc_connectors SET slug = id::text WHERE slug IS NULL OR slug = '';
UPDATE oidc_connectors c SET slug = c.id::text
WHERE EXISTS (
    SELECT 1 FROM oidc_connectors o WHERE o.slug = c.slug AND o.created_at < c.created_at
);
ALTER TABLE oidc_connectors ALTER COLUMN slug SET NOT NULL;
ALTER TABLE oidc_connectors ALTER COLUMN slug SET DEFAULT gen_random_uuid()::text;
CREATE UNIQUE INDEX uq_oidc_connector_slug ON oidc_connectors (slug);
