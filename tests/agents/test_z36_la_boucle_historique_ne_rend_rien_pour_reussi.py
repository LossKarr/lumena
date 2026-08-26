"""LOT Z36 — la boucle historique aussi rendait « reussi » sans rien ecrire.

Le lot Z35 (2026-08-23) a ferme ce defaut sur la route Codex :

    if not changed:            # aucun fichier ecrit
        return CodexCodeAgentResult(success=False, status_code="no_change", ...)

Mais Z35 ne gardait QUE le rail Codex autonome. Depuis que ce rail est retire
et que l'abonnement passe par la boucle CodeAgent HISTORIQUE, la garantie
disparaissait — parce que la boucle historique n'a jamais eu l'equivalent.

Le trou, dans la porte `done` :

    _edited_now = list(getattr(self, "_edited_files", []))
    if _edited_now:                       # <- zero fichier => porte SAUTEE
        ... run_gate(...) ...
    ...
    return AgentResult(success=True, ...)

Zero fichier ecrit ne declenche donc AUCUNE validation, et `done` passe
directement en succes. C'est le meme motif que les 53 lots : le fait existe
(`_edited_files` est vide), il est meme calcule trois lignes plus haut, et il
n'est jamais consulte pour la decision.

Ce lot ne demande PAS que tout travail ecrive un fichier — une tache d'analyse
ou de lecture n'en produit legitimement aucun. Il demande que « je n'ai rien
ecrit » ne puisse pas etre annonce comme « projet cree ».
"""

import pytest

from src.agents.sub_agent import _creation_task_wrote_nothing


# ══════════════════════════════════════════════════════════════════════════
#  Le cas mesure : une CREATION qui ne produit rien
# ══════════════════════════════════════════════════════════════════════════


def test_une_creation_sans_aucun_fichier_est_refusee():
    """LE lot. Demander un projet et n'ecrire aucun fichier n'est pas un succes."""
    assert _creation_task_wrote_nothing(
        intent="create",
        edited_files=[],
        description="Cree un SaaS complet multi-pages : frontend, backend, API PHP",
    ) is True


def test_un_seul_fichier_reste_accepte_par_CE_garde():
    """Z36 ne ferme que le ZERO. Le « un fichier sur vingt » est un autre
    defaut, ouvert et documente — le confondre ici rendrait ce garde trop
    large et bloquerait des creations legitimes."""
    assert _creation_task_wrote_nothing(
        intent="create",
        edited_files=["database/schema.sql"],
        description="Cree un SaaS complet",
    ) is False


# ══════════════════════════════════════════════════════════════════════════
#  Ce que le garde ne doit PAS attraper
# ══════════════════════════════════════════════════════════════════════════


def test_une_analyse_sans_fichier_reste_legitime():
    """Lire, chercher, expliquer : aucun fichier attendu. Un garde qui exige
    une ecriture pour tout casserait la moitie des usages du CodeAgent."""
    assert _creation_task_wrote_nothing(
        intent="analyze",
        edited_files=[],
        description="Analyse l'architecture de ce projet et explique-moi les risques",
    ) is False


def test_une_modification_sans_fichier_n_est_pas_du_ressort_de_ce_garde():
    """Une modification qui n'ecrit rien est suspecte, mais elle a ses propres
    signaux (diff vide, tests inchanges). Z36 vise la CREATION, ou l'absence de
    fichier est sans ambiguite."""
    assert _creation_task_wrote_nothing(
        intent="modify",
        edited_files=[],
        description="Corrige le bug de pagination",
    ) is False


def test_une_description_vide_ne_declenche_rien():
    """Sans demande explicite de creation, on ne presume rien."""
    assert _creation_task_wrote_nothing(
        intent="create", edited_files=[], description=""
    ) is False


# ══════════════════════════════════════════════════════════════════════════
#  Le garde prouve qu'il REFUSE — pas seulement qu'il accepte
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "description",
    [
        "Cree un site vitrine avec 4 pages",
        "Genere une API REST complete en Python",
        "Construis-moi une application de gestion",
        "Fais un jeu 3D dans le navigateur",
    ],
)
def test_toute_demande_de_creation_sans_fichier_est_refusee(description):
    assert _creation_task_wrote_nothing(
        intent="create", edited_files=[], description=description
    ) is True


@pytest.mark.parametrize(
    "description",
    [
        "Explique-moi ce que fait ce module",
        "Cherche les usages de cette fonction",
        "Resume l'etat des tests",
    ],
)
def test_aucune_demande_de_lecture_n_est_bloquee(description):
    assert _creation_task_wrote_nothing(
        intent="create", edited_files=[], description=description
    ) is False


# ══════════════════════════════════════════════════════════════════════════
#  Le message rendu doit etre actionnable
# ══════════════════════════════════════════════════════════════════════════


def test_le_refus_dit_quoi_faire_ensuite():
    """Un echec sans issue fait tourner l'agent en rond — c'est ce qui avait
    mene le run du SaaS a se rabattre sur `create_html` puis sur deux scripts
    PowerShell jetables."""
    from src.agents.sub_agent import _CREATION_WROTE_NOTHING_MESSAGE

    assert "AUCUN" in _CREATION_WROTE_NOTHING_MESSAGE
    assert "write_file" in _CREATION_WROTE_NOTHING_MESSAGE
