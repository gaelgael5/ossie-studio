-- Index des documents d'un espace (enabler docflow 15863091, epic « Index des
-- documents et validation avant publication »). Gael : « c'est un outil de
-- validation et un outil de recherche ».
--
-- C'est un CACHE dérivé de Git, jamais une source de vérité : on peut le vider et le
-- reconstruire sans rien perdre (sauf les résultats de validation, qui se
-- recalculent).
--
-- Deux niveaux :
-- 1. le CONTENU, global par espace : une ligne par fichier et par empreinte (blob
--    Git, la « version » des primitives). Le chemin fait partie de la clé : le type
--    de document et l'identité se lisent dans le chemin. Deux vues qui ont le même
--    contenu pour un fichier partagent la même ligne : il n'est indexé (et validé)
--    qu'une fois, pour tout le monde ;
-- 2. les VUES : la branche publiée d'un espace (`user_id` NULL) ou le brouillon
--    d'une personne sur une branche ; une liste « chemin → contenu », avec l'état
--    de chaque fichier dans la vue.

CREATE TABLE doc_index_content (
    id               bigserial PRIMARY KEY,
    space_id         uuid NOT NULL REFERENCES git_spaces (id) ON DELETE CASCADE,
    path             text NOT NULL,
    blob             text NOT NULL,
    -- Version de l'extraction : une extraction plus récente réindexe le contenu.
    indexer_version  integer NOT NULL,
    -- Les documents que porte le fichier : type, identité, nom, champs filtrables.
    documents        jsonb NOT NULL DEFAULT '[]',
    -- Les identités qu'il porte, et celles qu'il cite (forme `<type>:<identité>`).
    keys             text[] NOT NULL DEFAULT '{}',
    refs             text[] NOT NULL DEFAULT '{}',
    -- Validation du document seul (ticket des règles), et la version des règles
    -- qui l'a produite ; NULL : pas encore validé.
    problems         jsonb,
    rules_version    integer,
    indexed_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_doc_index_content UNIQUE (space_id, path, blob)
);

CREATE INDEX ix_doc_index_content_refs ON doc_index_content USING gin (refs);
CREATE INDEX ix_doc_index_content_keys ON doc_index_content USING gin (keys);

CREATE TABLE doc_index_view (
    id           bigserial PRIMARY KEY,
    space_id     uuid NOT NULL REFERENCES git_spaces (id) ON DELETE CASCADE,
    branch_id    uuid NOT NULL REFERENCES space_branches (id) ON DELETE CASCADE,
    -- NULL : la branche publiée ; sinon le brouillon de cette personne.
    user_id      uuid REFERENCES users (id) ON DELETE CASCADE,
    -- La tête publiée sur laquelle la vue a été comparée au disque, et quand ;
    -- NULL : jamais comparée (ses fichiers sont tous à valider).
    head         text,
    compared_at  timestamptz,
    CONSTRAINT uq_doc_index_view UNIQUE NULLS NOT DISTINCT (space_id, branch_id, user_id)
);

CREATE TABLE doc_index_file (
    view_id     bigint NOT NULL REFERENCES doc_index_view (id) ON DELETE CASCADE,
    path        text NOT NULL,
    content_id  bigint NOT NULL REFERENCES doc_index_content (id),
    status      text NOT NULL DEFAULT 'a_valider'
                CHECK (status IN ('a_valider', 'valide', 'en_erreur')),
    -- Les règles entre fichiers (références, unicité…) dépendent de la vue : leurs
    -- problèmes sont rangés ici, pas avec le contenu.
    problems    jsonb NOT NULL DEFAULT '[]',
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (view_id, path)
);

CREATE INDEX ix_doc_index_file_content ON doc_index_file (content_id);
CREATE INDEX ix_doc_index_file_status ON doc_index_file (view_id, status);
