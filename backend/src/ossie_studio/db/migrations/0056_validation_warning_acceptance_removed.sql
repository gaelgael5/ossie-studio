-- Règles de validation par type de document et règle d'or (ticket docflow d7139b17).
-- Gael : « tu ne peux pousser que des choses valides et conformes ». Un avertissement
-- ne s'accepte plus : il informe et ne bloque jamais ; une erreur bloque, même
-- préexistante. La primitive `accept_validation_warning` est retirée du catalogue (même
-- règle que les autres retraits : elle sort des profils agents qui la donnaient), et
-- les avertissements acceptés des brouillons disparaissent.
UPDATE agent_profiles
SET primitives = ARRAY(
        SELECT p FROM unnest(primitives) AS p
        WHERE p <> 'accept_validation_warning'
        ORDER BY p
    ),
    updated_at = now()
WHERE primitives && ARRAY['accept_validation_warning'];

DELETE FROM draft_decisions WHERE kind = 'warning_accepted';

ALTER TABLE draft_decisions DROP CONSTRAINT IF EXISTS draft_decisions_kind_check;
ALTER TABLE draft_decisions
    ADD CONSTRAINT draft_decisions_kind_check CHECK (kind IN ('impact_verified'));
