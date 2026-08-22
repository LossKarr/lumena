"""LOT D (run FidéliBar 2026-07-04) — jambe navigateur : claim sans exécution.

FidéliBar : le lead n'a JAMAIS servi l'app (aucun start_preview_server), browser_*
a échoué (« Navigateur non démarré »), et le FINAL livré disait « 4. Frontend
fonctionnel ✅ — gain de points, échange, refus » — qui ÉCHAPPAIT au détecteur
navigateur de LOT E (il ne couvrait que « vérifié...navigateur »).

D.1 : claims_browser_verified() couvre aussi les reformulations UI (« frontend
      fonctionnel / site opérationnel / page marche »), SCOPÉ à un nom d'interface,
      hors aveu négatif.
D.2 : BROWSER GATE à relance bornée (1 tir) — livrable web + intention/claim de
      vérif + aucune action browser_* réussie → relance dirigée avant FINAL.
"""
from __future__ import annotations

import src.reasoning.final_guards as fg


# ── D.1 : détection UI-scopée + garde négation ────────────────────────────────────

def test_ui_functional_claims_flagged():
    assert fg.claims_browser_verified("4. Frontend fonctionnel ✅")
    assert fg.claims_browser_verified("Le site est opérationnel.")
    assert fg.claims_browser_verified("La page marche parfaitement.")
    assert fg.claims_browser_verified("Interface OK, tout est prêt.")
    assert fg.claims_browser_verified("L'appli web est pleinement fonctionnelle.")


def test_existing_browser_claims_still_flagged():
    # Non-régression LOT E : les tournures explicites restent captées.
    assert fg.claims_browser_verified("Vérifié au navigateur : le flux passe.")
    assert fg.claims_browser_verified("frontend vérifié")
    assert fg.claims_browser_verified("Testé dans le navigateur.")


def test_backend_functional_never_flagged():
    """Scoping : « backend/module/API fonctionnel » n'est PAS un claim navigateur."""
    assert not fg.claims_browser_verified("Le backend est fonctionnel.")
    assert not fg.claims_browser_verified("Module transactions opérationnel.")
    assert not fg.claims_browser_verified("API fonctionnelle, tests verts.")
    assert not fg.claims_browser_verified("Le code est fonctionnel.")


def test_ui_negation_never_flagged():
    """Aveu honnête → jamais rétrogradé."""
    assert not fg.claims_browser_verified("Pas de frontend fonctionnel pour l'instant.")
    assert not fg.claims_browser_verified("Le site n'est pas opérationnel.")
    assert not fg.claims_browser_verified("Aucune page fonctionnelle livrée.")
    assert not fg.claims_browser_verified("")


def test_ui_functional_downgraded_by_truth_lock_without_proof():
    out, info = fg.apply_mission_truth_lock(
        "4. Frontend fonctionnel ✅ — gain de points, échange refusé.",
        has_green_test=True, has_browser_proof=False)
    assert info["changed"] is True
    assert "Vérification navigateur NON prouvée" in out


def test_ui_functional_passes_with_browser_proof():
    out, info = fg.apply_mission_truth_lock(
        "Frontend fonctionnel ✅", has_green_test=True, has_browser_proof=True)
    assert info["changed"] is False


# ── D.2 : helpers du BROWSER GATE (purs) ──────────────────────────────────────────

def _make_react():
    """ReActLoop minimal sans dépendances lourdes (on ne teste que les helpers purs)."""
    from src.reasoning.react import ReActLoop
    r = ReActLoop.__new__(ReActLoop)  # pas d'__init__ : on injecte le strict nécessaire
    return r


def test_browser_verify_intent():
    r = _make_react()
    assert r._browser_verify_intent("vérifie dans le navigateur que le solde monte")
    assert r._browser_verify_intent("teste au navigateur le flux complet")
    assert r._browser_verify_intent("confirme au navigateur l'affichage")
    # pas d'intention : « navigateur » sans verbe de vérif, ou pas de navigateur
    assert not r._browser_verify_intent("construis une app web de fidélité")
    assert not r._browser_verify_intent("écris le guide du navigateur")
    assert not r._browser_verify_intent("")


