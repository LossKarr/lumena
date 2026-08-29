"""LOT O2 — une mission demandée est une mission lancée.

Run HuffPack v2 (2026-08-14). Message de l'utilisateur : « Mission avec échéance
90 minutes. Le codec HuffPack existe déjà… améliore-le ». Résultat :

    7 × read_file      5 × list_directory      2 × parallel_tools
    0 × create_mission     0 × écriture
    12 itérations, uniquement de la lecture

Mesuré sur les messages réels commençant par « Mission … » :

    5 → mission créée    « Construis NoteFlow / MemoNest / CaveÀVin / HuffPack »
    2 → AUCUNE mission   « Le codec existe déjà… améliore-le »

Le point commun des cinq qui marchent est un verbe de CRÉATION : le modèle pense
« projet multi-fichiers → contrat », appelle `write_mission_contract`, se fait
répondre « au chat, utilise create_mission » — et c'est ce refus qui le
redirige. Avec un verbe de MODIFICATION il part lire, et plus rien ne le reprend.
**La mission se créait selon le verbe de la tâche, jamais selon l'annonce de
l'utilisateur.**

CONCEPTION, arrêtée avec l'utilisateur : le mot « mission » NE SUFFIT PAS — on
peut dire « ta mission c'est de m'aider » en attendant une réponse immédiate, et
forcer l'arrière-plan serait le défaut symétrique (il perd le fil). Le signal
fiable est l'ÉCHÉANCE CHIFFRÉE : personne n'attend 30 à 120 minutes devant son
écran. Mesuré : 9 des 10 messages contenant « mission » portent une échéance
(30 à 120 min), et **aucun n'écrit « en arrière-plan »** — exiger ce mot aurait
été inutile.
"""
from __future__ import annotations

import pytest

from src.reasoning.final_guards import chat_requests_background_mission


# ── LES messages réels qui doivent déclencher ───────────────────────────────

@pytest.mark.parametrize(
    "message",
    [
        "Mission avec échéance 120 minutes.\n\nConstruis « CaveÀVin », un SaaS local.",
        "Mission avec échéance 90 minutes.\n\nLe codec « HuffPack » existe déjà.",
        "Mission avec échéance 50 minutes.\n\nÉtablis un comparatif factuel.",
        "Enregistre une mission avec échéance 30 minutes : Construis SuiviDépenses",
        "Enregistre une mission autonome avec une échéance de 50 minutes.",
        "Enregistre une mission avec une échéance de 90 minutes. Construis…",
    ],
)
def test_the_real_messages_are_recognised(message):
    assert chat_requests_background_mission(message) is True


def test_an_explicit_background_request_needs_no_deadline():
    for message in (
        "lance ça en arrière-plan stp",
        "fais-le en arriere-plan pendant que je dors",
        "mets ça en tâche de fond",
    ):
        assert chat_requests_background_mission(message) is True, message


@pytest.mark.parametrize(
    "unit", ["30 minutes", "90 min", "2 heures", "1 h", "1 jour"]
)
def test_every_deadline_unit_counts(unit):
    assert chat_requests_background_mission(f"Mission avec échéance {unit}. Fais X.")


def test_the_deadline_may_precede_the_word():
    assert chat_requests_background_mission(
        "Je te donne une mission : 45 minutes d'échéance, construis un CSV."
    ) is True


# ── LE garde-fou demandé : « mission » ne suffit JAMAIS ─────────────────────

def test_the_word_mission_alone_never_triggers():
    """L'utilisateur peut dire « mission » sans vouloir l'arrière-plan. Forcer
    le différé lui ferait perdre le fil — défaut symétrique de celui qu'on
    corrige."""
    for message in (
        "ta mission c'est de m'aider",
        "ok ta mission : lis ce fichier et dis-moi ce que tu en penses",
        "j'ai une mission pour toi",
        "mission accomplie bravo",
    ):
        assert chat_requests_background_mission(message) is False, message


@pytest.mark.parametrize(
    "question",
    [
        "alors la dernière mission",
        "alors la derniere mission ?",
        "tu as quoi comme mission en cours ?",
        "où en est la mission ?",
        "ça avance ?",
        "quelle mission tourne là ?",
        "liste les missions",
        "résultat de la mission ?",
        "statut de la mission avec échéance 90 minutes ?",
    ],
)
def test_asking_about_a_mission_never_launches_one(question):
    """Sans ça, chaque « alors, la dernière mission ? » en relancerait une —
    y compris quand la question cite une échéance."""
    assert chat_requests_background_mission(question) is False


def test_an_objective_that_merely_mentions_the_previous_mission():
    """LOT O2-b (run HuffPack v3, 2026-08-14) — LE faux positif.

    L'objectif contenait « dans l'état où la DERNIÈRE MISSION les a laissés » :
    une référence factuelle, prise pour une demande de statut. Le garde s'est
    tu, aucune mission n'a été créée, le travail est parti en chat — et P2b
    étant inerte hors mission, `write_file` a de nouveau visé le livrable.

    Le vocabulaire confond les deux cas ; la longueur les sépare : la seule
    demande de statut de l'historique fait 29 caractères, cet objectif 1 047.
    """
    message = (
        "Mission avec échéance 90 minutes.\n\n"
        "Le codec « HuffPack » existe déjà dans workspace/huffpack/.\n"
        "Son benchmark et ses tests sont dans l'état où la dernière mission\n"
        "les a laissés — commence par mesurer cet état réel.\n\n"
        "Ta mission n'est PAS de le réécrire : c'est de l'AMÉLIORER.\n"
        "Méthode : 3 workers maximum ; relance pytest toi-même ; publie."
    )
    assert len(message) > 120
    assert chat_requests_background_mission(message) is True


