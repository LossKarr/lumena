"""LOT ESC-1b/1c — choisir une escalade utilisable, ou ne pas escalader.

Cette politique vivait en ligne dans `SubAgentOrchestrator.execute_task`. Elle
retenait un candidat dès que `check_api_key` trouvait une variable
d'environnement — sans jamais demander si le compte avait du crédit, ni
consulter le disjoncteur `provider_health` qui existe pourtant.

Run réel du 21 septembre 2026 : escalade vers `gpt-5.4-mini` sur un compte
OpenAI vide, trois refus `insufficient_quota`, secours Codex épuisé, puis chute
sur `nvidia-gpt-oss-20b` — **plus faible** que le `deepseek-flash` qui venait de
travailler six minutes.

Deux règles en découlent :

- un palier entièrement à sec ne fait pas renoncer : les autres paliers sont
  essayés avant d'abandonner ;
- sans aucun candidat, on **garde le modèle d'origine**. Ne pas escalader vaut
  mieux que descendre.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from loguru import logger

# Paliers d'escalade, du moins au plus coûteux. Ordre historique conservé.
ESCALATION_CHAIN: List[List[str]] = [
    ["gpt-5.4-mini", "gpt-5.4"],
    ["claude-sonnet-4.6", "claude-opus-4.6"],
]


def _disponible_par_defaut(modele: str) -> bool:
    """Utilisable veut dire : connu, clé présente, et crédit non épuisé."""
    try:
        from .providers import AVAILABLE_MODELS, check_api_key
        from .provider_quota import quota_epuise
    except Exception:
        return False
    config = AVAILABLE_MODELS.get(modele)
    if config is None:
        return False
    if quota_epuise(getattr(config, "provider", None)):
        return False
    try:
        return bool(check_api_key(config.provider))
    except Exception:
        return False


def choisir_escalade(
    attempt: int,
    *,
    disponible: Optional[Callable[[str], bool]] = None,
) -> Optional[str]:
    """Le modèle vers lequel escalader à cette tentative, ou None.

    `disponible` est nommé et injectable : les tests jugent sans toucher au
    réseau ni à l'environnement.
    """
    if attempt is None or attempt <= 0:
        return None
    juge = disponible or _disponible_par_defaut
    depart = min(attempt - 1, len(ESCALATION_CHAIN) - 1)

    # Le palier visé d'abord, puis les autres : un fournisseur a sec ne doit pas
    # faire renoncer a toute escalade s'il en reste un utilisable ailleurs.
    ordre = [depart] + [i for i in range(len(ESCALATION_CHAIN)) if i != depart]
    for index in ordre:
        for candidat in ESCALATION_CHAIN[index]:
            try:
                if juge(candidat):
                    return candidat
            except Exception as exc:
                logger.debug("[ESC-1] candidat {} ecarte : {}", candidat, type(exc).__name__)
    return None


def modele_apres_escalade(*, origine: Optional[str], escalade: Optional[str]) -> Optional[str]:
    """Faute de candidat, le modèle qui produisait du travail est conservé."""
    return escalade or origine
