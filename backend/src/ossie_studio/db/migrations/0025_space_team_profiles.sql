-- Droits par espace : une équipe rattachée à un espace avec un profil donne à
-- ses membres, sur cet espace seulement, les capacités « déclinables par
-- espace » de ce profil (union avec leur profil de base). Un rattachement par
-- (espace, équipe) ; supprimer l'espace, l'équipe ou le profil supprime le
-- rattachement (jamais de rattachement orphelin).
CREATE TABLE space_team_profiles (
    space_id    uuid NOT NULL REFERENCES git_spaces (id) ON DELETE CASCADE,
    team_id     uuid NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    profile_id  uuid NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (space_id, team_id)
);
CREATE INDEX ix_space_team_profiles_team ON space_team_profiles (team_id);
