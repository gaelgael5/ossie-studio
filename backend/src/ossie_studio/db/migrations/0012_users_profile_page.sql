-- Page Utilisateurs : rechercher un compte connu et lui assigner un profil.
-- On ajoute le nom d'affichage (capté des claims OIDC à la création), la date
-- de dernière connexion, et une clé de recherche normalisée (minuscules, sans
-- accents) sur nom + email — une recherche insensible à la casse et aux accents
-- sans dépendre d'une extension Postgres (unaccent) non triviale à installer.
ALTER TABLE users ADD COLUMN display_name  text NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN last_login_at timestamptz;
ALTER TABLE users ADD COLUMN search_key    text NOT NULL DEFAULT '';

UPDATE users SET search_key = lower(email);

CREATE INDEX ix_users_search_key ON users (search_key);
