"""CU-1 — Couche de gouvernance Computer Use.

Computer Use pilote la VRAIE machine (souris/clavier/apps). Contrairement au
reste (MCP sandboxé, browser dans un onglet), une action CU peut être
destructrice. Cette couche apporte les mêmes freins que le MCP :

  - kill-switch global    : LUMENA_CU_DISABLED=1        -> bloque tout
  - mode observe-only     : LUMENA_CU_OBSERVE_ONLY=1    -> bloque toute action mutante
  - blocklist destructrice: commande dangereuse tapée   -> bloquée
  - haut risque           : classé "approve" (l'agent peut demander via ask_user)

Par défaut (aucun flag, texte bénin) : tout est ALLOW -> comportement inchangé.
Module PUR (aucune dépendance machine) -> testable hors bureau.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from loguru import logger


class CUBlockedError(RuntimeError):
    """Action Computer Use refusée par la couche de sécurité."""


# Actions qui MODIFIENT l'état de la machine (vs lecture : screenshot/list/find)
MUTATING_ACTIONS = frozenset({
    "click", "double_click", "right_click", "type_text", "press_key", "hotkey",
    "open_application", "open_app", "close_window", "close_app", "drag", "paste",
    "scroll", "move_mouse", "mouse_pattern", "ui_click", "ui_type",
})

# Actions à HAUT RISQUE : autorisées mais devraient passer par une approbation
HIGH_RISK_ACTIONS = frozenset({"close_app", "close_window"})

# Commandes destructrices tapées au clavier / collées (Windows + Unix).
_DANGEROUS_TEXT = re.compile(
    r"""(
        \brm\s+-[a-z]*[rf][a-z]*\s+/                # rm -rf /, rm -fr /...
      | \brm\s+-[a-z]*[rf][a-z]*\s+~                # rm -rf ~
      | \bdel\s+/[a-z]\b                            # del /f /s /q
      | \brmdir\s+/s\b | \brd\s+/s\b                # rmdir /s
      | \bformat\s+[a-z]:                           # format C:
      | \bmkfs\b | \bdiskpart\b                     # formatage disque
      | \bdd\s+if=\S+\s+of=/dev/                    # dd vers un disque
      | \bshutdown\b | \brestart-computer\b         # arrêt/redémarrage
      | \breg\s+delete\b                            # suppression registre
      | \bremove-item\b[^\n]*-recurse[^\n]*-force   # PowerShell rm -rf
      | \bget-childitem\b[^\n]*remove-item          # pipe destructeur PS
      | :\(\)\s*\{\s*:\|:&\s*\};:                   # fork bomb
      | \b>\s*/dev/sd[a-z]                           # écrasement disque
    )""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class Decision:
    level: str            # "allow" | "approve" | "block"
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.level == "allow"


def _flag(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in ("1", "true", "yes", "on")


def classify(action: str, params: dict | None = None) -> Decision:
    """Classe une action CU : allow / approve / block. Pur, sans effet de bord."""
    params = params or {}
    a = (action or "").strip().lower()

    # 1) Kill-switch global
    if _flag("LUMENA_CU_DISABLED"):
        return Decision("block", "Computer Use désactivé (LUMENA_CU_DISABLED=1)")

    # 2) Observe-only : aucune action mutante
    if _flag("LUMENA_CU_OBSERVE_ONLY") and a in MUTATING_ACTIONS:
        return Decision("block", f"Mode observe-only actif — action '{a}' refusée")

    # 3) Blocklist destructrice sur le texte tapé/collé
    if a in ("type_text", "paste", "ui_type"):
        text = str(params.get("text", "") or "")
        if _DANGEROUS_TEXT.search(text):
            return Decision("block", "Commande destructrice détectée dans le texte à saisir")

    # 4) Haut risque -> approbation (non bloquant par défaut)
    if a in HIGH_RISK_ACTIONS:
        return Decision("approve", f"Action à haut risque : '{a}'")

    return Decision("allow")


def require_approval(action: str) -> bool:
    """True si l'action à haut risque doit passer par une approbation humaine.
    OPT-IN : actif uniquement si LUMENA_CU_REQUIRE_APPROVAL=1 (par défaut OFF →
    autonomie totale)."""
    return _flag("LUMENA_CU_REQUIRE_APPROVAL") and (action or "").strip().lower() in HIGH_RISK_ACTIONS


def enforce(action: str, **params) -> Decision:
    """À appeler avant d'exécuter une action mutante. Lève CUBlockedError si
    l'action est BLOQUÉE (kill-switch, observe-only, commande destructrice).
    Une action 'approve' n'est PAS bloquée ici (l'approbation se gère côté
    agent via ask_user) — on se contente de la tracer."""
    d = classify(action, params)
    if d.level == "block":
        logger.warning("🛡️ [CU-safety] BLOQUÉ '{}': {}", action, d.reason)
        raise CUBlockedError(d.reason)
    if d.level == "approve":
        logger.info("🛡️ [CU-safety] action à haut risque '{}': {}", action, d.reason)
    return d
