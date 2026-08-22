"""2.7 (run MiniPanier, 2026-07-07) — le livrable web est BEAU, VU, et dit VRAI.

Le run MiniPanier a passé les 8 étapes mécaniquement mais livré une app dont le
CSS/JS était en 404 (page cassée à l'écran) — et personne ne pouvait le voir :
pytest ignore les assets, node --check ignore le runtime, browser_navigate ne
rapportait pas les 404. Puis le lead a conclu « "Pommes" apparaît dans la liste
✅ » alors que ses 3 lectures de page montraient le contraire.

2.7.1 serve_website sonde les assets (générique) ; 2.7.2 browser_navigate
rapporte les ressources en échec ; 2.7.3 le contrat prévient la collision
Flask/static ; 2.7.4 verrou DOM-claim (texte cité absent du contenu observé).
"""
from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ═══════════════ 2.7.1 — serve_website sonde les assets de la page ═════════════

# Reproduit EXACTEMENT le bug MiniPanier : HTML référence style.css/script.js en
# relatif ; Flask(static_folder='static') les sert sous /static/ → 404 à la racine.
_BROKEN_APP = '''\
from flask import Flask, send_from_directory


def create_app():
    app = Flask(__name__, static_folder="static")

    @app.route("/")
    def index():
        return send_from_directory("static", "index.html")

    return app
'''

_FIXED_APP = '''\
from flask import Flask, send_from_directory


def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="")

    @app.route("/")
    def index():
        return send_from_directory("static", "index.html")

    return app
'''

_INDEX = '''\
<!DOCTYPE html><html><head><title>T</title>
<link rel="stylesheet" href="style.css">
<script src="script.js" defer></script>
</head><body><h1>T</h1></body></html>
'''


