-- Brouillon personnel : les modifications d'un utilisateur sur une branche, en
-- attendant d'être publiées en UN commit. Seul ce qui a changé est stocké :
-- pour chaque objet (fichier), son contenu complet et l'empreinte (blob Git) de
-- sa version de base, figée à la première modification — c'est ce qui permet la
-- fusion à trois quand la branche publiée a bougé. Perdu si l'application est
-- perdue : accepté (Git reste la source de vérité du travail publié).
CREATE TABLE drafts (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    space_id     uuid NOT NULL REFERENCES git_spaces (id) ON DELETE CASCADE,
    branch_id    uuid NOT NULL REFERENCES space_branches (id) ON DELETE CASCADE,
    base_commit  text NOT NULL,
    revision     integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_draft_user_branch UNIQUE (user_id, branch_id)
);

CREATE TABLE draft_objects (
    draft_id   uuid NOT NULL REFERENCES drafts (id) ON DELETE CASCADE,
    path       text NOT NULL,
    -- Blob de la version de base ; NULL = objet créé par le brouillon.
    base_blob  text,
    -- Contenu complet ; NULL = objet supprimé par le brouillon.
    content    bytea,
    PRIMARY KEY (draft_id, path),
    CONSTRAINT ck_draft_object_shape CHECK (content IS NOT NULL OR base_blob IS NOT NULL)
);

-- Décisions prises sur le brouillon (impact vérifié, avertissement accepté) :
-- elles partent dans le message du commit de publication.
CREATE TABLE draft_decisions (
    draft_id     uuid NOT NULL REFERENCES drafts (id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('impact_verified', 'warning_accepted')),
    key          text NOT NULL,
    fingerprint  text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (draft_id, kind, key)
);
