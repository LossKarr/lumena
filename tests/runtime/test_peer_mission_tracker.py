"""Brique 3 (M3) — tests du suivi des missions sortantes + poll + notification."""
from __future__ import annotations

import io
import zipfile

import pytest

from src.runtime import peer_mission_tracker as mt


@pytest.fixture(autouse=True)
def _store(tmp_path):
    _orig = mt._TRACKER_FILE
    mt.reset_for_tests(tmp_path / "outbound_missions.json")
    yield
    mt._TRACKER_FILE = _orig  # restaure le chemin global (isolation inter-tests)


def _reg(task_id="t1", channel="web", peer_id="peerB"):
    return mt.register_outbound_mission(
        task_id=task_id, peer_id=peer_id, peer_name="Lumena-B",
        host="192.0.2.57", port=8081, objective="préparer un site", channel=channel,
    )


# ── CRUD / persistance ───────────────────────────────────────────────────────

def test_register_and_pending():
    _reg()
    pending = mt.list_pending()
    assert len(pending) == 1
    assert pending[0]["task_id"] == "t1"
    assert pending[0]["status"] == "queued"


def test_update_status_terminal_removes_from_pending():
    _reg()
    mt.update_status("t1", "completed", result="voici le site")
    assert mt.list_pending() == []
    m = mt.get_mission("t1")
    assert m["status"] == "completed" and m["result"] == "voici le site"


def test_persistence_across_reload():
    _reg()
    # Recharge depuis le disque (nouveau _load) → la mission est toujours là.
    assert mt.get_mission("t1") is not None


# ── Rappels web ──────────────────────────────────────────────────────────────

def test_web_reminders_only_non_telegram_unacked():
    _reg(task_id="web1", channel="web")
    _reg(task_id="tg1", channel="telegram")
    mt.update_status("web1", "completed", "ok")
    mt.update_status("tg1", "completed", "ok")
    reminders = mt.pending_web_reminders()
    ids = {r["task_id"] for r in reminders}
    assert ids == {"web1"}          # telegram exclu (déjà poussé)
    mt.ack_web_reminders(["web1"])
    assert mt.pending_web_reminders() == []


# ── Texte de notification ────────────────────────────────────────────────────

def test_completion_text_variants():
    m = _reg()
    m["status"] = "completed"; m["result"] = "fini !"
    assert "✅" in mt._build_completion_text(m)
    m["status"] = "interrupted"
    assert "interrompue" in mt._build_completion_text(m)
    m["status"] = "failed"
    assert "n'a pas pu finir" in mt._build_completion_text(m)


def test_completion_text_mentions_artifacts():
    m = _reg()
    m["status"] = "completed"; m["result"] = "ok"
    m["artifacts_count"] = 3; m["artifacts_dir"] = "/ws/inbound/peerB/t1"
    txt = mt._build_completion_text(m)
    assert "3 fichier" in txt and "/ws/inbound/peerB/t1" in txt


@pytest.mark.asyncio
async def test_fetch_mission_artifacts(monkeypatch, tmp_path):
    import hashlib
    import src.utils.paths as paths
    monkeypatch.setattr(paths, "WORKSPACE_DIR", tmp_path)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("site/index.html", "<html>")
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()

    class _Resp:
        def __init__(self, status, json_data=None, content=b""):
            self.status_code = status; self._j = json_data; self.content = content
        def json(self): return self._j

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, url, headers=None):
            if url.endswith("/manifest"):
                return _Resp(200, {"available": True, "kind": "zip", "filename": "t.zip",
                                   "sha256": sha, "count": 1})
            return _Resp(200, content=data)

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    # isole le project_registry (la réception enregistre le projet reçu)
    import src.utils.project_registry as _reg
    monkeypatch.setattr(_reg, "_REGISTRY_PATH", tmp_path / "registry.json")

    mission = {"task_id": "t1", "peer_id": "peerB", "peer_name": "Lumena-B",
               "host": "192.0.2.57", "port": 8081}
    peer = {"peer_token_outbound": "tok", "pairing_method": "fleet"}
    out = await mt.fetch_mission_artifacts(mission, peer)
    assert out["received"] is True and out["count"] == 1
    # réception LISIBLE : workspace/recu-de-<pair>/site/index.html (plus d'UUID/task)
    from src.runtime.peer_artifacts import reception_dir_for
    dest = reception_dir_for("Lumena-B")
    assert (dest / "site" / "index.html").read_text(encoding="utf-8") == "<html>"
    assert "inbound" not in str(dest)


@pytest.mark.asyncio
async def test_fetch_artifacts_no_token():
    out = await mt.fetch_mission_artifacts(
        {"task_id": "t", "peer_id": "p", "host": "h", "port": 1}, {})
    assert out["received"] is False and out["reason"] == "no_token"


# ── Notification routée ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notify_telegram_pushes(monkeypatch):
    sent = {}

    async def _fake_push(text):
        sent["text"] = text
        return True

    import src.autonomy.ops_handlers as ops
    monkeypatch.setattr(ops, "_notify_telegram_proactive", _fake_push)

    m = _reg(channel="telegram")
    m["status"] = "completed"; m["result"] = "site prêt"
    pushed = await mt.notify_mission_done(m)
    assert pushed is True
    assert "site prêt" in sent["text"]


@pytest.mark.asyncio
async def test_notify_web_no_push(monkeypatch):
    m = _reg(channel="web")
    m["status"] = "completed"
    pushed = await mt.notify_mission_done(m)
    assert pushed is False  # web → pas de push, reste en rappel


