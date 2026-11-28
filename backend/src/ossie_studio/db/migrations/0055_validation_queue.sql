-- File de validation sérialisée (enabler docflow 6b171b74, epic « Index des documents
-- et validation avant publication »). Gael : « on ne peut pas lancer des processus de
-- validation en parallèle, et on ne peut pas lancer plusieurs processus de validation
-- de différents utilisateurs en même temps. Il faut plutôt trouver un moyen de les
-- sérialiser ».
--
-- Une ligne par demande de passe sur une vue (branche publiée : `user_id` NULL, ou
-- brouillon d'une personne). Elle survit à un redémarrage. Deux index la tiennent :
-- - une seule demande **en attente** par vue (déduplication) ;
-- - une seule passe **en cours** pour toute l'application (sérialisation, même avec
--   plusieurs processus serveur).
-- Une demande terminée est retirée ; son résultat est dans la vue de l'index.
CREATE TABLE validation_requests (
    id            bigserial PRIMARY KEY,
    space_id      uuid NOT NULL REFERENCES git_spaces (id) ON DELETE CASCADE,
    branch_id     uuid NOT NULL REFERENCES space_branches (id) ON DELETE CASCADE,
    user_id       uuid REFERENCES users (id) ON DELETE CASCADE,
    -- Ce qui l'a demandée : import, collect, write_batch, publication_page, periodic, publish.
    trigger       text NOT NULL,
    state         text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'running')),
    requested_at  timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    -- Signe de vie de la passe en cours : une passe muette depuis trop longtemps
    -- (serveur arrêté pendant la passe) est remise en attente.
    heartbeat_at  timestamptz,
    done          integer NOT NULL DEFAULT 0,
    total         integer NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX uq_validation_pending ON validation_requests (space_id, branch_id, user_id)
    NULLS NOT DISTINCT WHERE state = 'pending';
CREATE UNIQUE INDEX uq_validation_running ON validation_requests ((true)) WHERE state = 'running';
CREATE INDEX ix_validation_order ON validation_requests (state, id);

-- La dernière passe réussie d'une vue ; NULL après un échec (la vue n'est pas validée),
-- avec le message de l'échec.
ALTER TABLE doc_index_view
    ADD COLUMN validated_at timestamptz,
    ADD COLUMN validation_error text;
