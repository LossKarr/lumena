"""2.6 (run MiniQuiz §5, 2026-07-07) — la jambe navigateur passe par la voie
officielle À TOUS LES COUPS.

Autopsie du run : le lead n'avait pas `serve_website` dans son prompt (filtre
contextuel) → il a servi Flask à la main (`python -c "...app.run(...)"`) →
serveur hors registre SSRF → browser_navigate bloqué → fabrication « clic
Paris confirmé » (rattrapée par le truth-lock, mais critère §5 raté). Pendant
ce temps, un vérifieur navigateur automatique poussait les workers à
« corriger » des 404 structurels, le CodeAgent contournait son périmètre via
`powershell -Command "... | Set-Content app.py"` (port du contrat violé), et
le lead a publié sur un delegate_and_wait PARTIEL pendant que les workers
mutaient encore les fichiers.

2.6.1 force_allow_tools ; 2.6.2 sanitizer (Start-Job, Set-Content/Out-File
imbriqués, python -c écriture/serveur) ; 2.6.3 vérifieur post-delegate OFF en
mission ; 2.6.4 publish refusé tant que des workers tournent.
"""
from __future__ import annotations

import time
import types

import pytest

from src.utils.command_sanitizer import sanitize_command


# ═══════════════ 2.6.1 — serve_website GARANTI au prompt du lead web ═══════════


def _registry(allowed, modules):
    from src.reasoning.tool_registry import ToolRegistry
    tr = ToolRegistry.__new__(ToolRegistry)
    tr._allowed_tools = allowed
    tr._tool_modules = modules
    tr.tools = {}
    tr._tools_desc_cache = "cache-sentinelle"
    return tr


class TestForceAllowTools:
    def test_missing_tool_added_and_cache_invalidated(self):
        tr = _registry({"read_file"}, {"read_file": "files", "serve_website": "website"})
        added = tr.force_allow_tools(("serve_website",))
        assert added == ["serve_website"]
        assert "serve_website" in tr._allowed_tools
        assert tr._tools_desc_cache is None  # le prompt sera reconstruit

    def test_noop_without_filter(self):
        """Aucun filtre actif → tout est déjà visible, rien à forcer."""
        tr = _registry(None, {"serve_website": "website"})
        assert tr.force_allow_tools(("serve_website",)) == []

    def test_unknown_tool_not_added(self):
        tr = _registry({"read_file"}, {"read_file": "files"})
        assert tr.force_allow_tools(("outil_inexistant",)) == []
        assert tr._tools_desc_cache == "cache-sentinelle"  # cache intact

    def test_idempotent(self):
        tr = _registry({"serve_website"}, {"serve_website": "website"})
        assert tr.force_allow_tools(("serve_website",)) == []

    def test_mission_web_lead_tools_cover_the_official_path(self):
        """La constante couvre preview officielle + navigateur de preuve."""
        from src.reasoning.react import _MISSION_WEB_LEAD_TOOLS
        for name in ("serve_website", "start_preview_server", "stop_website_server",
                     "browser_navigate", "browser_click"):
            assert name in _MISSION_WEB_LEAD_TOOLS


# ═══════════════ 2.6.2 — contournements shell du run, fermés ═══════════════════


