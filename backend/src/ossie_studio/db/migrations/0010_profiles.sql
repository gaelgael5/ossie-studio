-- Profils d'accès : un profil donne un jeu de capacités (cases indépendantes),
-- et chaque utilisateur a un seul profil. Remplace le « tout ou rien »
-- administrateur par des droits par fonction (le catalogue des capacités vit
-- dans le code). Le profil « Administrateur » est livré d'office, a toutes les
-- capacités (calculées à l'exécution, y compris celles ajoutées plus tard), et
-- ne se supprime ni ne se modifie.
CREATE TABLE profiles (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL UNIQUE,
    description  text NOT NULL DEFAULT '',
    capabilities text[] NOT NULL DEFAULT '{}',
    is_admin     boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Un seul profil administrateur.
CREATE UNIQUE INDEX uq_profile_admin ON profiles ((is_admin)) WHERE is_admin;

INSERT INTO profiles (name, description, is_admin)
VALUES ('Administrateur', 'Toutes les capacités.', true);

-- Un utilisateur a un seul profil ; null = aucun accès (en attendant le profil
-- par défaut des nouveaux arrivants, ticket suivant).
ALTER TABLE users ADD COLUMN profile_id uuid REFERENCES profiles (id) ON DELETE SET NULL;

-- Les comptes administrateurs existants reçoivent le profil Administrateur.
UPDATE users SET profile_id = (SELECT id FROM profiles WHERE is_admin) WHERE role = 'admin';
