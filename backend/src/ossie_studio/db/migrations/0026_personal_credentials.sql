-- Identifiant personnel par forge : chaque utilisateur publie avec SON jeton.
-- Un identifiant par (utilisateur, connexion de forge) : cinq espaces sur la même
-- forge = un seul jeton à saisir. État explicite plutôt qu'un secret vide :
-- a_saisir (créé, pas de valeur), jamais_teste (valeur posée ou changée),
-- valide, refuse (avec error_code). Privé : jamais lu par un autre compte.
CREATE TABLE personal_credentials (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    connection_id    uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
    value_encrypted  bytea,
    status           text NOT NULL DEFAULT 'a_saisir'
                     CHECK (status IN ('a_saisir', 'jamais_teste', 'valide', 'refuse')),
    forge_login      text,
    error_code       text,
    checked_at       timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_personal_credential UNIQUE (user_id, connection_id),
    -- Une valeur absente n'est jamais « testée » : seul a_saisir l'autorise.
    CONSTRAINT ck_personal_credential_value CHECK (
        (status = 'a_saisir') = (value_encrypted IS NULL)
    )
);
