-- Connexion OAuth d'un client MCP (Claude…) : Ossie-studio est son propre
-- serveur d'autorisation pour sa surface MCP. La personne se connecte comme
-- dans l'IHM puis choisit, sur un écran de consentement, le profil agent
-- accordé au client. L'agent agit alors pour elle : droits = profil agent
-- borné par les capacités de la personne, relus à chaque appel.
--
-- Codes et jetons sont opaques ; seule leur empreinte SHA-256 est gardée.

-- Clients enregistrés dynamiquement (RFC 7591). Le secret d'un client
-- confidentiel est chiffré (clé maîtresse), jamais en clair.
CREATE TABLE oauth_clients (
    client_id               text PRIMARY KEY,
    client_name             text NOT NULL DEFAULT '',
    metadata                jsonb NOT NULL,
    client_secret_encrypted bytea,
    created_at              timestamptz NOT NULL DEFAULT now()
);

-- Demande d'autorisation en attente du consentement (courte durée, usage
-- unique). Liée à la première personne connectée qui l'ouvre.
CREATE TABLE oauth_authorization_requests (
    id                               text PRIMARY KEY,
    client_id                        text NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
    redirect_uri                     text NOT NULL,
    redirect_uri_provided_explicitly boolean NOT NULL,
    code_challenge                   text NOT NULL,
    state                            text,
    scopes                           text[] NOT NULL DEFAULT '{}',
    resource                         text NOT NULL,
    user_id                          uuid REFERENCES users (id) ON DELETE CASCADE,
    created_at                       timestamptz NOT NULL DEFAULT now(),
    expires_at                       timestamptz NOT NULL
);

-- Connexion d'un client pour une personne, avec le profil agent choisi. Une
-- seule connexion active par personne et par client.
CREATE TABLE oauth_connections (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    client_id        text NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
    -- Un profil agent en service ne se supprime pas (refus nommé).
    agent_profile_id uuid NOT NULL REFERENCES agent_profiles (id) ON DELETE RESTRICT,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    revoked_at       timestamptz,
    revoked_by       uuid REFERENCES users (id) ON DELETE SET NULL,
    -- Tenu à jour au plus une fois par minute.
    last_used_at     timestamptz
);

CREATE UNIQUE INDEX uq_oauth_connection_active
    ON oauth_connections (user_id, client_id) WHERE revoked_at IS NULL;

-- Codes d'autorisation : usage unique, quelques minutes.
CREATE TABLE oauth_codes (
    code_hash                        text PRIMARY KEY,
    client_id                        text NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
    user_id                          uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    agent_profile_id                 uuid NOT NULL REFERENCES agent_profiles (id) ON DELETE CASCADE,
    redirect_uri                     text NOT NULL,
    redirect_uri_provided_explicitly boolean NOT NULL,
    code_challenge                   text NOT NULL,
    scopes                           text[] NOT NULL DEFAULT '{}',
    resource                         text NOT NULL,
    expires_at                       timestamptz NOT NULL,
    used_at                          timestamptz
);

-- Jetons d'accès (courts) et de rafraîchissement (révocables), rattachés à
-- une connexion : la révoquer les refuse tous dès l'appel suivant.
CREATE TABLE oauth_tokens (
    token_hash    text PRIMARY KEY,
    kind          text NOT NULL CHECK (kind IN ('access', 'refresh')),
    connection_id uuid NOT NULL REFERENCES oauth_connections (id) ON DELETE CASCADE,
    scopes        text[] NOT NULL DEFAULT '{}',
    -- Audience : l'adresse de la surface MCP. Jamais de jeton sans audience.
    resource      text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz
);

CREATE INDEX ix_oauth_tokens_connection ON oauth_tokens (connection_id);
