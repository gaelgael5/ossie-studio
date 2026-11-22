-- Supprimer un profil agent : il disparaît des listes et des choix, et ne donne
-- plus rien, mais reste référencé par les clés et connexions RÉVOQUÉES qui l'ont
-- porté (leur historique garde son nom). La suppression est refusée tant
-- qu'une clé ou une connexion active le porte (contrôle applicatif, nommé).
ALTER TABLE agent_profiles ADD COLUMN deleted_at timestamptz;

-- Le nom et le slug d'un profil supprimé redeviennent libres.
DROP INDEX uq_agent_profile_slug;
DROP INDEX uq_agent_profile_name;
CREATE UNIQUE INDEX uq_agent_profile_slug ON agent_profiles (slug) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_agent_profile_name ON agent_profiles (name) WHERE deleted_at IS NULL;
