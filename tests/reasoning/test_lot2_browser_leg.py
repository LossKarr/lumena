"""LOT 2 clôture (run MotDuJour 2026-07-06) — la jambe navigateur devient possible.

2.0 : `start_preview_server` était un OUTIL FANTÔME (fonction interne, jamais
      enregistrée) nommé en PREMIER par toute la guidance → le lead a dit 2×
      « pas dans ma liste », écrit run.py puis fabriqué. En prime, le défaut
      port=8080 de serve_website était le port RÉSERVÉ Lumena (refus garanti).
2.1 : index.html livré SANS <link> (143 lignes de CSS jamais chargées) et
      title = description verbatim du contrat.
2.2 : contrat web sans route `/` → app inaccessible même servie.
2.3 : « 6. ✅ Serveur — Flask lancé sur le port 8085 » FABRIQUÉ, livré sous la
      bannière navigateur : aucun verrou sur le claim serveur.
2.4 : rien n'oblige le lead à `node --check` (pytest vert ne dit rien du JS).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import src.reasoning.final_guards as fg
from src.reasoning.handlers.website import get_website_handler_defs
from src.runtime.execution_ledger import ExecutionLedger
from src.subagents.mission_contract import generate_stub, web_root_route_warning
from src.utils.command_sanitizer import sanitize_command


# ═══════════════ 2.0 — l'outil fantôme devient réel ═══════════════

class TestPreviewToolReal:
    def _defs(self):
        return {d.name: d for d in get_website_handler_defs()}

    def test_start_preview_server_is_a_real_tool(self):
        defs = self._defs()
        assert "start_preview_server" in defs, "l'outil fantôme doit devenir réel"
        assert callable(defs["start_preview_server"].handler)

    def test_default_ports_not_reserved(self):
        """8080 = port réservé Lumena (E.0) → un défaut 8080 était un refus garanti."""
        defs = self._defs()
        for name in ("serve_website", "start_preview_server"):
            props = defs[name].parameters["properties"]
            assert props["port"]["default"] == 8081, name

    def test_guidance_gives_exact_call_syntax(self):
        ok, reason = sanitize_command("python -m flask run --port 8085")
        assert ok is False
        assert "serve_website(directory=" in reason  # syntaxe d'appel, pas juste un nom


# ═══════════════ 2.1 — stub HTML relié + title propre ═══════════════

_MOTDUJOUR_FILES = [
    {"path": "app.py", "owner": "w_backend", "desc": "backend"},
    {"path": "static/index.html", "owner": "w_frontend",
     "desc": "Page HTML avec titre 'MotDuJour', affiche la definition du mot"},
    {"path": "static/style.css", "owner": "w_frontend", "desc": "css"},
    {"path": "static/script.js", "owner": "w_frontend", "desc": "js"},
]


class TestHtmlStubLinked:
    def test_siblings_linked_and_title_clean(self):
        entry = _MOTDUJOUR_FILES[1]
        stub = generate_stub(entry, all_files=_MOTDUJOUR_FILES, project="motdujour")
        assert '<link rel="stylesheet" href="style.css">' in stub
        assert '<script src="script.js" defer></script>' in stub
        assert "<title>motdujour</title>" in stub
        assert "Page HTML avec titre" not in stub.split("</title>")[0]  # plus de desc verbatim

    def test_no_siblings_no_links(self):
        entry = {"path": "index.html", "owner": "w", "desc": "page"}
        stub = generate_stub(entry)
        assert "<link" not in stub
        assert "<script" not in stub
        assert "<title>index</title>" in stub

    def test_sibling_in_other_dir_not_linked(self):
        files = [{"path": "index.html", "owner": "w"},
                 {"path": "assets/other.css", "owner": "w"}]
        stub = generate_stub(files[0], all_files=files)
        assert "<link" not in stub  # pas le même dossier


# ═══════════════ 2.2 — warning route racine ═══════════════

class TestRootRouteWarning:
    def test_motdujour_contract_warns(self):
        data = {"project": "motdujour", "files": [
            {"path": "app.py", "owner": "w_backend",
             "desc": "Application Flask avec create_app(), GET /api/word, POST /api/guess",
             "api": ["def create_app() -> Flask", "def get_word() -> dict"]},
            {"path": "static/index.html", "owner": "w_frontend", "desc": "page"},
        ]}
        warn = web_root_route_warning(data)
        assert "GET /" in warn and "index.html" in warn

    def test_miniquiz_like_contract_silent(self):
        data = {"files": [
            {"path": "app.py", "owner": "w_backend",
             "desc": "Flask app, endpoint GET / serving index.html",
             "api": ["def create_app() -> Flask", "def index() -> str"]},
            {"path": "static/index.html", "owner": "w_frontend", "desc": "page"},
        ]}
        assert web_root_route_warning(data) == ""

    def test_non_web_contract_silent(self):
        data = {"files": [{"path": "app.py", "owner": "w", "api": ["def f() -> int"]}]}
        assert web_root_route_warning(data) == ""


# ═══════════════ 2.3 — verrou claim « serveur lancé » ═══════════════

_FABRICATED_SERVER_LINE = (
    "Bilan :\n6. ✅ **Serveur** — Flask lancé sur le port 8085\n"
    "L'app est toujours accessible sur http://127.0.0.1:8085 si tu veux."
)


class TestServerClaimLock:
    def test_fabricated_claim_bannered(self):
        out, info = fg.apply_mission_truth_lock(
            _FABRICATED_SERVER_LINE,
            has_green_test=True, has_server_started=False)
        assert info["changed"] is True
        assert info.get("overclaim_server") is True
        assert "Serveur NON lancé dans ce run" in out

    def test_with_proof_untouched(self):
        out, info = fg.apply_mission_truth_lock(
            _FABRICATED_SERVER_LINE,
            has_green_test=True, has_server_started=True)
        assert info["changed"] is False

    def test_default_none_preserves_existing_callers(self):
        out, info = fg.apply_mission_truth_lock(
            _FABRICATED_SERVER_LINE, has_green_test=True)
        assert info["changed"] is False

    def test_banner_not_self_matching(self):
        once, _ = fg.apply_mission_truth_lock(
            _FABRICATED_SERVER_LINE,
            has_green_test=True, has_server_started=False)
        twice, info2 = fg.apply_mission_truth_lock(
            once, has_green_test=True, has_server_started=False)
        assert info2.get("already_locked") is True
        assert twice.count("Serveur NON lancé dans ce run") == 1

    def test_negation_not_flagged(self):
        assert not fg.claims_server_started("Le serveur n'a pas été lancé sur le port 8085.")
        assert not fg.claims_server_started("Serveur non démarré — voir plus haut.")


# ═══════════════ 2.4 — preuve node --check + gate helper ═══════════════

class TestJsCheckProof:
    def test_ledger_detects_node_check(self):
        led = ExecutionLedger()
        led.append(iteration=1, action="run_command", target="C:/ws/app",
                   success=True, meta={"command": "node --check static/script.js"})
        assert led.has_js_syntax_check() is True

    def test_ledger_ignores_failed_or_other(self):
        led = ExecutionLedger()
        led.append(iteration=1, action="run_command", target="x",
                   success=False, meta={"command": "node --check a.js"})
        led.append(iteration=2, action="run_command", target="x",
                   success=True, meta={"command": "python -m pytest"})
        assert led.has_js_syntax_check() is False


class _FakeLedgerJS:
    def __init__(self, basenames=()):
        self._b = set(basenames)

    def written_basenames(self):
        return self._b


def _make_react_js(basenames):
    from src.reasoning.react import ReActLoop
    r = ReActLoop.__new__(ReActLoop)
    r.execution_ledger = _FakeLedgerJS(basenames)
    r.task_id = None
    r.task_orchestrator = None
    return r


class TestJsGateHelper:
    def test_js_written_detected(self):
        r = _make_react_js({"script.js"})
        assert r._mission_js_present_for_gate() == "JS écrit pendant ce run"

    def test_no_js_silent(self):
        r = _make_react_js({"rapport.md", "app.py"})
        assert r._mission_js_present_for_gate() == ""


# ═══════════════ LOT 2ter (run Converto) — la dernière marche du navigateur ═══════

class TestPublishNextSteps:
    """2.6 — le message de publish nomme l'outil avec sa syntaxe d'appel exacte
    quand le livrable est web (le lead a conclu 2× « pas dans ma liste »)."""

    def test_web_deliverable_gets_exact_serve_call(self):
        from src.reasoning.handlers.missions import _publish_next_steps
        msg = _publish_next_steps("converto", ["CONTRAT.md", "index.html", "script.js"])
        assert "serve_website(directory='workspace/converto', port=8081)" in msg
        assert "browser_navigate" in msg

    def test_non_web_keeps_generic_message(self):
        from src.reasoning.handlers.missions import _publish_next_steps
        msg = _publish_next_steps("rapport", ["rapport.md", "data.csv"])
        assert "serve_website(" not in msg
        assert "si c'est du web" in msg


class TestFinalizeBrowserGate:
    """2.7 — la voie FINALIZE déterministe passe par le BROWSER GATE (patron
    C0.4) : Converto est sorti par plan_complet AVANT de servir."""

    def _react(self, *, shots=0, pending="livrable web"):
        from src.reasoning.react import ReActLoop
        r = ReActLoop.__new__(ReActLoop)
        r._browser_gate_shots = shots
        r._mission_browser_verify_pending = lambda note, q: pending
        return r

    def test_pending_intercepts(self):
        r = self._react()
        assert r._finalize_browser_gate_pending("note", "objectif web navigateur") == "livrable web"

    def test_bounded_one_shot(self):
        r = self._react(shots=1)
        assert r._finalize_browser_gate_pending("note", "objectif") == ""

    def test_no_web_no_gate(self):
        r = self._react(pending="")
        assert r._finalize_browser_gate_pending("note", "objectif") == ""

    def test_defensive_on_error(self):
        from src.reasoning.react import ReActLoop
        r = ReActLoop.__new__(ReActLoop)
        r._browser_gate_shots = 0
        r._mission_browser_verify_pending = None  # appel plantera → ""
        assert r._finalize_browser_gate_pending("n", "q") == ""


class TestContractMustCarryMeaning:
    """2.8 — le contrat Converto (owners seuls) a passé la validation → les
    workers ont inventé. Chaque fichier doit porter du sens (desc OU exports)."""

    def test_converto_empty_contract_now_rejected(self):
        from src.subagents.mission_contract import validate_contract
        errs = validate_contract({"project": "converto", "files": [
            {"path": "index.html", "owner": "w_html"},
            {"path": "style.css", "owner": "w_css"},
            {"path": "script.js", "owner": "w_js"},
        ]})
        assert len(errs) == 3
        assert all("desc" in e for e in errs)

    def test_signature_field_gets_guiding_error(self):
        """La revue : le lead avait mis `signature` (hors vocabulaire) — le
        contrat AVAIT L'AIR riche mais était ignoré en silence."""
        from src.subagents.mission_contract import validate_contract
        errs = validate_contract({"project": "converto", "files": [
            {"path": "script.js", "owner": "w_js",
             "signature": "function convert() -> void"},
        ]})
        joined = " ".join(errs)
        assert "`signature` n'est pas un champ contractuel" in joined
        assert "exports" in joined and "desc" in joined

    def test_desc_or_exports_satisfy(self):
        from src.subagents.mission_contract import validate_contract
        assert validate_contract({"files": [
            {"path": "index.html", "owner": "w", "desc": "titre Converto, champ Celsius"},
            {"path": "script.js", "owner": "w",
             "exports": ["function convert()"]},
        ]}) == []

    def test_init_py_exempt(self):
        from src.subagents.mission_contract import validate_contract
        assert validate_contract({"files": [
            {"path": "pkg/__init__.py", "owner": "w"},
        ]}) == []
