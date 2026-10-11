"""PHASE PRESENCE (2026-10-09) — LA COUTURE UNIQUE ENTRE CE QUI EST VRAI ET CE QU'ELLE LIT.

===============================================================================
  UNE SEULE LIGNE DANS `react.py`, POUR TOUTE LA PHASE.
===============================================================================

Trois gels plafonnent `src/reasoning/react.py` a **9 718 lignes** (CONN-4d, nomme
`only_shrink` ; CONN-5a ; CONN-7a). Ils ont raison : ce fichier fait deja 9 718 lignes,
et la ligne rouge du depot interdit de le refactoriser.

Ce module est donc le POINT DE PASSAGE de toute la phase. `react.py` appelle
`bloc_presence(self)` sur une ligne qu'il avait DEJA — l'assemblage final de son
contexte d'identite — et les lots suivants (S4 capacites, S3 deltas) s'ajoutent ICI,
sans jamais rouvrir `react.py`.

--- Le budget, et pourquoi il est dur ---

Le prompt mesure du 2026-10-09 faisait **5 295 tokens, dont 67 % de conversation** :
trois heartbeats transcrits en entier, neuf alertes disque perimees. Pour une demande
qui etait « genere moi un site web ». On remplace du bavardage perime par de l'etat
frais — **on n'empile pas**. Plafond de la phase : 500 caracteres, verifie ici.

--- Les regles communes a tous les blocs ---

1. **Un FAIT, jamais une consigne.** Le bloc Emotion dit « Humeur=neutral », il ne dit
   pas « sois joyeuse ». Aucun bloc de presence ne donne d'ordre de comportement.
2. **Rien a dire -> silence.** Pas de ligne vide, pas de « 0 tache », pas de « inconnu ».
3. **Jamais bloquant.** Une donnee d'affichage ne fait JAMAIS echouer un tour.
4. **Jamais d'invention.** Une mesure trop vieille dit son age, ou se tait.
"""
from __future__ import annotations

from typing import Any, List

from .activite_courante import bloc_activite
from .etat_systeme import bloc_etat_systeme

__all__ = ["bloc_presence", "BUDGET_CARACTERES"]

# Ligne rouge du plan PRESENCE : 500 car. (~125 tokens) pour TOUS les blocs reunis.
BUDGET_CARACTERES = 500


def _scope_presence(boucle: Any) -> tuple[str | None, str | None]:
    """Résout le périmètre de la boucle sans jamais élargir vers toutes les tâches."""
    orchestrateur = getattr(boucle, "task_orchestrator", None)
    task_id = str(getattr(boucle, "task_id", None) or "").strip()
    if orchestrateur is not None and task_id:
        try:
            tache = orchestrateur.get_task(task_id)
        except Exception:
            tache = None
        if isinstance(tache, dict):
            meta = tache.get("metadata") if isinstance(tache.get("metadata"), dict) else {}
            requester = meta.get("requester") if isinstance(meta.get("requester"), dict) else {}
            conversation_id = str(tache.get("conversation_id") or meta.get("source_conversation_id") or "").strip()
            owner_user_id = str(meta.get("owner_user_id") or requester.get("owner_user_id") or "").strip()
            if conversation_id:
                return conversation_id, owner_user_id or None

    try:
        from src.runtime.context import get_current_runtime_context

        runtime = get_current_runtime_context()
    except Exception:
        runtime = None
    conversation_id = str(getattr(runtime, "conversation_id", None) or "").strip()
    owner_user_id = str(getattr(runtime, "owner_user_id", None) or "").strip()
    return conversation_id or None, owner_user_id or None


def bloc_presence(boucle: Any = None) -> str:
    """Rend tous les blocs de presence, ou "" s'il n'y a rien de sur a dire.

    `boucle` est la boucle ReAct appelante. On n'y lit que ce qui existe, par `getattr` :
    certains tests l'instancient sans `__init__`, et un bloc d'affichage ne doit pas
    etre la raison d'un `AttributeError` (lecon du lot A4).

    Ne leve jamais.
    """
    blocs: List[str] = []
    conversation_id, owner_user_id = _scope_presence(boucle)
    orchestrateur = getattr(boucle, "task_orchestrator", None)
    for rendu in (
        lambda: bloc_etat_systeme(),
        lambda: bloc_activite(
            orchestrateur,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
        ),
    ):
        try:
            texte = rendu()
        except Exception:
            continue
        if isinstance(texte, str) and texte.strip():
            blocs.append(texte.strip())

    if not blocs:
        return ""
    assemble = "\n".join(blocs)
    # Le budget est une ligne rouge, pas une intention : on coupe au bloc entier plutot
    # que de livrer un fait tronque au milieu d'un chiffre.
    while len(assemble) > BUDGET_CARACTERES and len(blocs) > 1:
        blocs.pop()
        assemble = "\n".join(blocs)
    return assemble[:BUDGET_CARACTERES]
