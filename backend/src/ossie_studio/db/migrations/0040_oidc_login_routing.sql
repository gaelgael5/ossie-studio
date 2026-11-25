-- Connexion en deux temps (ticket docflow 145b1414) : l'écran de connexion
-- demande l'email d'abord, puis oriente vers le connecteur OIDC dont le domaine
-- correspond. Colonnes additives avec valeur par défaut : les connecteurs
-- existants restent valides et ne captent aucun domaine tant qu'on n'en saisit pas.
ALTER TABLE oidc_connectors ADD COLUMN email_domains text[] NOT NULL DEFAULT '{}';
-- Phrase affichée sur l'écran de connexion (« Continuez avec Pickup ») ;
-- vide → l'interface affiche « Continuer avec <libellé> ».
ALTER TABLE oidc_connectors ADD COLUMN login_prompt text NOT NULL DEFAULT '';
CREATE INDEX ix_oidc_connectors_email_domains ON oidc_connectors USING gin (email_domains);
