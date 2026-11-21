-- Certifications : des niveaux de qualité (Or, Argent, Bronze…) assignables aux
-- assets (principe repris d'Entropy Data). Chaque niveau est adossé à un tag
-- (ex. « certification/or ») : poser ce tag sur un asset activera le badge — la
-- pose sur les assets viendra avec la curation. Le rang fixe l'ordre, le plus
-- bas en premier (Or = 1). Un niveau « restreint » ne s'assigne qu'avec la
-- capacité dédiée (à venir avec l'epic Profils).
CREATE TABLE certifications (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    rank        integer NOT NULL,
    description text NOT NULL DEFAULT '',
    criteria    text NOT NULL DEFAULT '',
    tag         text NOT NULL UNIQUE,
    icon        text NOT NULL DEFAULT '',
    restricted  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
