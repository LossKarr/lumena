r"""Lot VOICE-5 - l'endpointing ne coupe plus la parole avant le verbe.

--- Ce que la comparaison des deux journaux a mesure ---

Charles : « avant je lui parlais elle repondait directement, la c'est lent ». La mesure a
montre que ce n'etait pas la latence de reponse, mais **qu'elle le coupait**.

    28/09 avant 18 h 35 (V2, backup)  : 11/13 phrases avec un VERBE D'ACTION (84 %) | 25 car
    29/09 a 01 h 30    (V3, actuel)   :  3/8  phrases avec un VERBE D'ACTION (37 %) | 18 car

Sept caracteres de moins en moyenne, et le verbe tombe. Sans verbe, elle ne peut pas
agir : elle demande de preciser. Consequence mesuree sur le nombre d'actions :

    V2 : 102 outils / 18 demandes = 5,7 par demande
    V3 :   7 outils /  8 demandes = 0,9 par demande

Huit tours pour deux resultats, et Charles repete six fois. **C'est cela, « c'est lent ».**

--- La cause, prouvee au code ---

    live.py            endpointing branche : BACKUP = 0 occurrence, ACTUEL = 2
    turn_manager.py    dec = decide_endpoint(final_transcript OR partial_transcript)
    turn_manager.py    "wait_ms": dec.min_wait_ms
    input_sources.py   _arm(turn_id, wait_ms) -> timer -> on tranche

V3 a branche l'endpointing dans le pipeline live. Avant, la capture s'arretait apres le
**hangover de 700 ms** ; desormais l'endpointing decide, et il descend a **180 ms**.

Le pire cas, mesure : le mot **« ouvre » SEUL** rend
`turn_complete / 180 ms / complete_request`, parce que `looks_command` teste le PREMIER
mot contre `_ACTION_VERBS`. Des que Charles prononce « ouvre... », la decision tombe et
180 ms plus tard on transcrit — il n'a pas le temps de dire « ...moi Discord ».

Et la decision se prend sur `partial_transcript` : juger qu'une phrase est finie en lisant
son debut est structurellement fragile.

--- Ce que ce lot fait, et ne fait pas ---

Il pose un **plancher** egal au hangover qui regnait dans la backup. L'endpointing garde
tout le reste : il peut toujours ALLONGER l'attente jusqu'a 2,8 s sur une hesitation, la
detection de question, les fillers, le multilingue de V3. Il ne peut simplement plus
couper PLUS TOT qu'avant.

Les etats rendus ne changent pas : ce lot touche des DELAIS, pas des decisions. Les tests
d'etat existants (`test_endpointing_silence_v2.py`, `test_turn_manager_v2.py`,
`test_voice_vad_endpointing_v3.py`) restent verts — verifie, et aucun ne figeait les
valeurs de delai.
"""
from __future__ import annotations

import pytest

from src.voice.v2.endpointing import decide_endpoint

# Le hangover qui regnait seul dans la backup, et qui laissait passer 84 % des verbes.
PLANCHER_MS = 700


# -- 1. Le cas exact du run -------------------------------------------------

def test_le_verbe_seul_ne_coupe_plus_a_180ms():
    """« ouvre » seul rendait 180 ms : Charles n'avait pas le temps de dire la suite."""
    d = decide_endpoint("ouvre")
    assert d.min_wait_ms >= PLANCHER_MS, (
        f"{d.min_wait_ms} ms : elle coupe encore avant la fin de la phrase"
    )


@pytest.mark.parametrize("debut", [
    "ouvre", "ouvre-moi", "ferme", "lance", "mets", "fais", "envoie", "montre",
])
def test_aucun_debut_de_commande_ne_coupe_trop_tot(debut):
    """`looks_command` teste le PREMIER mot : tous ces debuts declenchaient 180 ms."""
    assert decide_endpoint(debut).min_wait_ms >= PLANCHER_MS, debut


@pytest.mark.parametrize("phrase", [
    "ouvre-moi Discord",
    "ouvre-moi Discord et mets un petit message",
    "fais-moi un devis",
    "ferme le serveur",
    "lance ton ide",
])
def test_les_demandes_reelles_laissent_le_temps_de_finir(phrase):
    assert decide_endpoint(phrase).min_wait_ms >= PLANCHER_MS, phrase


def test_une_question_laisse_aussi_le_temps():
    """`looks_question` partage la meme branche que `looks_command`."""
    assert decide_endpoint("pourquoi tu fais ca ?").min_wait_ms >= PLANCHER_MS


