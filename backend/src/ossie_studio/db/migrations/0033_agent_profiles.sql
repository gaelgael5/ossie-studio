-- Profils agents : ce qu'un agent peut appeler sur la surface MCP. Un profil
-- agent ne s'assigne jamais à une personne : il se choisit à l'émission d'une
-- clé d'API ou à la connexion OAuth d'un client. Le catalogue des primitives
-- vit dans le code ; un profil n'en stocke que les identifiants.
CREATE TABLE agent_profiles (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL DEFAULT gen_random_uuid()::text,
    name        text NOT NULL,
    description text NOT NULL DEFAULT '',
    primitives  text[] NOT NULL DEFAULT '{}',
    -- Reçoit toutes les primitives de lecture du catalogue, y compris celles
    -- ajoutées plus tard. Sans cette marque, une primitive nouvelle n'est donnée
    -- à aucun profil (fermé par défaut).
    all_read    boolean NOT NULL DEFAULT false,
    -- Profil livré : se duplique, ne se supprime pas.
    is_builtin  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_agent_profile_slug ON agent_profiles (slug);
CREATE UNIQUE INDEX uq_agent_profile_name ON agent_profiles (name);

INSERT INTO agent_profiles (slug, name, description, all_read, is_builtin)
VALUES ('lecture', 'Lecture', 'Toutes les primitives de lecture, aucune d''écriture.', true, true);
