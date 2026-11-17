-- Fournisseurs d'identité OIDC déclarés par un administrateur. L'issuer est
-- unique : deux connecteurs sur le même issuer rendraient l'ancrage ambigu.
CREATE TABLE oidc_connectors (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label                   text NOT NULL,
    issuer                  text NOT NULL UNIQUE,
    client_id               text NOT NULL,
    client_secret_encrypted bytea NOT NULL,
    scopes                  text NOT NULL DEFAULT 'openid email profile',
    created_at              timestamptz NOT NULL DEFAULT now()
);

-- Ancrage d'un compte sur une identité fédérée : le couple (issuer, sub).
-- Ancré sur la chaîne issuer, sans clé étrangère vers le connecteur :
-- supprimer le connecteur conserve l'identité ; la recréer sur le même issuer
-- la retrouve.
CREATE TABLE user_identities (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    issuer     text NOT NULL,
    subject    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_identity_issuer_subject UNIQUE (issuer, subject)
);

CREATE INDEX ix_user_identities_issuer ON user_identities (issuer);
