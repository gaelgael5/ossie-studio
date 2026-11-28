-- Contrats OpenAPI en lecture seule par les primitives communes (epic « Primitives
-- MCP — lectures communes », ticket docflow e806a6b4) : `list_contracts` est retirée,
-- remplacée par `list_documents(kind: contrat)` et `read_document`. Les profils agents
-- qui la donnaient reçoivent les deux primitives communes (même règle que 0042).
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT DISTINCT p
        FROM unnest(primitives || ARRAY['list_documents', 'read_document']) AS p
        WHERE p NOT IN ('list_contracts')
        ORDER BY p
    ),
    updated_at = now()
WHERE primitives && ARRAY['list_contracts'];
