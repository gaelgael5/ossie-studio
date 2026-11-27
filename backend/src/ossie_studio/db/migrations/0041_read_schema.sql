-- Primitive commune `read_schema` (ticket docflow 06ef8679) : elle remplace
-- `read_raw_schema`, `read_asset` et `read_referentiel_model`, retirées du
-- catalogue. Les profils agents qui en donnaient au moins une reçoivent
-- `read_schema` à la place (un identifiant retiré du catalogue ne donne plus
-- rien : sans cette migration, ces profils perdraient la lecture des schémas).
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT DISTINCT p
        FROM unnest(primitives || ARRAY['read_schema']) AS p
        WHERE p NOT IN ('read_raw_schema', 'read_asset', 'read_referentiel_model')
        ORDER BY p
    ),
    updated_at = now()
WHERE primitives && ARRAY['read_raw_schema', 'read_asset', 'read_referentiel_model'];
