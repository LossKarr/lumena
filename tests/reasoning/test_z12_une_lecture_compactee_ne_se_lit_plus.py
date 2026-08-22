"""LOT Z12 — la même notion écrite deux fois finit par diverger.

Run « Rustine » (2026-08-16). Le lead avait fait tout le travail d'analyse : Z7
lui avait nommé la page (« `index.html` n'a que 0/13 de ses classes stylées »),
il avait enquêté et trouvé la cause EXACTE — « le CSS utilise des classes
françaises (`.bouton`, `.carte`) mais les HTML des classes anglaises (`.btn`,
`.card`) ». Il savait quoi corriger.

Il n'a jamais pu. Ses `read_files_batch` rendaient 8437 et 8669 caractères —
juste au-dessus du seuil de 8000 — et il en recevait **830** : le `<head>` et le
pied de page, jamais le corps où vivent les classes. Il a relu, revu 830 chars,
relu autrement, revu 830 chars… dix itérations, puis mort par PLAN GUARD
anti-stagnation. Livrable final : `index.html` 0 %, `suivi.html` 5 %.

CAUSE : deux listes disaient « ceci est une lecture de fichier », et elles
avaient divergé.

    seuil de déclenchement : read_file, read_files_batch, search_in_code,
                             grep_search, find_files, parallel_tools,
                             write_mission_contract          → 8000
    stratégie de compaction: read_file, search_in_code,
                             grep_search, find_files         → garde 3000

Trois outils protégés jusqu'à 8000… puis réduits à 800 au lieu de 3000. Le seuil
élevé leur donnait une fausse sécurité : plus le contenu était gros, plus la
perte était totale. `grep_batch` n'était dans aucune des deux.

Deux lots précédents (B0.3 run PlantCare, C0.1 run FrigoZen) avaient corrigé le
seuil sans voir que la stratégie divergeait — **troisième occurrence du même
défaut**. Le problème n'était pas la valeur, c'était la duplication.

MESURE : les 11 compactions de tout le corpus portent EXACTEMENT sur les trois
outils mal classés (read_files_batch 6×, write_mission_contract 3×, grep_batch
2×) et ZÉRO sur ceux qui figuraient dans les deux listes.
"""

import re
from pathlib import Path

import pytest

from src.reasoning.react import _OBS_FILE_READ_TOOLS

_SRC = Path("src/reasoning/react.py").read_text(encoding="utf-8")


# ── Les outils qui ont réellement souffert ───────────────────────────────────


@pytest.mark.parametrize(
    "outil,compactions_observees",
    [("read_files_batch", 6), ("write_mission_contract", 3), ("grep_batch", 2)],
)
def test_les_outils_massacres_du_corpus_sont_couverts(outil, compactions_observees):
    """Ce sont les seuls du corpus à avoir été compactés — et ils perdaient tout."""
    assert outil in _OBS_FILE_READ_TOOLS


@pytest.mark.parametrize(
    "outil", ["read_file", "search_in_code", "grep_search", "find_files"]
)
def test_les_outils_deja_proteges_le_restent(outil):
    """Zéro compaction observée sur eux : Z12 ne doit rien leur retirer."""
    assert outil in _OBS_FILE_READ_TOOLS


def test_parallel_tools_est_couvert():
    """Ajouté au seuil par B0.3 mais jamais à la stratégie — il portait le même
    défaut, silencieusement."""
    assert "parallel_tools" in _OBS_FILE_READ_TOOLS


@pytest.mark.parametrize(
    "outil",
    ["delegate_task", "run_command", "browser_get_content", "delegate_and_wait",
     "create_project", "execute_code"],
)
def test_les_familles_qui_ont_leur_propre_strategie_restent_dehors(outil):
    """Chacune a sa compaction dédiée (600+200, 400+400, payload navigateur,
    20000 pour les livrables de workers). Les absorber casserait ces réglages."""
    assert outil not in _OBS_FILE_READ_TOOLS


# ── La source unique : ce qui rend la divergence impossible ──────────────────


def test_le_seuil_utilise_la_constante():
    i = _SRC.index("_OBS_COMPACT_LIMIT = 8000")
    assert "_OBS_FILE_READ_TOOLS" in _SRC[i - 700 : i]


