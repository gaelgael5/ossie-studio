from __future__ import annotations

from ossie_studio.forges.azure import AzureDevOpsStrategy
from ossie_studio.forges.base import FORGES, ForgeFormSpec, ForgeStrategy
from ossie_studio.forges.github import GitHubStrategy
from ossie_studio.forges.gitlab import GitLabStrategy

_STRATEGIES: dict[str, ForgeStrategy] = {
    "github": GitHubStrategy(),
    "gitlab": GitLabStrategy(),
    "azure_devops": AzureDevOpsStrategy(),
}


def list_form_specs() -> list[ForgeFormSpec]:
    """Descriptions de formulaire des forges enregistrées, pour construire le
    menu « Ajouter une forge » et les popups dédiées. Ajouter une forge au
    registre l'ajoute au menu, sans toucher aux écrans."""
    return [strategy.form_spec() for strategy in _STRATEGIES.values()]


def get_strategy(forge: str) -> ForgeStrategy:
    """Rend la stratégie d'une forge. Ajouter une forge = ajouter une entrée
    ici et une implémentation, sans toucher aux écrans ni aux consommateurs."""
    strategy = _STRATEGIES.get(forge)
    if strategy is None:
        msg = f"Forge inconnue : {forge}"
        raise ValueError(msg)
    return strategy


def is_known_forge(forge: str) -> bool:
    return forge in FORGES
