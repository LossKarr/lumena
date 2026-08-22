"""2.9 (re-run 4 familles post-2.8, 2026-07-08) — passe de finition : fermer les
faux positifs et les validations trop superficielles révélés par les 4 runs.

- A : le BROWSER GATE tirait sur data/API (« PAS de navigateur », « API Flask sans
      interface ») car _WEB_OBJECTIVE_RE matchait `flask`/`navigateur` nus.
- B : TriboBlog livré NU — style.css hors contrat → aucun `<link>` → site sans style,
      et aucun 404 (le css existe, il n'est juste pas lié).
- C : test_cli.py resté stub + lead bloqué par le guard A3 pour le remplir.
- D : le CodeAgent a clamé « rempli » alors que le fichier restait un stub.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


# ═══════════════ 2.9.A — l'objectif veut-il VRAIMENT un navigateur ? ════════════


class TestObjectiveWantsBrowser:
    def _f(self):
        from src.reasoning.react import _objective_wants_browser
        return _objective_wants_browser

    def test_multipage_web_yes(self):
        f = self._f()
        assert f("site statique 3 pages, index.html, browser_navigate sur l'accueil, "
                 "cliquer vers article1, vérifier le titre") is True

    def test_data_mission_no(self):
        f = self._f()
        assert f("genere_donnees.py crée ventes.csv, analyse.py, rapport.txt, "
                 "tests. Pas de navigateur.") is False

    def test_api_without_front_no(self):
        f = self._f()
        assert f("API REST Flask SANS interface, app.py create_app(), tests. "
                 "Pas de page HTML, pas de navigateur : valide par les tests "
                 "uniquement. Ne conclus navigateur validé nulle part.") is False

    def test_cli_no(self):
        f = self._f()
        assert f("outil Python CLI, core.py, cli.py, tests. Pas de navigateur : "
                 "c'est un outil CLI.") is False

    def test_flask_alone_insufficient(self):
        """`flask` seul (sans HTML ni négation) ne suffit plus."""
        f = self._f()
        assert f("Construis une API Flask dans app.py avec une route /ping") is False

    def test_positive_navigateur_still_fires(self):
        f = self._f()
        assert f("vérifie au navigateur que la page s'affiche correctement") is True

    def test_empty_no(self):
        f = self._f()
        assert f("") is False and f(None) is False


# ═══════════════ 2.9.B — CSS présent mais lié par aucun HTML ════════════════════


class TestUnlinkedStylesheets:
    def _f(self):
        from src.tools.website_builder import _unlinked_stylesheets
        return _unlinked_stylesheets

    def test_css_linked_by_no_page_detected(self):
        """Le cas TriboBlog2 : style.css existe, aucune page ne le lie."""
        d = Path(tempfile.mkdtemp())
        (d / "index.html").write_text("<html><head></head><body>x</body></html>",
                                      encoding="utf-8")
        (d / "article1.html").write_text("<html><head></head><body>y</body></html>",
                                         encoding="utf-8")
        (d / "style.css").write_text("body{color:red}", encoding="utf-8")
        assert self._f()(d) == ["style.css"]

    def test_css_linked_by_one_page_ok(self):
        """Lié par au moins une page → non orphelin (pas d'alerte)."""
        d = Path(tempfile.mkdtemp())
        (d / "index.html").write_text('<link rel="stylesheet" href="style.css">',
                                      encoding="utf-8")
        (d / "style.css").write_text("body{}", encoding="utf-8")
        assert self._f()(d) == []

    def test_no_css_silent(self):
        d = Path(tempfile.mkdtemp())
        (d / "index.html").write_text("<html></html>", encoding="utf-8")
        assert self._f()(d) == []

    def test_fail_open_bad_dir(self):
        assert self._f()("/no/such/dir/xyz") == []


class TestContractStylesheetWarning:
    def _f(self):
        from src.subagents.mission_contract import missing_shared_stylesheet_warning
        return missing_shared_stylesheet_warning

    def test_html_without_css_warns(self):
        c = {"project": "x", "files": [
            {"path": "index.html", "owner": "a"},
            {"path": "article1.html", "owner": "b"}]}
        w = self._f()(c)
        assert w and "style.css" in w

    def test_html_with_css_silent(self):
        c = {"project": "x", "files": [
            {"path": "index.html", "owner": "a"},
            {"path": "style.css", "owner": "b", "no_public_api": True}]}
        assert self._f()(c) == ""

    def test_no_html_silent(self):
        c = {"project": "x", "files": [{"path": "app.py", "owner": "a"}]}
        assert self._f()(c) == ""


# ═══════════════ 2.9.C — un test encore STUB est remplissable ═══════════════════


class TestPureStubTest:
    def _f(self):
        from src.reasoning.handlers.files import _is_pure_stub_test
        return _is_pure_stub_test

    def _write(self, body):
        p = Path(tempfile.mkdtemp()) / "test_cli.py"
        p.write_text(body, encoding="utf-8")
        return p

    def test_notimplemented_stub_is_fillable(self):
        p = self._write(
            "def test_x() -> None:\n"
            "    raise NotImplementedError('TODO worker — cf. CONTRAT.md')\n")
        assert self._f()(p) is True

    def test_real_test_is_locked(self):
        p = self._write("def test_x():\n    assert count_words('a a') == {'a': 2}\n")
        assert self._f()(p) is False

    def test_stub_with_real_assert_is_locked(self):
        """Un fichier qui a DÉJÀ une vraie assertion n'est plus un stub pur même
        s'il reste un NotImplementedError ailleurs → on ne relâche pas."""
        p = self._write(
            "def test_a():\n    assert 1 == 1\n\n"
            "def test_b():\n    raise NotImplementedError('TODO worker')\n")
        assert self._f()(p) is False

    def test_status_code_assertion_is_locked(self):
        p = self._write(
            "def test_api():\n    r = c.post('/x')\n"
            "    raise NotImplementedError('TODO worker')  # oubli\n"
            "    assert r.status_code == 200\n")
        assert self._f()(p) is False

    def test_missing_file_not_stub(self):
        assert self._f()(Path(tempfile.mkdtemp()) / "absent.py") is False


# ═══════════════ 2.9.D — CodeAgent qui clame « rempli » sur un stub ═════════════


class TestStillStubDetectionAnchors:
    """Garde-fou anti-régression sur le câblage (le comportement réel passe par
    delegate_task_handler, testé au runtime des runs)."""

    def test_agents_handler_flags_remaining_stub(self):
        src = Path("src/reasoning/handlers/agents.py").read_text(encoding="utf-8")
        assert "_still_stub_artifacts" in src
        assert "raise NotImplementedError" in src
        assert "encore des STUBS" in src