def test_a_long_objective_citing_a_status_word_still_launches():
    """Généralisation : « statut », « où en est », « ça avance » peuvent tous
    apparaître dans un objectif long sans en faire une question."""
    for word in ("statut", "où en est le projet", "ça avance bien", "missions en cours"):
        message = (
            f"Mission avec échéance 60 minutes.\n\nAnalyse le {word} du dépôt et "
            "produis un rapport chiffré. " + "Détaille chaque point mesuré. " * 4
        )
        assert len(message) > 120
        assert chat_requests_background_mission(message) is True, word


def test_an_ordinary_request_is_untouched():
    for message in (
        "fais-moi un site web complet",
        "ouvre le fichier stp",
        "génère un PDF de facture",
        "",
        "   ",
    ):
        assert chat_requests_background_mission(message) is False, message


def test_garbage_never_raises():
    for bad in (None, 42, "x" * 5000, "échéance"):
        assert isinstance(chat_requests_background_mission(bad), bool)


# ── le branchement : inerte DANS une mission, sinon cascade ─────────────────

def test_the_gate_is_wired_before_the_other_rails():
    """Il doit primer : le rail Document Studio a justement refusé run_command
    sur ce même message (LOT O1)."""
    import inspect

    from src.reasoning import react

    src = inspect.getsource(react)
    assert "_chat_mission_intent_gate" in src
    order = src.split("_cmi_obs is not None")[0]
    assert order.count("_chat_mission_intent_gate(action.tool_name)") == 1


def test_the_gate_is_inert_inside_a_mission():
    """Le lead et les workers reçoivent ce vocabulaire dans leur prompt injecté :
    les déclencher lancerait des missions en cascade."""
    import inspect

    from src.reasoning.react import ReActLoop

    # Lot RF-6b : la DECISION de ce gate a ete deplacee vers
    # `mission_runtime.py` ; `ReActLoop` ne garde que la mutation, le log
    # et la construction de l'Observation (invariants 5 et 16). Ce test lit
    # donc le source la ou il vit desormais — intention mot pour mot
    # inchangee. Preuve COMPORTEMENTALE adossee : matrice RF-6b, 51 valeurs
    # comparees (retours ET etat mute), 51 identiques.
    from src.reasoning.mission_runtime import (
        rf6b_decision_intention_mission_chat as _decision,
    )

    src = inspect.getsource(_decision)
    assert "if etat.est_run_mission_strict():" in src  # RF-6b : nom suivi du rebindage, intention inchangee (self.X -> etat.Y())
    # RF-6b : noms suivis du rebindage, ORDRE verifie a l'identique — le
    # garde mission passe toujours AVANT l'appel couteux.
    assert src.index("est_run_mission_strict") < src.index("chat_requests_background_mission")


def test_the_gate_redirects_once_and_does_not_block():
    import inspect

    from src.reasoning.react import ReActLoop

    # Lot RF-6b : la DECISION de ce gate a ete deplacee vers
    # `mission_runtime.py` ; `ReActLoop` ne garde que la mutation, le log
    # et la construction de l'Observation (invariants 5 et 16). Ce test lit
    # donc le source la ou il vit desormais — intention mot pour mot
    # inchangee. Preuve COMPORTEMENTALE adossee : matrice RF-6b, 51 valeurs
    # comparees (retours ET etat mute), 51 identiques.
    from src.reasoning.mission_runtime import (
        rf6b_decision_intention_mission_chat as _decision,
    )

    src = inspect.getsource(_decision)
    assert "_chat_mission_gate_shots" in src
    assert ">= 1" in src
    assert "create_mission" in src


def test_the_gate_never_fires_on_create_mission_itself():
    import inspect

    from src.reasoning.react import ReActLoop

    # Lot RF-6b : la DECISION de ce gate a ete deplacee vers
    # `mission_runtime.py` ; `ReActLoop` ne garde que la mutation, le log
    # et la construction de l'Observation (invariants 5 et 16). Ce test lit
    # donc le source la ou il vit desormais — intention mot pour mot
    # inchangee. Preuve COMPORTEMENTALE adossee : matrice RF-6b, 51 valeurs
    # comparees (retours ET etat mute), 51 identiques.
    from src.reasoning.mission_runtime import (
        rf6b_decision_intention_mission_chat as _decision,
    )

    src = inspect.getsource(_decision)
    assert 'if tool_name == "create_mission":' in src


# ── O2b : le mode est annoncé, et réversible ───────────────────────────────

def test_the_ack_says_how_to_come_back_to_direct_mode():
    """On ne peut pas garantir de deviner juste ; on peut rendre l'erreur
    gratuite."""
    from src.subagents.mission_ack import build_mission_ack

    ack = build_mission_ack("Construis un truc", "task_x", "90 minutes")
    assert "arrière-plan" in ack
    assert "direct" in ack.lower()