class TestShellBypassClosed:
    def test_nested_set_content_blocked(self):
        """Verbatim du run (01:01:10) : le CodeAgent borné à tests/ a muté
        app.py (port du contrat 8081→8085) via un -replace pipé — le check de
        verbe ne voyait que le cmdlet de TÊTE."""
        ok, reason = sanitize_command(
            'powershell -Command "(Get-Content -Encoding UTF8 app.py) '
            "-replace 'port=8081', 'port=8085' | Set-Content -Encoding UTF8 app.py\""
        )
        assert ok is False
        assert "edit_file" in reason

    def test_out_file_blocked(self):
        """Out-File a un verbe whitelisté (`out`) → même trou, même fermeture."""
        ok, reason = sanitize_command('powershell -Command "echo hi | Out-File notes.txt"')
        assert ok is False
        assert "edit_file" in reason

    def test_start_job_blocked_with_serve_website_guidance(self):
        """Verbatim du run (01:03:51) : 3 serveurs Flask fantômes via Start-Job."""
        ok, reason = sanitize_command(
            'powershell -Command "$j=Start-Job -ScriptBlock {python app.py}; '
            "Start-Sleep 3; try{ $r=Invoke-WebRequest -Uri "
            "'http://localhost:8085/static/style.css' -UseBasicParsing } catch {}\""
        )
        assert ok is False
        assert "serve_website" in reason

    def test_python_c_file_write_blocked(self):
        """Verbatim du run (00:58:43) : test_run_desktop.py créé à la racine
        via python -c → fichier poubelle PUBLIÉ dans le livrable."""
        ok, reason = sanitize_command(
            "python -c \"open('C:/Users/x/ws/test_run_desktop.py','w',"
            "encoding='utf-8').write('d')\""
        )
        assert ok is False
        assert "edit_file" in reason

    def test_python_c_manual_flask_server_blocked(self):
        """Verbatim du run (01:02:53) : LE contournement du lead — serveur réel
        mais hors registre SSRF → browser_navigate bloqué → fabrication."""
        ok, reason = sanitize_command(
            'python -c "from app import create_app; app = create_app(); '
            'app.run(port=8085, debug=False)"'
        )
        assert ok is False
        assert "serve_website" in reason

    # ── non-régression : le quotidien légitime passe toujours ──────────────

    @pytest.mark.parametrize("cmd", [
        "python -m pytest tests/test_app.py -v",
        "node --check static/script.js",
        "python -c \"print('ok')\"",
        "python -c \"import json; print(json.dumps({'a': 1}))\"",
        "Get-Content app.py",
        "python script.py",
    ])
    def test_legitimate_commands_still_allowed(self, cmd):
        ok, reason = sanitize_command(cmd)
        assert ok is True, f"{cmd!r} bloqué à tort: {reason}"


# ═══════════════ 2.6.3 — vérifieur web post-delegate OFF en mission ════════════


class _MissionOrch:
    def get_task(self, tid):
        return {"metadata": {"kind": "mission"}}


def _react(*, mission: bool):
    from src.reasoning.react import ReActLoop
    r = ReActLoop.__new__(ReActLoop)
    if mission:
        r.task_id = "task_x"
        r.task_orchestrator = _MissionOrch()
    else:
        r.task_id = None
        r.task_orchestrator = None
    return r


class TestPostDelegateVerifyOffInMission:
    def test_mission_never_verifies(self):
        assert _react(mission=True)._post_delegate_web_verify_allowed() is False

    def test_chat_keeps_current_behavior(self):
        from src.reasoning.delegate_strategy import _post_delegate_web_verify_enabled
        r = _react(mission=False)
        assert r._post_delegate_web_verify_allowed() == bool(_post_delegate_web_verify_enabled())


# ═══════════════ 2.6.4 — publish REFUSÉ tant que des workers tournent ══════════


def _publish_ctx(tmp_path):
    from src.runtime.task_orchestrator import TaskOrchestrator
    from src.tools.file_guardrails import WorkspaceFileGuardrails
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    fg = WorkspaceFileGuardrails(tmp_path)
    ctx = types.SimpleNamespace(lumena=core, runtime_task_id=None, file_guardrails=fg)
    return ctx, orch


def _lead_with_children(tmp_path, orch, *, n_children=2):
    lead = orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead",
                           metadata={"kind": "mission", "depth": 1})
    kids = []
    for i in range(n_children):
        k = orch.start_task(conversation_id="__missions__", channel="mission",
                            message_preview=f"w{i}",
                            metadata={"kind": "mission", "depth": 2,
                                      "parent_id": lead.task_id})
        orch.mark_running(k.task_id)
        kids.append(k.task_id)
    orch.set_task_metadata(lead.task_id, children=kids)
    d = tmp_path / "missions" / lead.task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "app.py").write_text("x = 1\n", encoding="utf-8")
    return lead.task_id, kids


