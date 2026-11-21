from __future__ import annotations

from dataclasses import dataclass, field

from email_validator import EmailNotValidError, validate_email

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


@dataclass
class CredentialsForm:
    email: str
    password: str
    # Codes de champ (ex. "email_invalid"), traduits par l'interface.
    errors: list[str] = field(default_factory=list)


def parse_credentials(email: str, password: str) -> CredentialsForm:
    """Valide l'email et le mot de passe AVANT tout usage ; renvoie des codes."""
    errors: list[str] = []
    email = email.strip()
    if not email:
        errors.append("email_required")
    else:
        try:
            email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError:
            errors.append("email_invalid")
    if not password:
        errors.append("password_required")
    elif len(password) < MIN_PASSWORD_LENGTH:
        errors.append("password_too_short")
    elif len(password) > MAX_PASSWORD_LENGTH:
        errors.append("password_too_long")
    return CredentialsForm(email=email, password=password, errors=errors)
