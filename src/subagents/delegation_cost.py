"""LOT P1 — dire au lead ce que son découpage va COÛTER.

Garde-fou posé par l'utilisateur (2026-08-14) : « il peut coder seul ou
travailler seul, ça oui — mais si c'est nécessaire, Lumena doit SAVOIR si elle a
besoin ou pas de workers ».

Aujourd'hui elle ne sait pas, parce qu'un fait lui manque : **déléguer ne
parallélise pas le codage**. `SubAgentOrchestrator` est un singleton portant UN
seul `CodeAgent`, dont `execute()` est sérialisé (LOT 2.12.A). Les workers
raisonnent en parallèle, mais dès qu'ils codent, ils font la queue.

Mesuré sur les runs réels :

    38 exécutions CodeAgent tracées · moyenne 403 s · la plus longue 1 365 s
    HuffPack v1 : 288 + 273 + 818 = 1 379 s EN FILE = 23 min pour 4 fichiers

Et sur les 79 contrats du disque : **40 % créent plus de workers que le livrable
n'a de familles de fichiers** — jusqu'à « 5 fichiers, 5 workers, 1 seule famille ».
Chaque worker en trop est un passage supplémentaire au même goulot.

Ce module n'IMPOSE aucun découpage : décider reste le travail du lead, et sur
HuffPack v2 travailler seule était le bon choix (un codec et ses tests sont
couplés par le format binaire). Il lui donne seulement le chiffre qu'il ignore,
au moment exact où il peut encore changer d'avis.

Module auto-contenu (stdlib) → testable sans runtime.
"""
from __future__ import annotations

__all__ = ["CODEAGENT_MEAN_SECONDS", "delegation_cost_note"]

# Moyenne mesurée sur 38 exécutions CodeAgent réelles (data/logs, 2026-08-14).
CODEAGENT_MEAN_SECONDS: int = 403


def delegation_cost_note(owner_count: int, file_count: int = 0) -> str:
    """Constat factuel sur le coût du découpage — "" si rien d'utile à dire.

    Silencieux à 0 ou 1 worker : il n'y a alors aucune file d'attente, et une
    note de plus ne ferait que diluer les autres avertissements (AUD-017).
    """
    try:
        owners = int(owner_count)
    except (TypeError, ValueError):
        return ""
    if owners < 2:
        return ""
    minutes = max(1, round(owners * CODEAGENT_MEAN_SECONDS / 60))
    note = (
        f"⏱️ **Ce que ce découpage coûte** : ton contrat crée {owners} workers. "
        "Ils réfléchissent en parallèle, mais le CodeAgent est unique et "
        "sérialisé — ils coderont **chacun leur tour**. Mesuré sur les runs "
        f"réels : ~{CODEAGENT_MEAN_SECONDS} s par passage, soit environ "
        f"{minutes} min de file d'attente ici."
    )
    try:
        files = int(file_count)
    except (TypeError, ValueError):
        files = 0
    if files and owners >= files:
        note += (
            f"\n⚠️ Tu as {owners} workers pour {files} fichier(s) — un worker par "
            "fichier. Des fichiers COUPLÉS (un module et son test, un format et "
            "son parseur) gagnent à être confiés au **même** owner : un seul "
            "passage, et personne à qui négocier l'interface."
        )
    return note