class TestPublishBlockedWhileWorkersRun:
    @pytest.mark.asyncio
    async def test_refused_while_children_running(self, tmp_path):
        from src.reasoning.handlers import missions as M
        ctx, orch = _publish_ctx(tmp_path)
        lead_id, kids = _lead_with_children(tmp_path, orch)
        ctx.runtime_task_id = lead_id
        r = await M.publish_mission_workspace_handler(ctx, target="out")
        assert not r.success
        assert "Publication refusée" in r.output
        assert "travaillent" in r.output
        assert not (tmp_path / "out").exists()  # rien copié

    @pytest.mark.asyncio
    async def test_allowed_once_all_children_terminal(self, tmp_path):
        from src.reasoning.handlers import missions as M
        ctx, orch = _publish_ctx(tmp_path)
        lead_id, kids = _lead_with_children(tmp_path, orch)
        ctx.runtime_task_id = lead_id
        for k in kids:
            orch.mark_done(k, result_summary="fini")
        r = await M.publish_mission_workspace_handler(ctx, target="out")
        assert r.success, r.output
        assert (tmp_path / "out" / "app.py").is_file()

    @pytest.mark.asyncio
    async def test_deadline_passed_escape_hatch(self, tmp_path):
        """À l'échéance, les workers sont annulés de toute façon : on ne bloque
        pas la clôture de la mission sur un enfant zombie.

        H1 (2026-08-13) — ce test posait `deadline_ts=time.time() - 10.0`, un
        **float epoch**. Or `manager.create_mission` pose TOUJOURS une chaîne ISO
        (via `normalize_deadline`) : le test validait donc le chemin heureux d'un
        format qui n'existe pas en production, pendant que le vrai format tombait
        dans un `except` silencieux. C'est ce faux témoin qui a laissé le bug
        survivre. Il utilise désormais le format réel.
        """
        from datetime import datetime, timedelta

        from src.reasoning.handlers import missions as M
        ctx, orch = _publish_ctx(tmp_path)
        lead_id, kids = _lead_with_children(tmp_path, orch)
        _past = (datetime.now() - timedelta(seconds=10)).isoformat()
        orch.set_task_metadata(lead_id, deadline_ts=_past)
        ctx.runtime_task_id = lead_id
        r = await M.publish_mission_workspace_handler(ctx, target="out")
        assert r.success, r.output

    @pytest.mark.asyncio
    async def test_epoch_deadline_no_longer_creates_a_false_escape_hatch(self, tmp_path):
        """Un `deadline_ts` numérique est une anomalie : il ne doit PAS ouvrir
        l'échappatoire. Le verrou reste fermé tant que des workers tournent."""
        from src.reasoning.handlers import missions as M
        ctx, orch = _publish_ctx(tmp_path)
        lead_id, _ = _lead_with_children(tmp_path, orch)
        orch.set_task_metadata(lead_id, deadline_ts=time.time() - 10.0)
        ctx.runtime_task_id = lead_id
        r = await M.publish_mission_workspace_handler(ctx, target="out")
        assert not r.success
        assert "travaillent" in r.output

    @pytest.mark.asyncio
    async def test_vanished_child_treated_as_terminal(self, tmp_path):
        """Un id enfant purgé/introuvable ne bloque JAMAIS la publication."""
        from src.reasoning.handlers import missions as M
        ctx, orch = _publish_ctx(tmp_path)
        lead_id, _ = _lead_with_children(tmp_path, orch, n_children=0)
        orch.set_task_metadata(lead_id, children=["task_disparu_0000"])
        ctx.runtime_task_id = lead_id
        r = await M.publish_mission_workspace_handler(ctx, target="out")
        assert r.success, r.output


class TestPartialDelegationStopMessage:
    def test_stop_footer_names_the_lock(self):
        """Le texte STOP du résultat partiel (2.6.4b) doit annoncer le refus de
        publish — cohérence message ↔ verrou réel."""
        import inspect
        from src.reasoning.handlers import missions as M
        src = inspect.getsource(M.delegate_and_wait_handler)
        assert "RÉSULTAT PARTIEL" in src
        assert "publish_mission_workspace sera" in src
