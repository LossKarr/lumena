"""LOT 1 clôture (run MiniQuiz 2026-07-06) — murs anti-freeze + périmètre + web.

F1  : run_command ne peut plus PENDRE — le timeout était vérifié à l'ARRIVÉE
      d'une ligne stdout ; un Flask orphelin (Start-Process) qui tenait le pipe
      sans écrire bloquait la lecture pour toujours (aucun [cmd_done], worker
      w_frontend `checkpointed` à jamais, lead gelé dans delegate_and_wait).
F2  : le sanitizer guidait flask/uvicorn… mais `Start-Process python app.py`
      (verbe PS `start` + python whitelistés) passait sans un mot.
M3b : le périmètre I.2 ignorait `apply_patches` (pluriel) et les chemins en
      liste — le CodeAgent de w_frontend a patché app.py (owner w_backend).
M6  : mission web SANS mutation (fabrication iter-1) → web_deliverable=False →
      claim « ✅ Navigateur : titre visible » sorti sans bannière.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agents.sub_agent import (
    _CODEAGENT_WRITE_ACTIONS,
    _action_write_paths,
    _write_within_perimeter,
)
from src.reasoning.final_guards import claims_browser_verified
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.system import run_command_handler
from src.utils.command_sanitizer import sanitize_command


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)


# ═══════════════ F2 — sanitizer : Start-Process / modules serveurs ═══════════════

class TestSanitizerServerBypass:
    def test_miniquiz_verbatim_command_refused(self):
        """La commande EXACTE qui a gelé le run."""
        ok, reason = sanitize_command(
            "Start-Process -NoNewWindow python -ArgumentList 'app.py 8081'; "
            "Start-Sleep -Seconds 2; Invoke-WebRequest -Uri http://127.0.0.1:8081/ "
            "-UseBasicParsing | Select-Object -ExpandProperty StatusCode"
        )
        assert ok is False
        assert "serve_website" in reason
        assert "détaché" in reason or "detach" in reason.lower()

    def test_python_m_flask_and_http_server_refused(self):
        for cmd in ("python -m flask run --port 8085",
                    "python -m http.server 8000",
                    "python -m uvicorn app:app --port 8082"):
            ok, reason = sanitize_command(cmd)
            assert ok is False, cmd
            assert "serve_website" in reason, cmd

    def test_flask_exe_still_refused_with_guidance(self):
        """Non-régression M1.b (exe hors whitelist)."""
        ok, reason = sanitize_command("flask --app app run --port 8085")
        assert ok is False
        assert "serve_website" in reason

    def test_innocent_commands_unchanged(self):
        for cmd in ("python script.py",
                    "python -m pytest tests/ -v",
                    "Start-Process notepad",
                    "node --check static/script.js"):
            ok, reason = sanitize_command(cmd)
            assert ok is True, f"{cmd} → {reason}"


# ═══════════════ M3b — périmètre CodeAgent : apply_patches couvert ═══════════════

class TestCodeAgentPerimeterPaths:
    def test_apply_patches_in_write_actions(self):
        assert "apply_patches" in _CODEAGENT_WRITE_ACTIONS

    def test_extract_paths_simple_and_list(self):
        assert _action_write_paths({"path": "a.py"}) == ["a.py"]
        assert _action_write_paths(
            {"patches": [{"file": "a.py"}, {"path": "static/b.js"}]}
        ) == ["a.py", "static/b.js"]

    def test_extract_paths_string_forms(self):
        """Formes réellement émises par les LLM : repr python et JSON."""
        assert _action_write_paths(
            {"patches": "[{'file': 'C:\\\\ws\\\\missions\\\\t1\\\\app.py'}]"}
        ) == ["C:\\ws\\missions\\t1\\app.py"]
        assert _action_write_paths(
            {"patches": '[{"file": "app.py"}, {"file": "tests/test_app.py"}]'}
        ) == ["app.py", "tests/test_app.py"]

    def test_extract_paths_garbage_safe(self):
        assert _action_write_paths(None) == []
        assert _action_write_paths({"patches": "pas du json"}) == []

    def test_cross_worker_patch_detected(self):
        """Le cas MiniQuiz : CodeAgent frontend patche app.py (owner backend)."""
        allowed = frozenset({"static/index.html", "static/style.css", "static/script.js"})
        action = {"patches": [{"file": "static/script.js"}, {"file": "app.py"}]}
        bad = [p for p in _action_write_paths(action)
               if not _write_within_perimeter(p, allowed)]
        assert bad == ["app.py"]

    def test_within_perimeter_all_good_passes(self):
        allowed = frozenset({"static/script.js"})
        action = {"patches": [{"file": "static/script.js"}]}
        bad = [p for p in _action_write_paths(action)
               if not _write_within_perimeter(p, allowed)]
        assert bad == []


# ═══════════════ M6 — claims navigateur nominaux + objectif web ═══════════════

class TestBrowserClaimHoles:
    def test_fabricated_miniquiz_lines_flagged(self):
        """Les lignes EXACTES du final fabriqué de la mission 1."""
        assert claims_browser_verified(
            "6. ✅ Navigateur : titre 'MiniQuiz' visible, bouton Paris cliqué, "
            "'Bonne réponse' affiché"
        )
        assert claims_browser_verified("**Vérification navigateur** : tout est ok")

    def test_negations_and_banners_not_flagged(self):
        assert not claims_browser_verified(
            "⚠️ **Navigateur NON vérifié** — livrable web sans action navigateur réussie"
        )
        assert not claims_browser_verified("Navigateur : bloqué par la protection SSRF")
        assert not claims_browser_verified("Le backend est fonctionnel.")


class _FakeLedger:
    def __init__(self, basenames=None):
        self._basenames = set(basenames or [])

    def written_basenames(self):
        return self._basenames

    def has_browser_action(self):
        return False


class _FakeOrch:
    """`_is_mission_run` est une PROPERTY (task_id + metadata.kind=='mission')."""

    def get_task(self, task_id):
        return {"metadata": {"kind": "mission"}}


def _make_react(*, mission=True, objective="", basenames=None):
    from src.reasoning.react import ReActLoop
    r = ReActLoop.__new__(ReActLoop)
    r.execution_ledger = _FakeLedger(basenames=basenames)
    r.task_id = "task_lot1" if mission else None
    r.task_orchestrator = _FakeOrch() if mission else None
    r._original_query = objective
    r._mission_allowed_files_meta = lambda: []
    return r


class TestWebObjectiveSource:
    def test_mission1_fabrication_now_covered(self):
        """Mission 1 MiniQuiz : zéro mutation, zéro contrat — l'objectif web suffit."""
        r = _make_react(objective="Construis MiniQuiz, une application web Flask "
                                  "de quiz dans workspace/miniquiz/ avec static/index.html")
        assert r._mission_web_present_for_gate() == "objectif web explicite"
        assert r._truth_lock_web_flag() is True

    def test_non_web_mission_untouched(self):
        r = _make_react(objective="Rédige un rapport PDF sur les ventes du T2")
        assert r._mission_web_present_for_gate() == ""
        assert r._truth_lock_web_flag() is False

    def test_chat_never_affected(self):
        r = _make_react(mission=False, objective="fais-moi une page web flask")
        assert r._mission_web_present_for_gate() == ""

    def test_ledger_source_still_first(self):
        """Non-régression : la source ledger existante prime toujours."""
        r = _make_react(mission=False, objective="", basenames={"index.html"})
        assert r._mission_web_present_for_gate() == "page web écrite pendant ce run"


