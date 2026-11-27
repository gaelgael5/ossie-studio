-- Écriture commune par genre (epic « Primitives MCP — écriture commune par genre »,
-- ticket docflow 62fbfe11) : les treize écritures de la gouvernance sont retirées du
-- catalogue (`create_tag`, `update_tag`, `delete_tag`, `create_classification`,
-- `update_classification`, `delete_classification`, `create_level`, `update_level`,
-- `delete_level`, `reorder_levels`, `create_certification`, `update_certification`,
-- `delete_certification`) : `write_documents` et `patch_document` font la même chose.
-- Les profils agents qui en citaient une sont nettoyés. Contrairement aux lectures
-- (0041, 0042), on ne leur donne pas l'écriture commune à la place : elle écrit tous
-- les genres, et l'ajouter en silence élargirait ce que le profil permet à l'agent ;
-- c'est à l'administrateur de la donner.
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT p
        FROM unnest(primitives) AS p
        WHERE p NOT IN (
            'create_tag', 'update_tag', 'delete_tag', 'create_classification',
            'update_classification', 'delete_classification', 'create_level',
            'update_level', 'delete_level', 'reorder_levels', 'create_certification',
            'update_certification', 'delete_certification'
        )
        ORDER BY p
    ),
    updated_at = now()
WHERE primitives && ARRAY[
    'create_tag', 'update_tag', 'delete_tag', 'create_classification',
    'update_classification', 'delete_classification', 'create_level',
    'update_level', 'delete_level', 'reorder_levels', 'create_certification',
    'update_certification', 'delete_certification'
];
