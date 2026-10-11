"""LOT P1 — dire au lead ce que son découpage va COÛTER.
LOT A6 (2026-10-08) — et le lui dire JUSTE, maintenant que le pool existe.

Garde-fou posé par l'utilisateur (2026-08-14) : « il peut coder seul ou
travailler seul, ça oui — mais si c'est nécessaire, Lumena doit SAVOIR si elle a
besoin ou pas de workers ».

--- Ce que ce module disait, et qui est devenu FAUX le 2026-10-08 ---

Jusqu'au lot A4, `SubAgentOrchestrator` portait UN seul `CodeAgent` dont `execute()`
était sérialisé : les workers raisonnaient en parallèle mais codaient chacun leur tour.

Mesure d'origine, conservée parce qu'elle fonde la constante ci-dessous :

    38 exécutions CodeAgent tracées · moyenne 403 s · la plus longue 1 365 s
    HuffPack v1 : 288 + 273 + 818 = 1 379 s EN FILE = 23 min pour 4 fichiers

**A4 a supprimé cette file** (pool d'instances, chacune gardant son verrou). Et dans le
run réel du 07/10 à 23:29:02, Lumena a lu de ce module :

    « le CodeAgent est unique et sérialisé — ils coderont chacun leur tour,
      soit environ 27 min de file d'attente ici »

**27 minutes annoncées. Zéro file réelle. Mission bouclée en 5 minutes.** Laisser ce
texte dissuaderait de déléguer alors que déléguer est devenu bien moins cher — le plan
du 02/09 l'avait anticipé : « le lot 2 invalide `delegation_cost.py` ».

--- Le fait qui remplace l'ancien ---

Le goulot n'est plus le CodeAgent, c'est le **créneau LLM** :

    parallélisme réel = min(workers, pool CodeAgent, créneaux LLM du provider)

Avec `LUMENA_PROVIDER_CONCURRENCY=2`, lancer 10 workers de code produit 8 attentes.
Et par la règle « un seul modèle par mission » (2026-10-07), tous les workers tapent
sur le MÊME provider : ce plafond-là ne se contourne pas en variant les métiers.

--- Ce module n'IMPOSE toujours aucun découpage ---

Décider reste le travail du lead. Sur les 79 contrats du disque, **40 % créent plus de
workers que le livrable n'a de familles de fichiers** — on le lui signale, on ne le lui
interdit pas.

Module auto-contenu (stdlib) → testable sans runtime. Il **reçoit** les plafonds, il ne
les lit jamais : c'est l'appelant qui les résout.
"""
from __future__ import annotations

from typing import Any, Optional

__all__ = ["CODEAGENT_MEAN_SECONDS", "TOKENS_PAR_WORKER", "delegation_cost_note"]

# Moyenne mesurée sur 38 exécutions CodeAgent réelles (data/logs, 2026-08-14).
# Conservée : elle reste le meilleur ordre de grandeur d'UN passage de codage.
CODEAGENT_MEAN_SECONDS: int = 403

# Un worker = un prompt complet (préambule mission + contrat + spec + outils).
# Mesure §1.10 du plan du 02/09. Jamais chiffré au lead avant ce lot.
TOKENS_PAR_WORKER: int = 16_000

# Part des workers qui ne touchent JAMAIS le CodeAgent (mesure du 02/09 :
# 305 sur 463). Ceux-là ne paient pas le créneau de codage.
_PART_SANS_CODE = "66 %"


def _entier(valeur: Any, defaut: int = 0) -> int:
    """Une note d'aide ne doit JAMAIS faire tomber une mission."""
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return defaut


def delegation_cost_note(
    owner_count: int,
    file_count: int = 0,
    *,
    parallelisme_reel: Optional[int] = None,
    plafond_llm: Optional[int] = None,
    pool: Optional[int] = None,
) -> str:
    """Constat factuel sur le coût du découpage — "" si rien d'utile à dire.

    Args:
        owner_count: nombre de workers que le contrat crée.
        file_count: nombre de fichiers du livrable (pour le signal de couplage).
        parallelisme_reel: `min(workers, pool, créneaux LLM)`, résolu par l'appelant.
            `None` → la note reste utilisable, sans la partie parallélisme.
        plafond_llm, pool: pour NOMMER le goulot. Savoir qu'on est bridé ne suffit
            pas : sans le nom, le lead ne peut pas le desserrer.

    Silencieux à 0 ou 1 worker : il n'y a alors rien à arbitrer, et une note de plus
    ne ferait que diluer les autres avertissements (AUD-017).
    """
    owners = _entier(owner_count)
    if owners < 2:
        return ""

    lignes = [f"⏱️ **Ce que ce découpage coûte** : ton contrat crée {owners} workers."]

    para = _entier(parallelisme_reel, 0)
    if para > 0:
        para = min(para, owners)
        vagues = -(-owners // para)          # division entière par excès
        if vagues > 1:
            detail = ""
            _llm, _pool = _entier(plafond_llm), _entier(pool)
            if _llm > 0:
                # Nommer le goulot : c'est ce qui rend le fait ACTIONNABLE.
                detail = f" — bridé par les créneaux LLM ({_llm})"
                if _pool > 0:
                    detail += f", le pool CodeAgent en offre {_pool}"
            lignes.append(
                f"⚙️ **Parallélisme réel : {para}** sur {owners}{detail}. "
                f"Ils coderont en **{vagues} vagues** d'environ "
                f"{max(1, round(CODEAGENT_MEAN_SECONDS / 60))} min."
            )
        else:
            lignes.append(
                f"✅ **Parallélisme réel : {para}** — tes {owners} workers tiennent "
                "en une seule vague, aucun n'attendra son tour."
            )

    cout_k = round(owners * TOKENS_PAR_WORKER / 1000)
    lignes.append(
        f"💰 Chaque worker reçoit un prompt complet (~{TOKENS_PAR_WORKER // 1000} k "
        f"tokens) : ~**{cout_k} k tokens** pour ce fan-out."
    )

    files = _entier(file_count)
    if files and owners >= files:
        lignes.append(
            f"⚠️ Tu as {owners} workers pour {files} fichier(s) — un worker par "
            "fichier. Des fichiers COUPLÉS (un module et son test, un format et "
            "son parseur) gagnent à être confiés au **même** owner : un seul "
            "passage, et personne à qui négocier l'interface."
        )

    if para > 0 and owners > para:
        lignes.append(
            f"ℹ️ Mesuré : **{_PART_SANS_CODE} des workers ne codent pas** "
            "(recherche, lecture, rédaction). Ceux-là ne paient PAS ce créneau — "
            "un découpage non-code parallélise vraiment."
        )

    return "\n".join(lignes)
