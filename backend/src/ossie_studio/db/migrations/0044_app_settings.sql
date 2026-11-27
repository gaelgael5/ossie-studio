-- Paramètres généraux de l'application, réglables sur la page « Paramètres »
-- (ticket docflow ae8fc4b6). Une ligne par paramètre modifié sur la page : elle prime
-- sur la configuration du serveur, qui reste la valeur par défaut (pas de ligne =
-- la configuration). Lue à chaque usage : un changement vaut sans redémarrage.
CREATE TABLE app_settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by uuid REFERENCES users(id) ON DELETE SET NULL
);
