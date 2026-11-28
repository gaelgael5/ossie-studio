-- Schémas raw et d'assets en lecture seule par les primitives communes (epic
-- « Primitives MCP — écriture commune par genre », ticket docflow 56f3784a) :
-- `list_raw_schemas` et `list_assets` sont retirées, remplacées par
-- `list_documents(kind: schema_raw | schema_asset)` et `read_document`. Les profils
-- agents qui en donnaient une reçoivent les deux primitives communes (même règle que
-- 0042).
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT DISTINCT p
        FROM unnest(primitives || ARRAY['list_documents', 'read_document']) AS p
        WHERE p NOT IN ('list_raw_schemas', 'list_assets')
        ORDER BY p
    ),
    updated_at = now()
WHERE primitives && ARRAY['list_raw_schemas', 'list_assets'];
