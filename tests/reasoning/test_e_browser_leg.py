"""LOT E (run CéramiShop 2026-07-04) — la jambe « navigateur exécuté ».

Le run CéramiShop a prouvé la moitié LIVRAISON (contrat → workers → tests verts →
publication lisible), mais la moitié VÉRIFICATION a cassé : le serveur Flask de la
mission (`flask run --port 8081`) n'était pas reconnu comme preview → SSRF a bloqué
127.0.0.1:8081 → le modèle a FABRIQUÉ le check navigateur (le plan l'a coché sur une
pensée, avant tout browser_navigate).

E.0  ports de contrôle Lumena = source de vérité (8080 web, 8245 IDE, Ollama) ;
E.1  register_preview des serveurs applicatifs mission (flask/uvicorn/…), REFUS des
     ports réservés ;
E.1b 8080 retiré de l'allowlist SSRF + refus des réservés en tête (l'agent ne peut
     plus atteindre l'UI de Lumena) ;
E.2  garde BROWSER-ONLY : « vérifier le navigateur » cochée QUE par une action
     browser_* réelle ;
E.3b publication propre : les *.bak* d'auto-backup ne polluent plus le livrable.
"""
from __future__ import annotations

import inspect

import pytest

from src.reasoning.plan_progress import browser_verify_task_blocks
from src.utils import local_preview as lp
from src.utils.url_safety import assert_url_safe


@pytest.fixture(autouse=True)
def _clean_previews():
    lp.clear_previews()
    yield
    lp.clear_previews()


# ── E.0 : ports réservés = source de vérité ──────────────────────────────────────

def test_reserved_ports_defaults():
    r = lp.reserved_lumena_ports()
    assert 8080 in r   # serveur web Lumena
    assert 8245 in r   # IDE bridge
    assert 11434 in r  # Ollama


def test_reserved_ports_follow_env(monkeypatch):
    monkeypatch.setenv("LUMENA_PORT", "9090")
    monkeypatch.setenv("LUMENA_IDE_WS_PORT", "9091")
    r = lp.reserved_lumena_ports()
    assert 9090 in r and 9091 in r


# ── E.1 : register_preview refuse les réservés ───────────────────────────────────

def test_register_preview_accepts_mission_port():
    assert lp.register_preview(8085, workspace="w", task_id="t") is True
    assert lp.is_preview_allowed("127.0.0.1", 8085) is True


def test_register_preview_refuses_reserved():
    assert lp.register_preview(8080) is False   # port web de Lumena
    assert lp.register_preview(8245) is False   # IDE bridge
    assert lp.is_preview_allowed("127.0.0.1", 8080) is False


def test_is_preview_allowed_refuses_reserved_even_if_forced(monkeypatch):
    """Défense en profondeur : même injecté dans le registre, un port réservé
    n'est jamais 'allowed'."""
    with lp._lock:
        lp._previews[8080] = {"workspace": "", "task_id": ""}
    assert lp.is_preview_allowed("127.0.0.1", 8080) is False


def test_is_preview_allowed_rejects_lan_host():
    lp.register_preview(8085)
    assert lp.is_preview_allowed("192.168.1.10", 8085) is False  # jamais l'IP LAN


# ── E.1b : SSRF guard — Lumena inatteignable, preview mission OK ──────────────────

def test_ssrf_blocks_lumena_control_port():
    """127.0.0.1:8080 = plan de contrôle de Lumena → toujours interdit."""
    with pytest.raises(ValueError, match="réservé Lumena"):
        assert_url_safe("http://127.0.0.1:8080/")
    with pytest.raises(ValueError, match="réservé Lumena"):
        assert_url_safe("http://localhost:8245/")


def test_ssrf_allows_registered_mission_preview():
    lp.register_preview(8085)
    assert_url_safe("http://127.0.0.1:8085/index.html")  # ne lève pas


def test_ssrf_blocks_unregistered_loopback_port():
    with pytest.raises(ValueError, match="interdit|privé"):
        assert_url_safe("http://127.0.0.1:8099/")  # jamais servi/enregistré


def test_ssrf_static_dev_ports_still_ok():
    """Les ports dev communs restent ouverts (8000), mais 8080 a disparu."""
    assert_url_safe("http://127.0.0.1:8000/")  # ne lève pas
    assert 8080 not in __import__("src.utils.url_safety", fromlist=["_SSRF_ALLOWED_LOCAL_PORTS"])._SSRF_ALLOWED_LOCAL_PORTS


def test_ssrf_external_still_ok():
    assert_url_safe("https://example.com/")  # externe légitime inchangé


