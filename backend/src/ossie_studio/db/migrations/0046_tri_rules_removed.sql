-- Règles dans les primitives communes (epic « Primitives MCP — écriture commune par
-- genre », ticket docflow 71347227) : `get_tri_rules` et `set_tri_rules` sont
-- retirées, remplacées par le genre `regles_tri` (`read_document`, `list_documents`,
-- `write_documents`, `patch_document`). Comme pour les autres retraits de l'epic : une
-- lecture retirée donne les lectures communes (0042) ; une écriture retirée est
-- seulement nettoyée, sans donner l'écriture commune, qui écrit tous les genres (0043).
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT DISTINCT p
        FROM unnest(
            primitives
            || CASE WHEN 'get_tri_rules' = ANY(primitives)
                    THEN ARRAY['list_documents', 'read_document'] ELSE ARRAY[]::text[] END
        ) AS p
        WHERE p NOT IN ('get_tri_rules', 'set_tri_rules')
        ORDER BY p
    ),
    updated_at = now()
WHERE primitives && ARRAY['get_tri_rules', 'set_tri_rules'];
