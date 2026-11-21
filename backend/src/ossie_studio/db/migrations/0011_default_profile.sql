-- Profil par défaut des nouveaux utilisateurs : tout compte créé à la première
-- connexion (OIDC) le reçoit. Un seul profil porte la marque à la fois.
-- À l'installation, c'est un profil « Sans accès » (aucune capacité), jamais
-- l'Administrateur : un connecteur ouvert à tout un annuaire ne doit pas donner
-- de droits d'office.
ALTER TABLE profiles ADD COLUMN is_default boolean NOT NULL DEFAULT false;

CREATE UNIQUE INDEX uq_profile_default ON profiles ((is_default)) WHERE is_default;

INSERT INTO profiles (name, description, is_default)
VALUES ('Sans accès', 'Aucune capacité ; profil par défaut des nouveaux comptes.', true);
