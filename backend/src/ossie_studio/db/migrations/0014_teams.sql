-- Référentiel des ÉQUIPES. Décision de Gael : l'équipe sert à l'AFFICHAGE
-- seulement — elle ne donne ni ne retire aucun droit et ne rattache rien.
-- Aucune vérification de droits ne doit lire l'équipe.
--
-- Le nom est la valeur exacte du claim (casse et espaces) : l'annuaire fait foi
-- pour les noms d'équipe. Une valeur jamais vue crée l'équipe, marquée « venue
-- de l'OIDC » (origin = 'oidc') et rattachée au connecteur qui l'a vue.
CREATE TABLE teams (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text NOT NULL UNIQUE,
    slug                text NOT NULL DEFAULT '',
    origin              text NOT NULL DEFAULT 'manual',
    source_connector_id uuid REFERENCES oidc_connectors (id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT teams_origin_check CHECK (origin IN ('oidc', 'manual'))
);

-- Une personne appartient à UNE SEULE équipe. Rattachement conservé si l'équipe
-- est supprimée du référentiel (SET NULL). « team_pinned » : équipe fixée à la
-- main (page Utilisateurs) ; les correspondances OIDC ne la remplacent plus.
ALTER TABLE users ADD COLUMN team_id uuid REFERENCES teams (id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN team_pinned boolean NOT NULL DEFAULT false;

-- Chemin du claim qui porte l'équipe sur ce connecteur (ex. « department »).
ALTER TABLE oidc_connectors ADD COLUMN team_claim text NOT NULL DEFAULT '';
