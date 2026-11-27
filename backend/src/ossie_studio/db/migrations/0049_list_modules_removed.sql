-- Modules lus par le genre `schema_raw` (epic « Primitives MCP — lectures communes »,
-- ticket docflow dcc52af7) : `list_modules` est retirée, remplacée par
-- `list_documents(kind: schema_raw, filters: {kind: module})` et `read_document`. Les
-- profils agents qui la donnaient reçoivent les deux primitives communes (même règle
-- que 0042).
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT DISTINCT p
        FROM unnest(primitives || ARRAY['list_documents', 'read_document']) AS p
        WHERE p NOT IN ('list_modules')
        ORDER BY p
    ),
    updated_at = now()
WHERE primitives && ARRAY['list_modules'];
