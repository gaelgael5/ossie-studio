-- Comptes locaux. Le rôle vit ici, en base — jamais déduit d'un token.
-- password_hash est nullable : un compte provisionné par OIDC n'a pas de mot
-- de passe local.
CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL UNIQUE,
    password_hash text,
    role          text NOT NULL,
    is_bootstrap  boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Il ne peut jamais exister deux administrateurs issus de l'écran de bootstrap :
-- l'unicité est tranchée par la base, pas par une lecture préalable.
CREATE UNIQUE INDEX uq_users_bootstrap_admin ON users (is_bootstrap) WHERE is_bootstrap;
