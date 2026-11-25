-- 0030 (espaces) et 0031 (profils, connecteurs) ont repris les slugs sans retirer les accents (« Sans accès » →
-- « sans-acc-s ») alors que l'écran et slugify() les translittèrent
-- (« sans-acces »). On recalcule les seuls slugs encore égaux à la
-- reprise fautive, si le slug corrigé est libre.
CREATE TEMP TABLE _acc (src text, dst text);
INSERT INTO _acc VALUES ('àâäáãåçéèêëíìîïñóòôöõúùûüýÿœæ', 'aaaaaaceeeeiiiinooooouuuuyyoa');

UPDATE profiles p
SET slug = n.fixed
FROM (
    SELECT id,
           trim(both '-' from regexp_replace(
               translate(replace(replace(lower(name), 'œ', 'oe'), 'æ', 'ae'),
                         (SELECT src FROM _acc), (SELECT dst FROM _acc)),
               '[^a-z0-9]+', '-', 'g')) AS fixed,
           trim(both '-' from regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g')) AS old
    FROM profiles
) n
WHERE p.id = n.id AND p.slug = n.old AND n.fixed <> n.old AND n.fixed <> ''
  AND NOT EXISTS (SELECT 1 FROM profiles o WHERE o.slug = n.fixed);

UPDATE oidc_connectors c
SET slug = n.fixed
FROM (
    SELECT id,
           trim(both '-' from regexp_replace(
               translate(replace(replace(lower(label), 'œ', 'oe'), 'æ', 'ae'),
                         (SELECT src FROM _acc), (SELECT dst FROM _acc)),
               '[^a-z0-9]+', '-', 'g')) AS fixed,
           trim(both '-' from regexp_replace(lower(label), '[^a-z0-9]+', '-', 'g')) AS old
    FROM oidc_connectors
) n
WHERE c.id = n.id AND c.slug = n.old AND n.fixed <> n.old AND n.fixed <> ''
  AND NOT EXISTS (SELECT 1 FROM oidc_connectors o WHERE o.slug = n.fixed);

UPDATE git_spaces c
SET slug = n.fixed
FROM (
    SELECT id,
           trim(both '-' from regexp_replace(
               translate(replace(replace(lower(name), 'œ', 'oe'), 'æ', 'ae'),
                         (SELECT src FROM _acc), (SELECT dst FROM _acc)),
               '[^a-z0-9]+', '-', 'g')) AS fixed,
           trim(both '-' from regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g')) AS old
    FROM git_spaces
) n
WHERE c.id = n.id AND c.slug = n.old AND n.fixed <> n.old AND n.fixed <> ''
  AND NOT EXISTS (SELECT 1 FROM git_spaces o WHERE o.slug = n.fixed);

DROP TABLE _acc;
