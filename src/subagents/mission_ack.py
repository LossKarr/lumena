"""LOT M3 — l'accusé de lancement d'une mission.

Demande utilisateur (2026-08-14, capture à l'appui) : « change ce message de
réponse de Lumena, la laisser libre et surtout dire ce qu'elle a fait et donner
au sous-agent ».

Le message était figé au caractère près :

    ✨ C'est lancé ! La mission tourne en arrière-plan (id : `task_…`).
    Tu peux continuer à me parler — demande-moi l'avancement quand tu veux.

Deux contraintes ont façonné la réponse :

1. **Il reste déterministe.** Repasser par le LLM pour formuler rouvrirait le
   « THOUGHT leaké comme réponse finale » (régime A) — encore visible trois fois
   dans le run du 14/08. La variété vient donc d'un choix de tournure indexé sur
   l'id de mission : stable, reproductible, testable — jamais d'un tirage au sort.
2. **Il ne peut pas parler des workers.** À l'instant de `create_mission`, le
   contrat n'est pas posé et aucun sous-agent n'existe. Annoncer « j'ai donné X à
   Y » serait une fabrication — exactement ce que tout ce chantier combat. Le
   message dit donc ce qui est VRAI maintenant : l'objectif retenu, l'échéance, et
   la façon dont le travail va être découpé.

Module auto-contenu (stdlib) → testable sans runtime.
"""
from __future__ import annotations

import hashlib
import re

__all__ = ["summarize_objective", "build_mission_ack"]


_OPENINGS: tuple = (
    "✨ C'est parti !",
    "✨ Je m'y mets.",
    "✨ C'est lancé !",
    "✨ Très bien, je prends.",
)

# « 120 minutes », « 2h », « demain 18h »… conservé tel quel : c'est la consigne
# de l'utilisateur, la reformuler risquerait de la déformer.
_WS_RE = re.compile(r"\s+")


def summarize_objective(objective: str, *, limit: int = 180) -> str:
    """Première phrase de l'objectif, compactée — "" si rien d'exploitable.

    On ne résume PAS au sens LLM : on coupe proprement. Un résumé inventé serait
    une reformulation non vérifiée de la consigne.
    """
    text = _WS_RE.sub(" ", str(objective or "")).strip()
    if not text:
        return ""
    for sep in (". ", " ; ", " : "):
        head, found, _ = text.partition(sep)
        if found and len(head) >= 25:
            text = head
            break
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—-")
        text = f"{cut}…"
    return text


def build_mission_ack(
    objective: str = "",
    mission_id: str = "",
    deadline: str = "",
    *,
    multi_worker: bool = False,
) -> str:
    """Accusé de lancement : ce qu'elle a retenu, et ce qu'elle va faire.

    `multi_worker` dit si le travail sera découpé entre sous-agents (le lead le
    décide ensuite, mais l'objectif le réclame souvent explicitement). Il ne
    change QUE la phrase de méthode — jamais une affirmation sur un résultat.
    """
    opening = _OPENINGS[0]
    key = str(mission_id or "")
    if key:
        digest = hashlib.sha256(key.encode("utf-8", errors="replace")).digest()
        opening = _OPENINGS[digest[0] % len(_OPENINGS)]

    lines = [opening]

    summary = summarize_objective(objective)
    if summary:
        lines.append(f"\n**Ce que j'ai retenu** : {summary}")

    if str(deadline or "").strip():
        lines.append(f"**Échéance** : {str(deadline).strip()}")

    if multi_worker:
        lines.append(
            "\n**Comment je m'y prends** : je pose d'abord le contrat "
            "(fichiers, propriétaire de chacun, signatures exactes), puis je "
            "confie à chaque sous-agent son périmètre — il ne peut écrire que "
            "ses fichiers. Je relance les tests moi-même avant de conclure."
        )
    else:
        lines.append(
            "\n**Comment je m'y prends** : je découpe le travail, je vérifie "
            "chaque étape par une exécution réelle, et je ne conclus que sur "
            "des preuves."
        )

    tail = "La mission tourne en arrière-plan"
    if mission_id:
        tail += f" (id : `{mission_id}`)"
    tail += (
        ". Tu peux continuer à me parler — demande-moi l'avancement quand tu "
        "veux, je te dirai où en sont les sous-agents."
    )
    lines.append(f"\n{tail}")
    # LOT O2b — le mode est DÉTECTÉ à partir de l'échéance ; le détecteur peut se
    # tromper sur un cas jamais observé. On ne peut pas garantir de deviner juste,
    # on peut rendre l'erreur gratuite : le mode est annoncé, et le demi-tour tient
    # en une phrase.
    lines.append(
        "_Si tu préférais que je m'en occupe tout de suite avec toi, dis-le-moi "
        "et je reprends en direct._"
    )

    return "\n".join(lines)
