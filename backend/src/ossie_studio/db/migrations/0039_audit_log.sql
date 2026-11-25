-- Journal d'audit (ticket docflow cf4434da) : « qui a fait quoi, quand, sur quel
-- objet » pour l'état de configuration tenu en base (secrets, connexions,
-- connecteurs OIDC, profils, profils agents, clés d'API, équipes, utilisateurs).
-- La gouvernance, elle, vit dans Git : son historique est lu dans les commits
-- publiés (décision de Gael, 23/11/2026), aucune ligne ici.
--
-- - `actor_id` sans clé étrangère : la ligne survit à la suppression du compte ;
--   `actor_label` est l'empreinte lisible figée au moment de l'action.
--   NULL = action du système.
-- - Immuable : aucune route ne modifie ni ne supprime une ligne ; le déclencheur
--   le garantit aussi contre une erreur de code.
-- - `detail` ne contient jamais de valeur sensible (filtré à l'écriture).
CREATE TABLE audit_log (
    id bigserial PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor_id uuid,
    actor_label text NOT NULL,
    action text NOT NULL CHECK (action IN ('created', 'updated', 'deleted')),
    object_type text NOT NULL,
    object_id text NOT NULL,
    object_label text NOT NULL DEFAULT '',
    detail jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_log_at_idx ON audit_log (at DESC, id DESC);
CREATE INDEX audit_log_type_idx ON audit_log (object_type, at DESC);

CREATE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