# ── E.1 : détection des serveurs applicatifs dans run_command (structurel) ───────

def test_system_registers_app_servers():
    import src.reasoning.handlers.system as sys_mod
    src = inspect.getsource(sys_mod)
    i = src.find("_is_app_srv = (")
    assert i > 0, "bloc de détection des serveurs applicatifs absent"
    block = src[i:i + 1500]
    for sig in ("flask run", "uvicorn", "gunicorn"):
        assert sig in block, f"serveur applicatif '{sig}' non reconnu comme preview"
    assert "5000 if" in block  # défaut Flask sans --port explicite
    assert "register_preview" in block


# ── E.2 : garde BROWSER-ONLY ─────────────────────────────────────────────────────

def test_browser_verify_task_blocked_for_non_browser_tools():
    """Le cas CéramiShop figé : « Vérifier le navigateur » cochée sans browser_*."""
    desc = "vérifier le navigateur : passer une commande et vérifier l'admin"
    assert browser_verify_task_blocks("read_file", desc)
    assert browser_verify_task_blocks("read_files_batch", desc)
    assert browser_verify_task_blocks("run_command", desc)


def test_browser_verify_task_allowed_for_browser_tools():
    desc = "vérifier le navigateur"
    assert not browser_verify_task_blocks("browser_navigate", desc)
    assert not browser_verify_task_blocks("browser_click_index", desc)
    # le vérificateur runtime dédié est aussi préfixé browser_ → compté au ledger
    assert not browser_verify_task_blocks("browser_verify_local_project", desc)


def test_browser_verify_guard_ignores_non_browser_tasks():
    """Une tâche sans intention navigateur n'est jamais bloquée par ce garde."""
    assert not browser_verify_task_blocks("read_file", "lire le contrat de mission")
    assert not browser_verify_task_blocks("run_command", "lancer pytest")
    # « navigateur » sans verbe de vérif (nom de livrable) → pas bloqué
    assert not browser_verify_task_blocks("write_file", "écrire le guide du navigateur")


def test_browser_verify_guard_wired_both_chains():
    """Lot RF-4 du refactor ReAct (2026-08-27) : le corps de
    `_update_plan_progress` a quitté `react.py` pour
    `src/reasoning/react_plan_runtime.py`. Les deux branchements du garde
    BROWSER-ONLY y ont suivi ; aucun n'a été perdu.

    Preuve COMPORTEMENTALE équivalente exigée par le plan avant ce repointage —
    elle existe et elle est plus forte, car elle vérifie que le garde REFUSE
    vraiment au lieu de compter des occurrences :
      tests/reasoning/test_rf4_plan_runtime_extraction.py
        - test_comportement_le_garde_browser_only_refuse_par_la_chaine_principale
    """
    import src.reasoning.react_plan_runtime as plan_runtime_mod
    src = inspect.getsource(plan_runtime_mod)
    assert src.count("browser_verify_task_blocks(tool_name") == 2, (
        "le garde BROWSER-ONLY doit être branché aux 2 chaînes (principale + fallback)")
    assert "[PLAN] Guard BROWSER-ONLY" in src


# ── E.3b : publication propre (pas de *.bak*) ────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_excludes_backup_files(tmp_path):
    import types
    from src.runtime.task_orchestrator import TaskOrchestrator
    from src.reasoning.handlers import missions as M
    from src.tools.file_guardrails import WorkspaceFileGuardrails

    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    fg = WorkspaceFileGuardrails(tmp_path)
    lead = orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead",
                           metadata={"kind": "mission", "depth": 1})
    ctx = types.SimpleNamespace(lumena=core, runtime_task_id=lead.task_id,
                                file_guardrails=fg)
    d = tmp_path / "missions" / lead.task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "orders.py").write_text("def create_order(): return 1\n", encoding="utf-8")
    (d / "orders.py.bak_062053").write_text("stub\n", encoding="utf-8")  # backup d'édition
    (d / "scratch.tmp").write_text("x\n", encoding="utf-8")
    (d / "contract.json").write_text('{"project": "shop", "files": []}', encoding="utf-8")

    r = await M.publish_mission_workspace_handler(ctx, target="shop")
    assert r.success, r.output
    dest = tmp_path / "shop"
    assert (dest / "orders.py").is_file()               # le vrai code publié
    assert not (dest / "orders.py.bak_062053").exists()  # backup EXCLU
    assert not (dest / "scratch.tmp").exists()           # temp EXCLU
    assert "orders.py.bak" not in r.output               # pas listé non plus
