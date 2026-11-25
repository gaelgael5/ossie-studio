-- Clés d'API de service : un compte de service, sans personne derrière, qui
-- n'ouvre que la surface MCP. Ses droits sont exactement ceux de son profil
-- agent. La clé n'est rendue qu'une fois, à l'émission : seule son empreinte
-- SHA-256 est gardée (une clé perdue se révoque et se réémet). Le préfixe
-- (12 premiers caractères) sert à la reconnaître à l'écran.
CREATE TABLE api_keys (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label            text NOT NULL,
    prefix           text NOT NULL,
    key_hash         text NOT NULL,
    -- Un profil agent porté par une clé ne se supprime pas (refus nommé).
    agent_profile_id uuid NOT NULL REFERENCES agent_profiles (id) ON DELETE RESTRICT,
    created_by       uuid REFERENCES users (id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    revoked_at       timestamptz,
    revoked_by       uuid REFERENCES users (id) ON DELETE SET NULL,
    -- Tenu à jour au plus une fois par minute (pas une écriture par appel).
    last_used_at     timestamptz
);

CREATE UNIQUE INDEX uq_api_key_hash ON api_keys (key_hash);