# -- 2. Le plancher vaut pour TOUTES les branches ---------------------------

@pytest.mark.parametrize("texte,final,pause", [
    ("", False, 0),                                  # empty
    ("ouvre", False, 0),                             # complete_request
    ("il faut regarder le dossier", True, 900),      # final_with_pause
    ("oui", True, 0),                                # short_final
    ("je pensais que", False, 0),                    # suspended_or_continuation
])
def test_aucune_branche_A_UN_SEUL_ETAGE_ne_coupe_trop_tot(texte, final, pause):
    """Les branches qui TRANCHENT directement doivent respecter le plancher. Sans ce
    test, une seule branche oubliee reproduit le defaut."""
    d = decide_endpoint(texte, is_final=final, pause_ms=pause)
    assert d.min_wait_ms >= 500, f"{d.reason} : {d.min_wait_ms} ms"


def test_uncertain_est_VOLONTAIREMENT_hors_plancher():
    """Decision de conception, prise APRES une casse mesuree.

    J'avais d'abord planche `uncertain` aussi. Deux tests sont tombes :
    `test_timer_endpoint_uncertain_neutral_waits` et
    `test_uncertain_final_uses_two_stage_timer_without_double_send`. Ils protegent un
    timer A DEUX ETAGES : au premier tir on passe en `user_paused` sans agir, au second
    on tranche. Porter le plancher a 700 ms rendait vraie la condition
    `pause_ms >= adaptive_pause` de la seconde evaluation, qui basculait en
    `turn_complete` — **le second etage disparaissait**.

    `uncertain` offre donc deja deux fenetres a l'utilisateur : il n'a pas besoin du
    plancher. Le defaut du run venait de `complete_request`, qui tranche en UN seul
    etage a 180 ms.
    """
    d = decide_endpoint("un truc quelconque ici")
    assert d.state == "uncertain"
    assert d.min_wait_ms <= 450, (
        "uncertain a ete planche : verifier que le timer a deux etages survit "
        "(cf. test_timer_endpoint_uncertain_neutral_waits)"
    )


def test_la_reponse_courte_reste_la_plus_reactive():
    """Nuance assumee : « oui »/« ok » repondant a une question de Lumena n'a pas besoin
    d'attendre autant qu'une commande. Elle reste la branche la plus rapide."""
    courte = decide_endpoint("oui", is_final=True).min_wait_ms
    commande = decide_endpoint("ouvre").min_wait_ms
    assert courte <= commande


# -- 3. Ce que le lot ne doit PAS changer -----------------------------------

@pytest.mark.parametrize("texte,final,pause,attendu", [
    ("", False, 0, "uncertain"),
    ("ouvre", False, 0, "turn_complete"),
    ("pourquoi tu fais ca ?", False, 0, "turn_complete"),
    ("je pensais que", False, 0, "continue_expected"),
    ("envoie le message et", False, 0, "continue_expected"),
    ("oui", True, 0, "turn_complete"),
    ("un truc quelconque ici", False, 0, "uncertain"),
])
def test_les_ETATS_rendus_sont_INCHANGES(texte, final, pause, attendu):
    """Caracterisation : ce lot touche des DELAIS, jamais des decisions."""
    assert decide_endpoint(texte, is_final=final, pause_ms=pause).state == attendu


def test_l_hesitation_peut_toujours_ALLONGER_l_attente():
    """L'apport de V3 est conserve : un mot de liaison fait patienter bien au-dela du
    plancher, jusqu'a 2,8 s."""
    d = decide_endpoint("envoie le message et")
    assert d.state == "continue_expected"
    assert d.max_wait_ms >= 2800


def test_le_rythme_appris_reste_honore():
    """`user_avg_pause_ms` module toujours l'attente vers le HAUT."""
    lent = decide_endpoint("je pensais que", user_avg_pause_ms=1500)
    rapide = decide_endpoint("je pensais que", user_avg_pause_ms=400)
    assert lent.min_wait_ms >= rapide.min_wait_ms


def test_le_plafond_reste_superieur_au_plancher():
    """Garde de coherence : un `max_wait_ms` inferieur au `min_wait_ms` rendrait le
    timer absurde."""
    for texte, final in (("ouvre", False), ("oui", True), ("je pensais que", False),
                         ("un truc quelconque ici", False), ("", False)):
        d = decide_endpoint(texte, is_final=final)
        assert d.max_wait_ms >= d.min_wait_ms, f"{d.reason}: {d}"
