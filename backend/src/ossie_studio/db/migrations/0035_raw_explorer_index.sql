-- Index de l'explorateur raw : ce que la couche raw de chaque source contient
-- (objets, documents, badges), tel que le voit un utilisateur sur une branche.
-- C'est un CACHE dérivé de Git, jamais une source de vérité : il se reconstruit
-- quand sa signature (tête publiée de la branche + révision du brouillon) ne
-- correspond plus, et peut être vidé sans rien perdre. Sans lui, chaque clic
-- dans l'explorateur rematérialisait la vue de brouillon et relisait raw/.
--
-- Une ligne par source ; `view` distingue la vue fusionnée (avec le brouillon)
-- de la vue publiée. Les dossiers ne sont pas stockés : ils se calculent à la
-- volée pour le mode d'affichage demandé.
CREATE TABLE raw_explorer_index (
    space_id     uuid NOT NULL REFERENCES git_spaces (id) ON DELETE CASCADE,
    branch_id    uuid NOT NULL REFERENCES space_branches (id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    view         text NOT NULL CHECK (view IN ('merged', 'published')),
    source_slug  text NOT NULL,
    signature    text NOT NULL,
    label        text NOT NULL,
    source_type  text NOT NULL,
    collected    boolean NOT NULL,
    objects      integer NOT NULL,
    documents    integer NOT NULL,
    entries      jsonb NOT NULL,
    built_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (space_id, branch_id, user_id, view, source_slug)
);

-- Une branche sans aucune source garde une ligne témoin (source_slug = '') :
-- sans elle, « aucune source » se recalculerait à chaque visite.
