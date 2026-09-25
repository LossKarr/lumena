"""LOT 10 — MESURER L'ATTENTE AU PLAFOND PROVIDER, SANS LA SURESTIMER.

═══════════════════════════════════════════════════════════════════════════════
  LE PLAFOND QUI BRIDE LE PLUS EST CELUI QU'ON NE VOIT PAS
═══════════════════════════════════════════════════════════════════════════════

`LUMENA_PROVIDER_CONCURRENCY` (défaut 2) borne les appels LLM simultanés vers un
même fournisseur. Le sémaphore est **global au processus** et n'a qu'un seul
point d'acquisition (`multi_provider._chat_provider_result`) : le chat, le
heartbeat, le lead d'une mission, chacun de ses workers et chacun de leurs
CodeAgents le partagent.

Mesuré sur ce dépôt au 03/09 : cette clé apparaît **une seule fois** dans tout
`src/` + `web/` — sa lecture depuis l'environnement. Elle est absente du panel
de configuration, où ses trois frères figurent pourtant (`MISSION_CONCURRENCY`,
`MISSION_WORKER_CONCURRENCY`, `MISSION_MAX_DEPTH`), et son attente n'émet aucune
trace. Un utilisateur qui monte « workers en parallèle » de 2 à 6 n'obtient donc
aucune accélération sur des workers du même métier — et rien ne lui dit pourquoi.

═══════════════════════════════════════════════════════════════════════════════
  POURQUOI L'UNION DES INTERVALLES, ET NON LA SOMME
═══════════════════════════════════════════════════════════════════════════════

Deux workers bloqués 30 s **en même temps**, c'est 30 s perdues pour la mission,
pas 60. Une somme cumulée ferait dire au constat le double de la vérité — et un
avertissement qui exagère est un avertissement qu'on apprend à ignorer
(AUD-017).

On compte donc le temps pendant lequel **au moins un** appel est en file :

    0 → 1 appel bloqué   on note l'instant de départ
    1 → 0 appel bloqué   on ajoute l'intervalle écoulé

`attente_s` est ainsi toujours ≤ la durée réelle du run, et se compare
honnêtement à elle.

═══════════════════════════════════════════════════════════════════════════════
  PORTÉE : UN COMPTEUR PAR MISSION, JAMAIS UN COMPTEUR GLOBAL
═══════════════════════════════════════════════════════════════════════════════

Le compteur vit dans un `ContextVar` ouvert par le runner au démarrage de la
mission. Les tâches asyncio créées ensuite (lead, workers, CodeAgents) héritent
d'une copie du contexte pointant vers **le même dictionnaire** : leurs attentes
s'y accumulent, et le runner relit l'objet qu'il a lui-même créé.

Les appels du chat, eux, voient `None` et ne comptent rien — une mission ne peut
donc pas se voir attribuer l'attente d'une autre, ni celle du chat.
"""

from __future__ import annotations

import contextvars
import time
from typing import Any, Dict, Optional

_COMPTEUR: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "lumena_attente_provider", default=None
)


def ouvrir_compteur() -> Dict[str, Any]:
    """Ouvre un compteur pour le contexte courant et RETOURNE l'objet.

    L'appelant garde la référence : il n'a pas à relire le `ContextVar` ensuite,
    ce qui le rend insensible à un éventuel rebind dans une tâche fille.
    """
    compteur: Dict[str, Any] = {
        "attente_s": 0.0,       # union des intervalles, en secondes
        "appels_bloques": 0,    # combien d'appels ont trouvé porte close
        "_en_attente": 0,       # combien attendent À CET INSTANT
        "_depuis": 0.0,
    }
    _COMPTEUR.set(compteur)
    return compteur


def compteur_courant() -> Optional[Dict[str, Any]]:
    return _COMPTEUR.get()


def fermer_compteur() -> None:
    """Referme la mesure. Ne remet jamais les valeurs à zéro : le runner lit son
    objet après coup."""
    _COMPTEUR.set(None)


def entrer_en_attente() -> None:
    """Un appel vient de trouver le sémaphore plein."""
    compteur = _COMPTEUR.get()
    if compteur is None:
        return
    if compteur["_en_attente"] == 0:
        compteur["_depuis"] = time.perf_counter()
    compteur["_en_attente"] += 1
    compteur["appels_bloques"] += 1


def sortir_d_attente() -> None:
    """Cet appel a obtenu son créneau. On ne ferme l'intervalle que lorsque le
    DERNIER attendant est servi."""
    compteur = _COMPTEUR.get()
    if compteur is None or compteur["_en_attente"] <= 0:
        return
    compteur["_en_attente"] -= 1
    if compteur["_en_attente"] == 0:
        compteur["attente_s"] += max(0.0, time.perf_counter() - compteur["_depuis"])


def plafond_provider() -> int:
    """Valeur courante de `LUMENA_PROVIDER_CONCURRENCY` (défaut 2).

    Lue ici plutôt qu'importée de `multi_provider` : le constat doit pouvoir
    nommer le réglage sans faire remonter le client LLM dans le runner.
    """
    import os

    try:
        return max(1, int(os.getenv("LUMENA_PROVIDER_CONCURRENCY", "2")))
    except Exception:
        return 2
