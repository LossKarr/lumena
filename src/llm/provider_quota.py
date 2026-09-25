"""LOT ESC-1a — registre de session des fournisseurs sans crédit.

Une clé API valide ne prouve pas qu'il reste du crédit. `check_api_key` ne lit
qu'une variable d'environnement : un compte à sec y passe pour disponible, et
chaque tentative repart vers lui.

Ce désarmement existait déjà, mais pour **Z.AI seulement**
(`_zai_balance_exhausted`, `multi_provider.py`). Ce module le généralise à tout
fournisseur, sans rien persister : un rechargement de compte ne doit pas exiger
d'éditer un fichier, un redémarrage suffit.

Le registre dit « ce fournisseur a répondu qu'il n'a plus de crédit », jamais
« ce fournisseur est en panne » — cette seconde question appartient au
disjoncteur `provider_health` de `MultiProviderLLM`, qui reste seul juge des
échecs transitoires.
"""
from __future__ import annotations

import re
from threading import Lock
from typing import Dict, Optional

from loguru import logger

_verrou = Lock()
_epuises: Dict[str, str] = {}


def _nom(provider) -> str:
    """Accepte un `ProviderType`, une chaîne, ou tout objet portant `.value`."""
    brut = getattr(provider, "value", provider)
    return str(brut or "").strip().lower()


# Signatures relevees dans les reponses reelles des fournisseurs (logs de
# production des 17 et 21 septembre 2026). Volontairement ETROITES : une
# limitation de debit arrive elle aussi en 429, mais elle est transitoire et ne
# doit jamais desarmer un fournisseur sain.
_SIGNATURES_QUOTA = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "no credits remaining",
    "credit balance is too low",
    "exceeded_current_quota",
    "insufficient balance",
    '"code":"1113"',
)


# LOT PROV-1 (25/09/2026) — les codes HTTP qui disent « n'insiste pas ».
#
# Mesure sur les logs des DEUX machines : xAI 403 (7 fois sur A, 2 sur B) et NVIDIA NIM
# 410 (7 et 2) — 18 tentatives perdues sur des fournisseurs definitivement refuses, parce
# que rien ne les desarme.
#
# Pourquoi le CODE et non le corps : `error_msg = str(e)` (`multi_provider.py` l.1393)
# ne contient PAS le corps de la reponse — mesure : `str(HTTPStatusError)` rend
# « Client error '403 Forbidden' for url … », le corps reste dans `e.response.text`, lu
# et jete par le `logger.error` de chaque provider. Le code, lui, EST dans `str(e)`.
#
# Le 429 est VOLONTAIREMENT absent : voir `_SIGNATURES_QUOTA` ci-dessus — une limitation
# de debit arrive aussi en 429 et elle est transitoire. Un 401 peut etre une cle a
# renouveler, un 400/422 une requete malformee de NOTRE cote, un 404 un modele mal nomme :
# aucun ne prouve que le fournisseur est mort.
#
# Le contexte HTTP est EXIGE : « erreur a la ligne 403 du fichier » ne desarme rien.
_CODES_DEFINITIFS_RE = re.compile(
    r"(?:HTTP\s+|error\s+')(403|410)\b",
    re.IGNORECASE,
)


def ressemble_a_un_acces_definitivement_refuse(message) -> bool:
    """Vrai pour un 403 (permission refusee) ou un 410 (Gone), et rien d'autre."""
    texte = str(message or "")
    if not texte:
        return False
    return bool(_CODES_DEFINITIFS_RE.search(texte))


def ressemble_a_un_quota_epuise(corps) -> bool:
    """Vrai seulement si le fournisseur dit explicitement manquer de credit."""
    texte = str(corps or "").lower()
    if not texte:
        return False
    return any(signature in texte for signature in _SIGNATURES_QUOTA)


def marquer_quota_epuise(provider, raison: str = "") -> None:
    """Désarme un fournisseur pour la session en cours.

    Idempotent : seul le premier marquage est journalisé, pour qu'une cascade de
    tentatives ne noie pas les traces.
    """
    nom = _nom(provider)
    if not nom:
        return
    with _verrou:
        deja_connu = nom in _epuises
        _epuises[nom] = str(raison or "")[:200]
    if not deja_connu:
        logger.warning(
            "Fournisseur {} desarme pour cette session : credit ou quota epuise ({})",
            nom, str(raison or "")[:120],
        )


def quota_epuise(provider) -> bool:
    nom = _nom(provider)
    if not nom:
        return False
    with _verrou:
        return nom in _epuises


def raison_quota(provider) -> Optional[str]:
    """La raison rendue par le fournisseur, pour l'expliquer à l'utilisateur."""
    nom = _nom(provider)
    with _verrou:
        return _epuises.get(nom)


def fournisseurs_epuises() -> tuple:
    with _verrou:
        return tuple(sorted(_epuises))


def reinitialiser() -> None:
    """Vide le registre. Réservé aux tests et à un rechargement explicite."""
    with _verrou:
        _epuises.clear()
