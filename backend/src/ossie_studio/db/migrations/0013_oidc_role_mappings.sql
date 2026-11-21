-- Correspondances « rôle du jeton → profil » par connecteur OIDC. Les rôles du
-- jeton servent à CHOISIR un profil ; les capacités restent celles du profil en
-- base (un rôle n'accorde jamais une capacité par lui-même). Les correspondances
-- sont ORDONNÉES : à la connexion, la première qui correspond l'emporte.
ALTER TABLE oidc_connectors ADD COLUMN roles_claim text NOT NULL DEFAULT '';

-- Un profil « fixé à la main » (assigné depuis la page Utilisateurs) n'est plus
-- remplacé par les correspondances tant que la marque reste.
ALTER TABLE users ADD COLUMN profile_pinned boolean NOT NULL DEFAULT false;
UPDATE users SET profile_pinned = true WHERE role = 'admin';

CREATE TABLE oidc_role_mappings (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connector_id uuid NOT NULL REFERENCES oidc_connectors (id) ON DELETE CASCADE,
    position     integer NOT NULL DEFAULT 0,
    role_value   text NOT NULL,
    profile_id   uuid NOT NULL REFERENCES profiles (id) ON DELETE RESTRICT,
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- Doublon exact (même connecteur, même valeur, même profil) refusé.
    CONSTRAINT uq_role_mapping UNIQUE (connector_id, role_value, profile_id)
);

CREATE INDEX ix_role_mappings_connector ON oidc_role_mappings (connector_id, position);