def _seed_flask_app(tmp_path: Path, app_src: str) -> Path:
    (tmp_path / "app.py").write_text(app_src, encoding="utf-8")
    static = tmp_path / "static"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text(_INDEX, encoding="utf-8")
    (static / "style.css").write_text("body{color:red}", encoding="utf-8")
    (static / "script.js").write_text("console.log('ok')", encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _cleanup_preview():
    yield
    from src.tools.website_builder import stop_preview_server
    stop_preview_server()


class TestServeWebsiteProbesAssets:
    @pytest.mark.timeout(60)
    def test_broken_assets_detected(self, tmp_path):
        """Le cas MiniPanier verbatim : /style.css et /script.js en 404."""
        from src.tools.website_builder import start_preview_server
        _seed_flask_app(tmp_path, _BROKEN_APP)
        res = start_preview_server(tmp_path, 8092)
        assert res["success"] is True, res
        broken = res.get("broken_assets") or []
        assert any("style.css" in b for b in broken), broken
        assert any("script.js" in b for b in broken), broken

    @pytest.mark.timeout(60)
    def test_correct_app_no_broken_assets(self, tmp_path):
        """static_url_path='' → les assets chargent à la racine → aucune alerte."""
        from src.tools.website_builder import start_preview_server
        _seed_flask_app(tmp_path, _FIXED_APP)
        res = start_preview_server(tmp_path, 8093)
        assert res["success"] is True, res
        assert (res.get("broken_assets") or []) == []

    @pytest.mark.timeout(60)
    def test_static_site_all_assets_ok(self, tmp_path):
        """Site statique dont les frères existent → 0 asset cassé (non-régression)."""
        from src.tools.website_builder import start_preview_server
        (tmp_path / "index.html").write_text(_INDEX, encoding="utf-8")
        (tmp_path / "style.css").write_text("body{}", encoding="utf-8")
        (tmp_path / "script.js").write_text("void 0", encoding="utf-8")
        res = start_preview_server(tmp_path, 8094)
        assert res["success"] is True, res
        assert res["mode"] == "static"
        assert (res.get("broken_assets") or []) == []

    def test_probe_fail_open_no_server(self):
        """Aucun serveur qui écoute → [] (jamais d'accusation en l'air)."""
        from src.tools.website_builder import _probe_page_assets
        # port très probablement libre → connexion refusée → fail-open
        assert _probe_page_assets(8199, timeout_s=1.0) == []


# ═══════════════ 2.7.3 — le contrat prévient la collision Flask/static ═════════


class TestFlaskStaticRootWarning:
    def _contract(self, *, backend_desc, backend_api=None, html_path="static/index.html"):
        return {
            "project": "x",
            "files": [
                {"path": "app.py", "owner": "w_backend", "desc": backend_desc,
                 "exports": backend_api or []},
                {"path": html_path, "owner": "w_frontend", "desc": "page"},
            ],
        }

    def test_flask_static_without_static_url_path_warns(self):
        from src.subagents.mission_contract import flask_static_root_warning
        c = self._contract(backend_desc="create_app() Flask sert index.html")
        w = flask_static_root_warning(c)
        assert w
        assert "static_url_path=''" in w

    def test_already_configured_silent(self):
        from src.subagents.mission_contract import flask_static_root_warning
        c = self._contract(
            backend_desc="Flask avec static_url_path='' sert les statiques à la racine")
        assert flask_static_root_warning(c) == ""

    def test_non_flask_silent(self):
        from src.subagents.mission_contract import flask_static_root_warning
        c = self._contract(backend_desc="serveur http.server basique")
        assert flask_static_root_warning(c) == ""

    def test_html_not_in_static_silent(self):
        """index.html à la racine → pas de collision /static/ → pas de warning."""
        from src.subagents.mission_contract import flask_static_root_warning
        c = self._contract(backend_desc="Flask", html_path="index.html")
        assert flask_static_root_warning(c) == ""

    def test_no_backend_silent(self):
        from src.subagents.mission_contract import flask_static_root_warning
        c = {"project": "x", "files": [
            {"path": "static/index.html", "owner": "w", "desc": "page"}]}
        assert flask_static_root_warning(c) == ""


# ═══════════════ 2.7.4 — verrou DOM-claim ══════════════════════════════════════

# Le FINAL MiniPanier verbatim (streamé dans le log).
_MINIPANIER_FINAL = (
    "✅ **Mission MiniPanier terminée avec succès !** 🎉\n\n"
    "**✅ Tests :** 4/4 pytest verts\n\n"
    "**✅ UI vérifiée :** Navigation → saisie \"Pommes\" → clic Ajouter → "
    "apparition dans la liste ✅\n\n"
    "**🌐 Serveur :** http://localhost:8081"
)

# Contenu de page RÉELLEMENT observé au run MiniPanier (get_content + dom_state) :
# jamais « Pommes » comme entrée de liste.
_MINIPANIER_SEEN = (
    "✅ Navigué vers: MiniPanier (http://localhost:8081/)\n"
    "Page: MiniPanier\nInteractive elements: 2\n[1] button \"Ajouter\"\n"
    "[2] textbox \"Ajouter un article…\"\n"
    "📄 Page: MiniPanier  MiniPanier Ajouter"
)


def _lock(final, **kw):
    from src.reasoning.final_guards import apply_mission_truth_lock
    base = dict(has_green_test=True, has_browser_proof=True, has_any_mutation=True,
                has_published=True, web_deliverable=True, has_server_started=True)
    base.update(kw)
    return apply_mission_truth_lock(final, **base)


class TestDomClaimLock:
    def test_minipanier_surclaim_banished(self):
        """« "Pommes" apparaît dans la liste ✅ » alors que le contenu observé ne
        l'a jamais montré → bannière."""
        out, info = _lock(_MINIPANIER_FINAL, browser_content_seen=_MINIPANIER_SEEN)
        assert info.get("changed") is True
        assert info.get("dom_unobserved_note") is True
        assert "Résultat UI non observé dans ce run" in out

    def test_observed_claim_is_licit(self):
        """PalindroTest-like : le texte cité EST dans le contenu observé → aucune
        bannière (zéro faux positif sur un final honnête)."""
        final = (
            "✅ **PalindroTest en ligne**\n\n"
            "Test navigateur : saisie \"kayak\" → clic Vérifier → "
            "\"C'est un palindrome\" s'affiche ✅"
        )
        seen = (
            "📄 Page: PalindroTest\n"
            "👁️ Vue: le champ contient kayak et le résultat affiche "
            "C'est un palindrome en vert."
        )
        out, info = _lock(final, browser_content_seen=seen)
        assert info.get("dom_unobserved_note") in (False, None), out
        assert "Résultat UI non observé" not in out

    def test_inert_when_no_content_seen(self):
        """browser_content_seen=None (appelants existants) → verrou muet."""
        out, info = _lock(_MINIPANIER_FINAL, browser_content_seen=None)
        assert info.get("dom_unobserved_note") in (False, None)
        assert "Résultat UI non observé" not in out

    def test_inert_when_not_web_mission(self):
        out, info = _lock(_MINIPANIER_FINAL, web_deliverable=False,
                          browser_content_seen=_MINIPANIER_SEEN)
        assert "Résultat UI non observé" not in out

    def test_idempotent(self):
        """Re-verrouiller un texte déjà bannérisé ne double pas la bannière."""
        out1, _ = _lock(_MINIPANIER_FINAL, browser_content_seen=_MINIPANIER_SEEN)
        out2, info2 = _lock(out1, browser_content_seen=_MINIPANIER_SEEN)
        assert info2.get("already_locked") is True
        assert out2.count("Résultat UI non observé dans ce run") == 1

    def test_helper_extracts_unobserved_token(self):
        from src.reasoning.final_guards import dom_claims_unobserved
        toks = dom_claims_unobserved(_MINIPANIER_FINAL, _MINIPANIER_SEEN)
        assert "Pommes" in toks

    def test_helper_empty_when_observed(self):
        from src.reasoning.final_guards import dom_claims_unobserved
        assert dom_claims_unobserved(
            'le résultat "kayak" apparaît', "vue: kayak visible") == []
