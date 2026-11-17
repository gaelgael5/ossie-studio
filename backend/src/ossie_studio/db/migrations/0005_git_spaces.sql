-- Espaces Git : un dépôt où Ossie-studio a le droit d'écrire, pousser et taguer.
-- Un dépôt n'appartient qu'à un espace (deux espaces mélangeraient branches,
-- commits et versions). Le dépôt n'est rattaché qu'après les cinq droits
-- éprouvés. La connexion ne peut pas être supprimée tant qu'un espace l'utilise
-- (RESTRICT) ; l'API le refuse avec la liste des espaces avant d'en arriver là.
CREATE TABLE git_spaces (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name              text NOT NULL UNIQUE,
    connection_id     uuid NOT NULL REFERENCES connections (id) ON DELETE RESTRICT,
    repo_url          text NOT NULL UNIQUE,
    last_probe_status text,
    last_probe_at     timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);
