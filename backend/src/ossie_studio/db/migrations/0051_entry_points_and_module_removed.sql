-- Points d'entrée lus par le genre `point_entree`, modules par les lectures communes
-- (epic « Primitives MCP — lectures communes », ticket docflow 15c43ed1) :
-- `list_entry_points` et `get_module` sont retirées, remplacées par `list_documents`
-- et `read_document` (genres `point_entree`, `schema_raw`, `extension_raw`). Les profils
-- agents qui en donnaient une reçoivent les deux primitives communes (même règle que
-- 0042).
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT DISTINCT p
        FROM unnest(primitives || ARRAY['list_documents', 'read_document']) AS p
        WHERE p NOT IN ('list_entry_points', 'get_module')
        ORDER BY p
    ),
    updated_at = now()
WHERE primitives && ARRAY['list_entry_points', 'get_module'];
