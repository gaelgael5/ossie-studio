-- L'historique des collectes est porté par Git (chaque collecte publiée est un
-- commit qui touche sources/<slug>/) : la table qui le dupliquait est supprimée.
-- L'index de travail scan_files, recalculable, reste.
DROP TABLE IF EXISTS source_collections;
