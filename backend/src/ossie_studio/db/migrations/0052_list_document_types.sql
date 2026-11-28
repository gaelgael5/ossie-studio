-- « genre » devient « type de document » (ticket docflow b90fcc85) : la primitive
-- `list_write_kinds` s'appelle désormais `list_document_types`. Les profils agents
-- qui la donnaient reçoivent la nouvelle (même place dans le catalogue, même droit).
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT DISTINCT p
        FROM unnest(array_replace(primitives, 'list_write_kinds', 'list_document_types')) AS p
        ORDER BY p
    ),
    updated_at = now()
WHERE 'list_write_kinds' = ANY(primitives);
