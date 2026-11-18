-- Schémas de classification (gouvernance des données, principe repris d'Entropy
-- Data). Un schéma classe les objets selon une échelle (ou un ensemble de
-- valeurs, en multi-valeurs). L'application des niveaux aux assets (écriture,
-- dérivation, réécriture au renommage) viendra avec la curation.
--
-- L'id d'un schéma est dérivé du nom à la création et NON modifiable : c'est la
-- clé dans les fichiers d'assets. Un seul schéma de l'installation est le
-- schéma principal (index unique partiel). Un schéma multi-valeurs ne peut pas
-- être principal (vérifié par l'application).
CREATE TABLE classification_schemes (
    id            text PRIMARY KEY,
    name          text NOT NULL,
    description   text NOT NULL DEFAULT '',
    badge_prefix  text NOT NULL DEFAULT '',
    multi_value   boolean NOT NULL DEFAULT false,
    is_primary    boolean NOT NULL DEFAULT false,
    display_order integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Au plus un schéma principal sur l'installation.
CREATE UNIQUE INDEX uq_classification_primary
    ON classification_schemes ((is_primary)) WHERE is_primary;

-- Un niveau : le `name` est la valeur canonique stockée dans les assets (stable,
-- unique dans le schéma, sans virgule) ; le `label` est l'affichage traduisible
-- du badge. Le `rank` fait foi à la dérivation (le plus élevé l'emporte).
CREATE TABLE classification_levels (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scheme_id   text NOT NULL REFERENCES classification_schemes (id) ON DELETE CASCADE,
    name        text NOT NULL,
    label       text NOT NULL DEFAULT '',
    short_name  text NOT NULL DEFAULT '',
    rank        integer NOT NULL DEFAULT 0,
    description text NOT NULL DEFAULT '',
    criteria    text NOT NULL DEFAULT '',
    color       text NOT NULL DEFAULT '',
    icon        text NOT NULL DEFAULT '',
    position    integer NOT NULL DEFAULT 0,
    CONSTRAINT uq_level_name UNIQUE (scheme_id, name),
    CONSTRAINT uq_level_short_name UNIQUE (scheme_id, short_name)
);

-- Installation neuve : le schéma « Classification » existe, principal, avec ses
-- quatre niveaux (noms canoniques = libellés par défaut, en français).
INSERT INTO classification_schemes (id, name, description, is_primary, display_order)
VALUES ('classification', 'Classification',
        'Niveau de confidentialité de l''objet.', true, 0);

INSERT INTO classification_levels (scheme_id, name, label, short_name, rank, position) VALUES
    ('classification', 'Public',       'Public',       'PUB',   1, 0),
    ('classification', 'Interne',      'Interne',      'INT',   2, 1),
    ('classification', 'Confidentiel', 'Confidentiel', 'CONF',  3, 2),
    ('classification', 'Restreint',    'Restreint',    'RESTR', 4, 3);
