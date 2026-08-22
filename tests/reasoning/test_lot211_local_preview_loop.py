"""LOT 2.11.C/D — anti-boucle preview LOCALE (run memo, 2026-07-08).

Sur une preview servie par Lumena en loopback, l'inspection visuelle répétée
(screenshot/dom_state) ne progresse pas ET ne comptait dans aucun stop → boucle
infinie. Politique BORNÉE : escalade UNE fois vers `browser_evaluate`, puis
conclusion HONNÊTE (jamais « jeu validé » sans preuve).
"""

from __future__ import annotations

from src.reasoning.react import (
    _local_preview_loop_decision,
    _url_is_local_preview,
)
from src.utils import local_preview


# ─────────────────────────── décision pure C/D ─────────────────────────────
def test_cd_not_local_preview_is_inert():
    # Hors preview locale : jamais d'action, streak remis à zéro.
    assert _local_preview_loop_decision(False, "browser_screenshot", False, 7, True) == (
        "none",
        0,
        False,
    )


def test_cd_progress_resets():
    assert _local_preview_loop_decision(True, "browser_screenshot", True, 4, True) == (
        "none",
        0,
        False,
    )


def test_cd_non_visual_non_evaluate_tool_neutral():
    # Un clic (vraie action) est compté par browser_no_progress_streak, pas ici.
    action, streak, asked = _local_preview_loop_decision(
        True, "browser_click_index", False, 2, False
    )
    assert action == "none"
    assert streak == 2  # inchangé
    assert asked is False


def test_cd_escalates_once_at_warn_threshold():
    # 2 inspections passées → la 3e (warn_at=3) déclenche l'escalade, UNE fois.
    action, streak, asked = _local_preview_loop_decision(
        True, "browser_screenshot", False, 2, False
    )
    assert action == "escalate"
    assert streak == 3
    assert asked is True


def test_cd_no_double_escalate():
    # Escalade déjà demandée, inspection visuelle encore → pas de 2e escalade.
    action, streak, asked = _local_preview_loop_decision(
        True, "browser_dom_state", False, 3, True
    )
    assert action == "none"
    assert streak == 4
    assert asked is True


def test_cd_evaluate_without_proof_after_ask_stops():
    # On a demandé l'évaluation ; browser_evaluate revient sans progrès → stop honnête.
    action, _streak, asked = _local_preview_loop_decision(
        True, "browser_evaluate", False, 3, True
    )
    assert action == "stop"
    assert asked is True


def test_cd_stop_at_streak_after_ask():
    # stop_at=5 atteint alors que l'escalade a déjà été demandée → stop.
    action, streak, _asked = _local_preview_loop_decision(
        True, "browser_screenshot", False, 4, True
    )
    assert action == "stop"
    assert streak == 5


def test_cd_evaluate_with_proof_resets_no_stop():
    # browser_evaluate qui PROUVE (progressed=True) → succès, pas de stop.
    action, streak, asked = _local_preview_loop_decision(
        True, "browser_evaluate", True, 4, True
    )
    assert action == "none"
    assert streak == 0
    assert asked is False


# ───────────────────────── détection preview locale ────────────────────────
def test_url_local_preview_registered_loopback():
    local_preview.clear_previews()
    try:
        local_preview.register_preview(8137, workspace="memo")
        assert _url_is_local_preview("http://127.0.0.1:8137/") is True
        assert _url_is_local_preview("http://localhost:8137/index.html") is True
    finally:
        local_preview.clear_previews()


def test_url_unregistered_port_is_not_preview():
    local_preview.clear_previews()
    assert _url_is_local_preview("http://127.0.0.1:9999/") is False


def test_url_lan_and_external_never_preview():
    local_preview.clear_previews()
    try:
        local_preview.register_preview(8137, workspace="memo")
        # Même port, mais host LAN/externe → jamais une preview.
        assert _url_is_local_preview("http://192.168.1.20:8137/") is False
        assert _url_is_local_preview("https://example.com:8137/") is False
    finally:
        local_preview.clear_previews()


