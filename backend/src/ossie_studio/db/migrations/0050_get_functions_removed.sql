-- Méthodes et fonctions lues par le genre `extension_raw` (epic « Primitives MCP —
-- lectures communes », ticket docflow 2c069134) : `get_functions` est retirée,
-- remplacée par `read_document(kind: extension_raw)` et `list_documents`. Les profils
-- agents qui la donnaient reçoivent les deux primitives communes (même règle que
-- 0042).
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT DISTINCT p
        FROM unnest(primitives || ARRAY['list_documents', 'read_document']) AS p
        WHERE p NOT IN ('get_functions')
        ORDER BY p
    ),
    updated_at = now()
WHERE primitives && ARRAY['get_functions'];
