-- Page de publication (ticket 48618517) : ce que la dernière comparaison entre le
-- disque et l'index a vu, et ce qui a demandé la dernière passe complète.
--   disk_files : fichiers présents sur le disque (la révision et le brouillon) ;
--   detected : fichiers trouvés nouveaux ou changés par la comparaison, c'est-à-dire
--     modifiés hors de l'application (vide au premier remplissage de la vue) ;
--   validated_trigger : déclencheur de la dernière passe complète (import, collect,
--     write_batch, publication_page, periodic, publish).
ALTER TABLE doc_index_view
    ADD COLUMN disk_files integer,
    ADD COLUMN detected text[] NOT NULL DEFAULT '{}',
    ADD COLUMN validated_trigger text;
