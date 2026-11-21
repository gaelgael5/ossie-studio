-- Connexions aux bases de données (PostgreSQL, SQL Server, Snowflake…), sur la
-- même page que les forges. Elles servent à lire la STRUCTURE (schémas, tables),
-- jamais les données. Le mot de passe ou la clé vit dans la table des secrets,
-- désigné par son slug ; la connexion n'en garde que le slug, et un secret
-- encore désigné ne peut pas être supprimé (ON DELETE RESTRICT).
CREATE TABLE db_connections (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label           text NOT NULL,
    engine          text NOT NULL,  -- postgres | sqlserver | snowflake
    target          text NOT NULL,  -- hôte ou compte, pour l'affichage
    params          jsonb NOT NULL DEFAULT '{}'::jsonb,  -- champs propres au moteur
    secret_slug     text NOT NULL REFERENCES secrets (slug) ON DELETE RESTRICT,
    identity        text NOT NULL DEFAULT '',
    server_version  text NOT NULL DEFAULT '',
    last_status     text NOT NULL DEFAULT 'unknown',
    last_error_code text,
    last_checked_at timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Un même moteur, même cible, même identité → doublon refusé.
    CONSTRAINT uq_db_connection UNIQUE (engine, target, identity)
);
