-- Historique des collectes : métadonnée opérationnelle (pas du contenu
-- versionné). Une ligne par collecte terminée (y compris « aucun changement »,
-- local_commit NULL).
CREATE TABLE source_collections (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id      uuid NOT NULL,
    branch_slug   text NOT NULL,
    source_slug   text NOT NULL,
    author_email  text NOT NULL,
    source_commit text NOT NULL,
    local_commit  text,            -- NULL = aucun changement, pas de commit
    added         integer NOT NULL DEFAULT 0,
    modified      integer NOT NULL DEFAULT 0,
    deleted       integer NOT NULL DEFAULT 0,
    unchanged     integer NOT NULL DEFAULT 0,
    error_count   integer NOT NULL DEFAULT 0,
    duration_ms   integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_source_collections_lookup
    ON source_collections (space_id, branch_slug, source_slug, created_at DESC);