def test_url_empty_is_false():
    assert _url_is_local_preview("") is False
    assert _url_is_local_preview(None) is False


# ── LOT R′ : le garde a coupé une mission qui AVAIT sa preuve ────────────────

def test_the_exact_cadran_sequence_is_not_cut():
    """Run Cadran (2026-08-14) — la mission a prouvé son tri, puis a été coupée.

        23:55:47  clic « Auteur » → L'Étranger/Camus devient Fahrenheit/Bradbury
        23:55:56  les 8 lignes relues dans le nouvel ordre
        23:56:03  browser_evaluate SANS `script` (appel mal formé)
        23:56:10  browser_evaluate (test du thème) → STOP

    Conclusion à 7 min 19 sur 60, thème persistant / responsive / clavier jamais
    vérifiés. La preuve existait : `local_preview_interaction_proven` était posé —
    mais APRÈS cette décision, et sans lui être transmis.
    """
    action, streak, asked = _local_preview_loop_decision(
        True, "browser_evaluate", False, 2, True,
        interaction_proven=True, tool_succeeded=True,
    )
    assert action == "none", "une preuve démontrée ne doit JAMAIS couper"
    assert streak == 0, "et elle remet le compteur à zéro"


def test_a_malformed_evaluate_does_not_burn_the_attempt():
    """23:56:03 — `browser_evaluate` sans paramètre `script`. Un typo ne doit pas
    coûter la tentative que l'escalade vient de réclamer."""
    action, streak, asked = _local_preview_loop_decision(
        True, "browser_evaluate", False, 3, True,
        interaction_proven=False, tool_succeeded=False,
    )
    assert action == "none"
    assert streak == 3, "le compteur ne bouge pas"
    assert asked is True, "la tentative demandée reste due"


def test_a_successful_but_empty_evaluate_still_stops():
    """Le cas memo : on a réclamé l'état JS, l'appel aboutit, il ne démontre rien.
    Sans cette branche, la boucle infinie d'origine revient."""
    action, _, _ = _local_preview_loop_decision(
        True, "browser_evaluate", False, 3, True,
        interaction_proven=False, tool_succeeded=True,
    )
    assert action == "stop"


def test_repeated_blind_screenshots_still_escalate_then_stop():
    """Le comportement historique, intact."""
    action, streak, asked = _local_preview_loop_decision(
        True, "browser_screenshot", False, 2, False,
        interaction_proven=False, tool_succeeded=True,
    )
    assert (action, streak, asked) == ("escalate", 3, True)
    action, _, _ = _local_preview_loop_decision(
        True, "browser_screenshot", False, 4, True,
        interaction_proven=False, tool_succeeded=True,
    )
    assert action == "stop"


def test_a_proof_already_persisted_protects_later_iterations():
    """Une fois l'interactif démontré, ce garde n'a plus rien à dire — même sur
    des inspections visuelles répétées."""
    for outil in ("browser_screenshot", "browser_dom_state", "browser_evaluate"):
        action, streak, _ = _local_preview_loop_decision(
            True, outil, False, 4, True,
            interaction_proven=True, tool_succeeded=True,
        )
        assert action == "none", outil
        assert streak == 0, outil


def test_the_caller_computes_the_proof_before_deciding():
    """Le défaut n'était pas la fonction mais l'ORDRE : la preuve était
    enregistrée après l'appel. Elle doit être calculée avant."""
    import inspect as _inspect

    from src.reasoning import react

    src = _inspect.getsource(react)
    # L'APPEL (pas la définition) — il commence par l'affectation du triplet.
    appel = src.split("_cd_action, _cd_streak, _cd_asked =")[1][:700]
    assert "interaction_proven=_cd_proven" in appel
    assert "tool_succeeded=" in appel
    # …et la preuve est calculée en amont de cet appel, pas après.
    amont = src.split("_cd_action, _cd_streak, _cd_asked =")[0]
    assert "_cd_proven = bool(" in amont, "la preuve doit être calculée AVANT"
