-- Valeurs **globales** des paramètres de plugins de parsing, réglées dans la page
-- de gestion des plugins. Une valeur par (plugin, paramètre). Les paramètres
-- eux-mêmes (slug, libellé, doc, type, défaut) sont déclarés par le plugin dans
-- le code ; seules leurs valeurs vivent ici.
CREATE TABLE plugin_parameters (
    plugin_id  text NOT NULL,
    slug       text NOT NULL,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (plugin_id, slug)
);