# ═══════════════ F1 — run_command ne pend plus ═══════════════

# Petit-fils qui HÉRITE des handles stdout/stderr et dort, parent qui SORT tout
# de suite — le patron exact du Flask orphelin lancé via Start-Process. Avant F1,
# run_command restait bloqué à lire le pipe tenu par le petit-fils. Une seule
# ligne : cmd.exe (shell=True) ne transmet pas les sauts de ligne d'un `-c`.
_ORPHAN_SCRIPT = (
    "import subprocess, sys; "
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(45)']); "
    "print('PARENT_DONE', p.pid)"
)


class TestRunnerNoHang:
    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_orphan_child_does_not_hang(self, ctx):
        """Parent sorti + petit-fils qui tient le pipe → retour RAPIDE avec note."""
        t0 = time.monotonic()
        r = await run_command_handler(
            ctx, command=f'python -c "{_ORPHAN_SCRIPT}"', timeout=30,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 25, f"run_command a mis {elapsed:.0f}s — encore pendu"
        assert "PARENT_DONE" in r.output
        # nettoyage : tuer le dormeur orphelin
        try:
            import re as _re, subprocess as _sp
            m = _re.search(r"PARENT_DONE (\d+)", r.output)
            if m:
                _sp.call(["taskkill", "/F", "/T", "/PID", m.group(1)],
                         stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        except Exception:
            pass

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_timeout_kills_tree_with_partial_output(self, ctx):
        """Commande qui écrit PUIS se tait → timeout tiré, sortie partielle rendue."""
        t0 = time.monotonic()
        r = await run_command_handler(
            ctx,
            command='python -c "import time; print(\'BOOT_LINE\', flush=True); time.sleep(30)"',
            timeout=3,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 20, f"timeout non honoré ({elapsed:.0f}s)"
        assert "Timeout" in r.output
        assert "BOOT_LINE" in r.output  # la sortie partielle accompagne le timeout

    @pytest.mark.asyncio
    async def test_normal_command_unchanged(self, ctx):
        r = await run_command_handler(ctx, command="echo LOT1_OK")
        assert "LOT1_OK" in r.output