def test_la_strategie_utilise_la_meme_constante():
    """Le cœur du lot : c'est ICI que la divergence vivait."""
    i = _SRC.index("Lectures fichiers : seuil élevé atteint")
    assert "_OBS_FILE_READ_TOOLS" in _SRC[i - 300 : i]


def test_plus_aucune_liste_de_lecture_ecrite_en_dur():
    """La liste littérale des 4 outils est ce qui avait divergé — elle ne doit
    plus exister nulle part dans le fichier."""
    assert '"read_file", "search_in_code", "grep_search", "find_files",' not in _SRC


def test_la_constante_est_definie_une_seule_fois():
    assert len(re.findall(r"^_OBS_FILE_READ_TOOLS", _SRC, re.M)) == 1


def test_les_deux_sites_sont_bien_deux_sites_distincts():
    """Un seul usage signifierait qu'un des deux branchements a été perdu."""
    usages = [
        m.start() for m in re.finditer(r"in _OBS_FILE_READ_TOOLS", _SRC)
    ]
    assert len(usages) == 2
    seuil = _SRC.index("_OBS_COMPACT_LIMIT = 8000")
    strategie = _SRC.index("Lectures fichiers : seuil élevé atteint")
    assert any(u < seuil for u in usages)
    assert any(seuil < u < strategie for u in usages)


# ── La règle que le lot institue ─────────────────────────────────────────────


def test_un_outil_protege_par_le_seuil_est_protege_par_la_strategie():
    """LA règle. Avant Z12 elle était fausse pour 3 outils sur 7 : ils étaient
    tolérés jusqu'à 8000 chars puis réduits à 800.

    Une seule constante sert aux deux endroits — l'assertion est donc vraie par
    construction, et ce test existe pour que ça le reste."""
    i = _SRC.index("_OBS_COMPACT_LIMIT = 8000")
    bloc_seuil = _SRC[i - 700 : i]
    j = _SRC.index("Lectures fichiers : seuil élevé atteint")
    bloc_strategie = _SRC[j - 300 : j]
    assert "_OBS_FILE_READ_TOOLS" in bloc_seuil
    assert "_OBS_FILE_READ_TOOLS" in bloc_strategie


def test_la_strategie_garde_bien_3000_chars():
    """800 chars sur un HTML, c'est le `<head>` et le footer — jamais le corps."""
    i = _SRC.index("Lectures fichiers : seuil élevé atteint")
    assert "content[:3000]" in _SRC[i : i + 900]


def test_le_message_invite_a_relire_par_plage():
    """Tronquer sans dire comment récupérer la suite, c'est ce qui a fait boucler
    le lead : il relisait le fichier entier au lieu d'une plage."""
    i = _SRC.index("Lectures fichiers : seuil élevé atteint")
    assert "plage de lignes" in _SRC[i : i + 900]


# ── Ce que Z12 ne change pas ─────────────────────────────────────────────────


def test_le_seuil_des_livrables_de_workers_reste_intact():
    """20000 pour `delegate_and_wait` : les livrables doivent rester entiers pour
    que le lead fusionne sans re-fouiller le disque."""
    assert "_OBS_COMPACT_LIMIT = 20000" in _SRC


def test_les_seuils_navigateur_restent_intacts():
    assert "_OBS_COMPACT_LIMIT = 4000 if _is_chat_surface else 1800" in _SRC


def test_le_seuil_par_defaut_reste_intact():
    assert "_OBS_COMPACT_LIMIT = 3000" in _SRC


def test_les_strategies_des_autres_familles_restent_intactes():
    for signature in ("content[:600]", "content[-200:]", "content[:400]", "content[-400:]"):
        assert signature in _SRC


def test_la_raison_du_lot_est_ecrite_dans_le_code():
    """Trois lots ont buté sur cette duplication. Sans la trace, un quatrième
    recommencera."""
    i = _SRC.index("_OBS_FILE_READ_TOOLS: frozenset")
    entete = _SRC[i - 1800 : i]
    assert "B0.3" in entete and "C0.1" in entete
    assert "Rustine" in entete
