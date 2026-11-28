-- Lecture commune par genre (epic « Primitives MCP — écriture commune par genre »,
-- ticket docflow 2113e7bc) : `list_documents` et `read_document` remplacent les
-- onze lectures par domaine, retirées du catalogue (`list_carto_types`,
-- `list_carto_objects`, `get_carto_object`, `list_carto_models`, `get_carto_model`,
-- `list_referentiels`, `search_referentiel_values`, `get_referentiel_value`,
-- `list_tags`, `list_classifications`, `list_certifications`). Les profils agents
-- qui en donnaient au moins une reçoivent les deux primitives communes à la place
-- (même règle que 0041 : un identifiant retiré ne donne plus rien).
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT DISTINCT p
        FROM unnest(primitives || ARRAY['list_documents', 'read_document']) AS p
        WHERE p NOT IN (
            'list_carto_types', 'list_carto_objects', 'get_carto_object',
            'list_carto_models', 'get_carto_model', 'list_referentiels',
            'search_referentiel_values', 'get_referentiel_value', 'list_tags',
            'list_classifications', 'list_certifications'
        )
        ORDER BY p
    ),
    updated_at = now()
WHERE primitives && ARRAY[
    'list_carto_types', 'list_carto_objects', 'get_carto_object',
    'list_carto_models', 'get_carto_model', 'list_referentiels',
    'search_referentiel_values', 'get_referentiel_value', 'list_tags',
    'list_classifications', 'list_certifications'
];
