-- Connexions aux forges Git (GitHub, Azure DevOps, GitLab). Le jeton n'est pas
-- stocké ici : il vit dans la table des secrets, la connexion n'en garde que le
-- slug. Une connexion porte l'adresse de la forge ou de l'organisation, pas
-- celle d'un dépôt.
CREATE TABLE connections (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label           text NOT NULL,
    forge           text NOT NULL,
    url             text NOT NULL,
    secret_slug     text NOT NULL,
    identity        text NOT NULL,
    last_status     text NOT NULL,
    last_error_code text,
    last_checked_at timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Deux connexions sur la même forge, même URL et même identité → doublon.
    CONSTRAINT uq_connection_forge_url_identity UNIQUE (forge, url, identity)
);
