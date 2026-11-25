-- Décision prise par un agent pour la personne (impact vérifié, avertissement
-- accepté) : le nom de l'agent, repris dans le message du commit de publication
-- (« … (via l'agent claude) »). NULL : décision de la personne elle-même.
ALTER TABLE draft_decisions ADD COLUMN via text;
