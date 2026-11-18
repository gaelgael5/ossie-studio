-- Langue choisie par un utilisateur, enregistrée sur son compte pour le suivre
-- d'un appareil à l'autre. NULL = aucune préférence (le socle i18n s'applique).
-- Seules les langues proposées (fr, en) sont écrites ; une valeur inconnue lue
-- est traitée comme « anglais » côté interface.
ALTER TABLE users ADD COLUMN language text;
