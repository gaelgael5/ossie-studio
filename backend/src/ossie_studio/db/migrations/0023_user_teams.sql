-- Appartenance MULTIPLE aux équipes. Décision de Gael (20 novembre 2026) : une
-- personne peut appartenir à plusieurs équipes, et l'équipe portera un profil
-- (dans un ticket suivant). On remplace `users.team_id` (une seule équipe) par
-- une table de liaison ; chaque rattachement porte son ORIGINE : « venu de
-- l'OIDC » ou « manuel ». Un rattachement manuel ne sera jamais retiré par une
-- synchronisation OIDC.
CREATE TABLE user_teams (
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    team_id    uuid NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    origin     text NOT NULL DEFAULT 'manual',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, team_id),
    CONSTRAINT user_teams_origin_check CHECK (origin IN ('oidc', 'manual'))
);

CREATE INDEX idx_user_teams_team ON user_teams (team_id);

-- Reprise SANS PERTE : l'équipe actuelle de chaque personne devient un
-- rattachement, l'origine déduite de l'épinglage d'alors (épinglée → manuelle,
-- sinon venue de l'OIDC).
INSERT INTO user_teams (user_id, team_id, origin)
SELECT id, team_id, CASE WHEN team_pinned THEN 'manual' ELSE 'oidc' END
FROM users
WHERE team_id IS NOT NULL;

-- L'ancien modèle « une seule équipe » disparaît : l'appartenance vit désormais
-- dans user_teams.
ALTER TABLE users DROP COLUMN team_id;
ALTER TABLE users DROP COLUMN team_pinned;
