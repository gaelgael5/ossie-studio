from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

# Codes d'erreur stables émis par l'API. Le backend ne rédige jamais le message
# affiché : il renvoie un code et ses paramètres, et l'interface les traduit.
# Ajouter une langue ne touche donc jamais cette liste. Cette liste est la
# source de vérité, partagée avec le frontend (frontend/src/errorCodes.json) ;
# un test vérifie qu'elles ne divergent pas.
ERROR_CODES: tuple[str, ...] = (
    "forbidden",
    "unauthenticated",
    "not_found",
    "invalid_request",
    "invalid_credentials",
    "admin_already_exists",
    "secret_slug_taken",
    "secret_not_found",
    "secret_category_unknown",
    "secret_value_required",
    "secret_in_use",
    "oidc_issuer_unreachable",
    "oidc_issuer_already_declared",
    "oidc_secret_invalid",
    "oidc_token_rejected",
    "oidc_email_ambiguous",
    "oidc_email_unverified",
    "oidc_flow_invalid",
    "connection_token_invalid",
    "connection_token_scope_insufficient",
    "connection_url_unreachable",
    "connection_azure_org_not_found",
    "connection_duplicate",
    "connection_slug_taken",
    "space_slug_taken",
    "team_slug_taken",
    "profile_slug_taken",
    "oidc_connector_slug_taken",
    "certification_slug_taken",
    "credential_not_found",
    "credential_value_missing",
    "credential_no_write",
    "credential_write_unverifiable",
    "connection_not_found",
    "connection_in_use",
    "connection_secret_invalid",
    "space_name_taken",
    "space_repo_taken",
    "space_probe_failed",
    "space_not_found",
    "space_base_required",
    "branch_slug_taken",
    "branch_root_readonly",
    "branch_exists",
    "branch_not_found",
    "branch_changed",
    "publish_required",
    "nothing_to_version",
    "version_taken",
    "version_not_found",
    "repo_access_lost",
    "git_failed",
    "tag_id_taken",
    "tag_not_found",
    "tag_rule_invalid",
    "tag_rule_not_found",
    "classification_rule_not_found",
    "tag_import_invalid",
    "certification_tag_taken",
    "certification_not_found",
    "scheme_id_taken",
    "scheme_not_found",
    "scheme_import_invalid",
    "level_not_found",
    "level_name_taken",
    "level_short_name_taken",
    "profile_name_taken",
    "profile_not_found",
    "profile_admin_protected",
    "profile_in_use",
    "profile_admin_default",
    "profile_default_protected",
    "user_not_found",
    "admin_lockout",
    "profile_in_mapping",
    "mapping_duplicate",
    "mapping_not_found",
    "team_not_found",
    "team_name_taken",
    "db_connection_duplicate",
    "db_connection_slug_taken",
    "db_connection_not_found",
    "db_connection_secret_invalid",
    "db_host_unreachable",
    "db_auth_failed",
    "db_not_found",
    "db_role_not_found",
    "db_warehouse_not_found",
    "db_key_rejected",
    "db_driver_unavailable",
    "source_slug_taken",
    "source_not_found",
    "collect_branch_behind",
    "collect_failed",
    "contract_not_openapi",
    "contract_yaml_invalid",
    "contract_swagger_unsupported",
    "contract_ref_missing",
    "contract_too_large",
    "contract_version_invalid",
    "contract_version_taken",
    "contract_not_found",
    "plugin_not_found",
    "plugin_param_not_found",
    "parseur_unknown",
    "parseur_conflict",
    "asset_origin_missing",
    "curation_path_invalid",
    "raw_explorer_mode_invalid",
    "raw_explorer_folder_not_found",
    "raw_explorer_object_not_found",
    "asset_not_retained",
    # Classification d'un asset refusée (ticket 5e8680d7).
    "classification_unknown_level",
    "classification_unknown_scheme",
    "classification_not_single",
    # Domaines (tickets a7baaaa3, 36d35a88).
    "domain_not_found",
    "domain_name_invalid",
    "domain_name_taken",
    "domain_dataset_not_found",
    "domain_invalid",
    "dataset_name_taken",
    "relationship_cross_document",
    "relationship_columns_mismatch",
    "review_branch_behind",
    "review_stale",
    "draft_path_invalid",
    "draft_object_too_large",
    "draft_object_not_found",
    "merge_unresolved",
    "impact_not_found",
    "warning_not_found",
    "validation_error_not_acceptable",
    "merge_not_needed",
    "merge_pointer_not_found",
    "publish_in_progress",
    "publish_not_ready",
    "branch_changed_conflict",
    "publish_credential_rejected",
    "api_key_not_found",
    "api_key_revoked",
    "agent_profile_not_found",
    "agent_profile_beyond_rights",
    "oauth_request_not_found",
    "agent_profile_not_usable",
    "agent_connection_not_found",
    "agent_connection_revoked",
    "agent_profile_name_taken",
    "agent_profile_slug_taken",
    "agent_profile_builtin_protected",
    "agent_profile_in_use",
)


class ApiError(Exception):
    """Erreur applicative portée par un code stable et ses paramètres.

    Le `code` est l'un de ERROR_CODES ; `args` fournit les valeurs
    d'interpolation que l'interface insérera dans le message traduit.
    """

    def __init__(self, code: str, *, status_code: int = 400, **args: object) -> None:
        if code not in ERROR_CODES:
            msg = f"Code d'erreur inconnu : {code}"
            raise ValueError(msg)
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.args_dict = args


async def api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(
        {"error": exc.code, "error_args": exc.args_dict}, status_code=exc.status_code
    )