class _FakeLedger:
    def __init__(self, browser=False, basenames=None):
        self._browser = browser
        self._basenames = set(basenames or [])

    def has_browser_action(self):
        return self._browser

    def written_basenames(self):
        return self._basenames


def test_web_present_from_written_basenames():
    r = _make_react()
    r.execution_ledger = _FakeLedger(basenames={"index.html", "app.py"})
    r.task_id = None
    r.task_orchestrator = None
    assert r._mission_web_present_for_gate() == "page web écrite pendant ce run"


def test_web_absent_when_no_web_file():
    r = _make_react()
    r.execution_ledger = _FakeLedger(basenames={"app.py", "customers.py"})
    r.task_id = None
    r.task_orchestrator = None
    assert r._mission_web_present_for_gate() == ""


def test_pending_triggers_on_intent():
    r = _make_react()
    r.execution_ledger = _FakeLedger(browser=False, basenames={"index.html"})
    r.task_id = None
    r.task_orchestrator = None
    # intention navigateur dans l'objectif → pending
    assert r._mission_browser_verify_pending("j'ai fini", "vérifie au navigateur le flux")


def test_pending_triggers_on_claim_even_without_intent():
    """Déclencheur validé revue : objectif SANS « vérifie » mais FINAL claim UI."""
    r = _make_react()
    r.execution_ledger = _FakeLedger(browser=False, basenames={"index.html"})
    r.task_id = None
    r.task_orchestrator = None
    assert r._mission_browser_verify_pending("Frontend fonctionnel ✅", "construis une app web")


def test_pending_false_when_browser_action_present():
    r = _make_react()
    r.execution_ledger = _FakeLedger(browser=True, basenames={"index.html"})
    r.task_id = None
    r.task_orchestrator = None
    # preuve déjà au ledger → aucune relance
    assert r._mission_browser_verify_pending("Frontend fonctionnel", "vérifie au navigateur") == ""


def test_pending_false_when_no_web_file():
    r = _make_react()
    r.execution_ledger = _FakeLedger(browser=False, basenames={"app.py"})
    r.task_id = None
    r.task_orchestrator = None
    assert r._mission_browser_verify_pending("Frontend fonctionnel", "vérifie au navigateur") == ""


def test_pending_false_when_web_but_no_intent_no_claim():
    """Page web mais ni intention objectif ni claim UI → pas de relance (statique)."""
    r = _make_react()
    r.execution_ledger = _FakeLedger(browser=False, basenames={"index.html"})
    r.task_id = None
    r.task_orchestrator = None
    assert r._mission_browser_verify_pending("livrable produit", "construis une landing page") == ""


def test_pending_false_for_delegated_sub_worker():
    """LOT D-fix (run CoVoit'Éco) : un SOUS-WORKER délégué (périmètre allowed_files)
    ne déclenche JAMAIS le BROWSER GATE — même web + intention + claim. La vérif
    navigateur est le job du top-lead (l'app n'est pas servie pendant le run isolé
    du sous-worker). Ferme mon sur-déclenchement sur w_backend/w_tests."""
    r = _make_react()
    r.execution_ledger = _FakeLedger(browser=False, basenames={"index.html"})
    r.task_id = None
    r.task_orchestrator = None
    # simule un sous-worker : périmètre de fichiers assigné
    r._mission_allowed_files_meta = lambda: ["index.html", "app.js"]
    # objectif AVEC le boilerplate « navigateur PARTAGÉ » + « test » + claim UI
    objectif = "Implémente le frontend. Tu travailles sur un navigateur PARTAGÉ, teste…"
    assert r._mission_browser_verify_pending("Frontend fonctionnel ✅", objectif) == ""


def test_pending_true_for_top_lead_no_allowed_files():
    """Le top-lead (aucun allowed_files) déclenche bien le gate."""
    r = _make_react()
    r.execution_ledger = _FakeLedger(browser=False, basenames={"index.html"})
    r.task_id = None
    r.task_orchestrator = None
    r._mission_allowed_files_meta = lambda: []  # top-lead : pas de périmètre
    assert r._mission_browser_verify_pending("Frontend fonctionnel ✅", "construis une app web")
