-- Une ÉQUIPE porte un PROFIL (décision de Gael, 20 novembre 2026) : appartenir à
-- l'équipe donne les capacités de ce profil (calculées en union avec le profil
-- personnel, côté contrôle d'accès). Un seul profil par équipe, ou aucun.
--
-- ON DELETE SET NULL : supprimer un profil encore associé à des équipes les laisse
-- SANS profil (jamais de référence cassée ni de capacité fantôme).
ALTER TABLE teams ADD COLUMN profile_id uuid REFERENCES profiles (id) ON DELETE SET NULL;
