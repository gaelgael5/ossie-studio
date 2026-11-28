-- Éléments gardés car utilisés lors d'un import (ticket docflow 6d36073b) : la
-- stratégie de suppression d'un import prévoyait de supprimer un élément absent du
-- fichier, mais il était encore utilisé (une relation vise l'objet) ; il est gardé
-- et l'import se termine. La page « Éléments gardés car utilisés » de l'exécution
-- explique chaque cas, avec ses utilisations au moment de l'import.
--
-- Une ligne par exécution qui en a produit, rattachée à sa ligne du journal des
-- imports (`audit_log`, qui n'accepte que des valeurs simples dans `detail`).
-- Comme le journal, elle ne change plus une fois écrite.
CREATE TABLE import_kept (
    audit_id bigint PRIMARY KEY REFERENCES audit_log (id),
    items jsonb NOT NULL
);
