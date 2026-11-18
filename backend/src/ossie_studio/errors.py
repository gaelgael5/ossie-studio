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
    "connection_url_unreachable",
    "connection_azure_org_not_found",
    "connection_duplicate",
    "connection_not_found",
    "connection_in_use",
    "connection_secret_invalid",
    "space_name_taken",
    "space_repo_taken",
    "space_probe_failed",
    "space_not_found",
    "space_base_required",
    "branch_slug_taken",
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
