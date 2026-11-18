-- Intégrité d'usage des secrets. Une connexion ne peut désigner qu'un secret
-- existant, et un secret encore désigné ne peut pas être supprimé
-- (ON DELETE RESTRICT). C'est le garde-fou qui rend « supprimer si inutilisé »
-- sûr même en concurrence : un secret devenu utilisé entre-temps n'est jamais
-- supprimé, et aucun usage ne peut pointer un secret disparu. Les tickets qui
-- ajoutent un usage (connecteurs OIDC, bases de données) poseront la même
-- contrainte sur leur colonne de slug.
ALTER TABLE connections
    ADD CONSTRAINT fk_connections_secret
    FOREIGN KEY (secret_slug) REFERENCES secrets (slug) ON DELETE RESTRICT;