# ── Poll réseau (mocké) ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_marks_completed_and_notifies(monkeypatch):
    _reg(task_id="t1", peer_id="peerB", channel="telegram")

    # Registre : le pair a un token sortant.
    monkeypatch.setattr(
        "src.runtime.peer_network_autonomy._load_peers",
        lambda: {"peerB": {"instance_id": "peerB", "peer_token_outbound": "tok", "pairing_method": "fleet"}},
    )

    class _Resp:
        status_code = 200
        def json(self):
            return {"task_id": "t1", "status": "completed", "result": "terminé"}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, url, headers=None): return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    notified = {}
    async def _fake_notify(mission):
        notified["task_id"] = mission["task_id"]
        return True
    monkeypatch.setattr(mt, "notify_mission_done", _fake_notify)

    out = await mt.poll_outbound_missions(timeout=1.0)
    assert out["terminated"] == 1
    assert notified["task_id"] == "t1"
    assert mt.get_mission("t1")["status"] == "completed"
    assert mt.get_mission("t1")["notified"] is True
    # Re-poll : déjà notifié + terminal → plus rien.
    assert mt.poll_outbound_missions.__name__  # sanity
    assert mt.list_pending() == []


# ── Polish web — rappel injecté au prochain message ──────────────────────────

def test_inject_mission_reminders():
    from web.routes.chat import _inject_mission_reminders

    # Sans mission terminée → message inchangé.
    assert _inject_mission_reminders("salut") == "salut"

    # Mission web terminée → rappel préfixé + acquittement.
    _reg(task_id="m1", channel="web")
    mt.update_status("m1", "completed", result="le site est prêt")
    out = _inject_mission_reminders("ma question")
    assert "NOTE SYSTÈME" in out
    assert "le site est prêt" in out
    assert out.endswith("ma question")
    # Acquitté → plus de rappel la fois suivante.
    assert mt.pending_web_reminders() == []
    assert _inject_mission_reminders("autre") == "autre"


# ── P1/P2 — mission EN COURS injectée dans le contexte (anti re-délégation) ───

def test_inject_running_mission_warns_no_recreate():
    from web.routes.chat import _inject_mission_reminders
    _reg(task_id="r1", channel="web")  # statut 'queued' = en cours
    out = _inject_mission_reminders("alors ?")
    assert "EN COURS" in out
    assert "recrée pas" in out.lower()  # anti-doublon (P2)
    assert "relance pas" in out.lower()
    assert out.endswith("alors ?")
    # une mission EN COURS n'est PAS acquittée → persiste au message suivant
    assert "EN COURS" in _inject_mission_reminders("et maintenant ?")


def test_inject_completed_mission_shows_dest():
    from web.routes.chat import _inject_mission_reminders
    _reg(task_id="r2", channel="web")
    mt.update_status("r2", "completed", result="ok")
    mt.set_artifacts("r2", dest="workspace/recu-de-lumena-b/mini-horloge", count=3)
    out = _inject_mission_reminders("alors ?")
    assert "TERMINÉ" in out
    assert "workspace/recu-de-lumena-b/mini-horloge" in out  # chemin lisible remonté


def test_inject_running_not_acked():
    # P1 : injecter une mission en cours ne doit pas la faire disparaître
    _reg(task_id="r3", channel="web")
    from web.routes.chat import _inject_mission_reminders
    _inject_mission_reminders("alors ?")
    assert any(m["task_id"] == "r3" for m in mt.list_pending())


# ── M4 — détection du canal d'origine ────────────────────────────────────────

def test_detect_origin_channel():
    from src.reasoning.handlers.peer_tasks import _detect_origin_channel

    class _RT:
        channel = "telegram"

    class _Ctx:
        runtime_ctx = _RT()

    assert _detect_origin_channel(_Ctx()) == "telegram"
    assert _detect_origin_channel(None) == "web"          # défaut sûr
    assert _detect_origin_channel(object()) == "web"       # pas de runtime_ctx


@pytest.mark.asyncio
async def test_poll_tolerates_network_error(monkeypatch):
    _reg(task_id="t1", peer_id="peerB")
    monkeypatch.setattr(
        "src.runtime.peer_network_autonomy._load_peers",
        lambda: {"peerB": {"peer_token_outbound": "tok", "pairing_method": "fleet"}},
    )

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, url, headers=None): raise OSError("wifi coupé")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    out = await mt.poll_outbound_missions(timeout=1.0)  # ne lève pas
    assert out["terminated"] == 0
    assert mt.list_pending()[0]["task_id"] == "t1"  # toujours en cours → re-poll plus tard


# ── Fix #2 — suffixe de réception locale pour get_peer_task_status ────────────

class TestLocalReceptionSuffix:
    """Rend le « alors ? » fiable : statut distant + état de RÉCEPTION local."""

    def _suffix(self, task_id):
        from src.reasoning.handlers.peer_tasks import _local_reception_suffix
        return _local_reception_suffix(task_id)

    def test_no_mission_returns_empty(self):
        assert self._suffix("inconnu") == ""

    def test_running_mission_no_suffix(self):
        _reg(task_id="tA")
        assert self._suffix("tA") == ""

    def test_completed_without_artifacts_announces_pending(self):
        _reg(task_id="tB")
        mt.update_status("tB", "completed", result="ok")
        s = self._suffix("tB")
        assert "rapatriement" in s.lower() or "inbound" in s.lower()

    def test_completed_with_artifacts_shows_dest(self):
        _reg(task_id="tC")
        mt.update_status("tC", "completed", result="ok")
        mt.set_artifacts("tC", dest="workspace/inbound/peerB/tC", count=2)
        s = self._suffix("tC")
        assert "📦" in s and "2 fichier" in s and "workspace/inbound/peerB/tC" in s
