"""M1 (run RévizIA 2026-07-05) — jambe navigateur : truth-lock DUR + voie preview.

Le run RévizIA a livré le 1er mensonge depuis FidéliBar : « 🔬 Test navigateur
validé sur http://127.0.0.1:8085 » + détails fabriqués, alors qu'AUCUN browser_*
n'avait réussi (flask bloqué par la whitelist, SSRF sur le reste — le serveur n'a
jamais tourné). Deux causes : (1) la forme NOMINALE « test navigateur validé »
échappait à la regex ; (2) le verrou était claim-based — courir après les
formulations est une guerre perdue.

M1.a  : policy DURE — livrable web + zéro browser_* réussi → bannière déterministe
        sur TOUT final, indépendamment du texte (param `web_deliverable`).
M1.a' : scope top-lead via `_truth_lock_web_flag` (un sous-worker isolé ne peut
        pas vérifier au navigateur — la vérité incombe au lead).
M1.b  : le refus sanitizer d'un serveur web guide vers start_preview_server
        (guidance, JAMAIS de redirection silencieuse — revue M1).
"""
from __future__ import annotations

import src.reasoning.final_guards as fg
from src.utils.command_sanitizer import sanitize_command

_HARD = "Navigateur NON vérifié"        # bannière policy dure (7)
_CLAIM = "Vérification navigateur NON prouvée"  # bannière claim (3)


# ── M1.a : policy dure (indépendante du texte) ─────────────────────────────────────

def test_web_without_proof_banners_even_innocent_text():
    """Le cœur de M1 : texte SANS aucun claim navigateur → bannière quand même."""
    out, info = fg.apply_mission_truth_lock(
        "Mission terminée : fichiers produits et publiés proprement.",
        has_green_test=True, has_browser_proof=False, web_deliverable=True)
    assert info["changed"] is True
    assert info["browser_unverified_note"] is True
    assert _HARD in out
    assert out.rstrip().endswith("proprement.")  # additif : texte préservé


def test_web_with_proof_untouched():
    out, info = fg.apply_mission_truth_lock(
        "Mission terminée, navigateur vérifié.",
        has_green_test=True, has_browser_proof=True, web_deliverable=True)
    assert info["changed"] is False


def test_default_false_preserves_existing_callers():
    """NON-RÉGRESSION cœur : sans web_deliverable, comportement strictement identique
    (une mission non-web sans browser_* ne reçoit RIEN)."""
    out, info = fg.apply_mission_truth_lock(
        "Rapport rédigé et sauvegardé.",
        has_green_test=True, has_browser_proof=False)
    assert info["changed"] is False


def test_idempotent_no_double_banner():
    once, _ = fg.apply_mission_truth_lock(
        "Livrable prêt.", has_green_test=True,
        has_browser_proof=False, web_deliverable=True)
    twice, info2 = fg.apply_mission_truth_lock(
        once, has_green_test=True,
        has_browser_proof=False, web_deliverable=True)
    assert info2.get("already_locked") is True
    assert twice.count(_HARD) == 1


def test_no_doublon_when_claim_also_fires():
    """Claim navigateur détecté → la bannière claim (3) parle, la note (7) se tait."""
    out, info = fg.apply_mission_truth_lock(
        "Frontend vérifié au navigateur, tout marche.",
        has_green_test=True, has_browser_proof=False, web_deliverable=True)
    assert info["changed"] is True
    assert _CLAIM in out
    assert _HARD not in out  # anti-doublon


# ── M1.a : regex élargie (couche 2 — formes nominales) ─────────────────────────────

def test_nominal_forms_now_flagged():
    assert fg.claims_browser_verified("🔬 Test navigateur validé sur http://127.0.0.1:8085")
    assert fg.claims_browser_verified("Tests navigateur réussis, tout est bon.")
    assert fg.claims_browser_verified("Le flux a été validé au navigateur.")


def test_existing_forms_and_negations_intact():
    assert fg.claims_browser_verified("Vérifié au navigateur : le flux passe.")
    assert fg.claims_browser_verified("frontend vérifié")
    assert not fg.claims_browser_verified("Pas de test navigateur pour l'instant.")
    assert not fg.claims_browser_verified("Le backend est fonctionnel.")
    assert not fg.claims_browser_verified("")


# ── M1.a' : scope top-lead du flag react ───────────────────────────────────────────

class _FakeLedger:
    def __init__(self, basenames=None):
        self._basenames = set(basenames or [])

    def written_basenames(self):
        return self._basenames

    def has_browser_action(self):
        return False


def _make_react(basenames, allowed_files):
    from src.reasoning.react import ReActLoop
    r = ReActLoop.__new__(ReActLoop)
    r.execution_ledger = _FakeLedger(basenames=basenames)
    r.task_id = None
    r.task_orchestrator = None
    r._mission_allowed_files_meta = lambda: allowed_files
    return r


def test_flag_true_for_top_lead_with_web():
    r = _make_react({"index.html", "app.py"}, [])
    assert r._truth_lock_web_flag() is True


def test_flag_false_for_sub_worker():
    """Un sous-worker (périmètre allowed_files) ne porte jamais la policy."""
    r = _make_react({"index.html"}, ["static/index.html"])
    assert r._truth_lock_web_flag() is False


def test_flag_false_without_web_deliverable():
    r = _make_react({"rapport.md", "app.py"}, [])
    assert r._truth_lock_web_flag() is False


# ── M1.b : message sanitizer serveur (guidance, pas redirection) ───────────────────

def test_flask_refusal_guides_to_preview_server():
    ok, reason = sanitize_command("flask --app app run --port 8085")
    assert ok is False
    # LOT 2.0 : la guidance nomme l'outil RÉEL avec sa syntaxe d'appel
    # (start_preview_server était un fantôme — run MotDuJour).
    assert "serve_website" in reason
    assert "move_mouse" not in reason  # fini la guidance souris/clavier hors sujet


def test_uvicorn_and_gunicorn_refusals_guide_too():
    for cmd in ("uvicorn app:app --port 8085", "gunicorn app:app"):
        ok, reason = sanitize_command(cmd)
        assert ok is False
        assert "serve_website" in reason


def test_other_blocked_exe_keeps_generic_message():
    ok, reason = sanitize_command("supercalifragilistic --do-things")
    assert ok is False
    assert "start_preview_server" not in reason
    assert "move_mouse" in reason  # message générique inchangé (non-régression)
