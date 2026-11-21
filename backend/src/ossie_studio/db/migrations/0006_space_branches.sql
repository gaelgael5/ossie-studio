-- Branches de travail d'un espace. La racine (branche par défaut du dépôt)
-- reste en lecture seule : Ossie-studio n'écrit que dans des branches work/<slug>.
-- Une base par espace ; des filles tirées d'elle.
CREATE TABLE space_branches (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id   uuid NOT NULL REFERENCES git_spaces (id) ON DELETE CASCADE,
    label      text NOT NULL,
    slug       text NOT NULL,
    kind       text NOT NULL,
    git_ref    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_branch_space_slug UNIQUE (space_id, slug)
);

-- Une seule branche de base par espace.
CREATE UNIQUE INDEX uq_one_base_per_space ON space_branches (space_id) WHERE kind = 'base';

-- La branche regardée, retenue par compte et par espace.
CREATE TABLE user_viewed_branch (
    user_id   uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    space_id  uuid NOT NULL REFERENCES git_spaces (id) ON DELETE CASCADE,
    branch_id uuid NOT NULL REFERENCES space_branches (id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, space_id)
);
