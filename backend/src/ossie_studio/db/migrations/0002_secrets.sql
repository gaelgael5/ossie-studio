-- Secrets de l'application. La valeur ne vit qu'ici, chiffrée (AES-256-GCM) —
-- pas de coffre externe, pas de valeur déportée dans un fichier.
CREATE TABLE secrets (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            text NOT NULL UNIQUE,
    label           text NOT NULL,
    category        text NOT NULL,
    value_encrypted bytea NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
