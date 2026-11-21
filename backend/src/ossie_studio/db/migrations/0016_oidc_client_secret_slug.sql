-- Le client secret d'un connecteur OIDC vit désormais dans la table des secrets
-- (catégorie « oidc »), comme pour les forges. Le connecteur n'en garde que le
-- slug : on peut remplacer la valeur dans Réglages › Secrets sans recréer le
-- connecteur. Migration de l'existant SANS déchiffrer : on recopie les mêmes
-- octets chiffrés (même clef maître) dans la table des secrets.
ALTER TABLE oidc_connectors ADD COLUMN client_secret_slug text;

-- Slug dérivé de l'id du connecteur : bien formé, unique, sans collision.
INSERT INTO secrets (slug, label, category, value_encrypted)
SELECT 'oidc-' || replace(id::text, '-', ''),
       'Client secret — ' || label,
       'oidc',
       client_secret_encrypted
FROM oidc_connectors;

UPDATE oidc_connectors
SET client_secret_slug = 'oidc-' || replace(id::text, '-', '');

-- Verrouiller : le slug devient obligatoire et pointe un secret existant ; un
-- secret encore désigné par un connecteur ne peut pas être supprimé.
ALTER TABLE oidc_connectors ALTER COLUMN client_secret_slug SET NOT NULL;
ALTER TABLE oidc_connectors
    ADD CONSTRAINT fk_oidc_connectors_secret
    FOREIGN KEY (client_secret_slug) REFERENCES secrets (slug) ON DELETE RESTRICT;

-- L'ancienne colonne chiffrée n'a plus lieu d'être : la valeur est dans secrets.
ALTER TABLE oidc_connectors DROP COLUMN client_secret_encrypted;
