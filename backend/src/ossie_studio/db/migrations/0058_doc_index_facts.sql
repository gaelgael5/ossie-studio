-- Faits d'un fichier gardés par l'index des documents (ticket 4618ee38, recherche par
-- l'index des schémas raw, assets, extensions et points d'entrée) : ce que
-- l'Explorateur et la curation lisent d'un fichier de `sources/<source>/` (résumé d'un
-- schéma, ancres d'une vue de module, compteurs et points d'entrée d'une extension,
-- manifestes techniques, index des documents et des contrats, manifeste de la source).
-- Une fonction pure du chemin et du contenu, comme `documents` ; NULL : aucun fait.
ALTER TABLE doc_index_content ADD COLUMN facts jsonb;
