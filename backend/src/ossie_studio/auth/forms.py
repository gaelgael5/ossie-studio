from __future__ import annotations

from dataclasses import dataclass, field

from email_validator import EmailNotValidError, validate_email

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


@dataclass
class CredentialsForm:
    email: str
    password: str
    errors: list[str] = field(default_factory=list)


def parse_credentials(email: str, password: str) -> CredentialsForm:
    """Valide l'email et le mot de passe AVANT tout usage ; messages en clair."""
    errors: list[str] = []
    email = email.strip()
    if not email:
        errors.append("L'adresse email est obligatoire.")
    else:
        try:
            email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError:
            errors.append("L'adresse email n'est pas valide.")
    if not password:
        errors.append("Le mot de passe est obligatoire.")
    elif len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Le mot de passe doit faire au moins {MIN_PASSWORD_LENGTH} caractères.")
    elif len(password) > MAX_PASSWORD_LENGTH:
        errors.append(f"Le mot de passe ne peut pas dépasser {MAX_PASSWORD_LENGTH} caractères.")
    return CredentialsForm(email=email, password=password, errors=errors)
