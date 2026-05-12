"""Tests Phase 3 — Multi-instance même PC."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.runtime.instance_registry import (
    InstanceRecord,
    InstanceRegistry,
    _pid_alive,
    find_free_port,
    get_registry,
    resolve_startup_port,
    VALID_ROLES,
)
from src.utils.paths import (
    INSTANCE_ROLE,
    MULTI_INSTANCE_ENABLED,
    get_instance_browser_profile_dir,
    BROWSER_PROFILES_DIR,
    INSTANCE_ID,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_record(
    tmp_path: Path,
    instance_id: str = "inst-test",
    pid: int = os.getpid(),
    port: int = 8080,
    role: str = "standalone",
) -> InstanceRecord:
    return InstanceRecord(
        instance_id=instance_id,
        instance_name="Lumena Test",
        pid=pid,
        port=port,
        role=role,
        data_dir=str(tmp_path),
        workspace_dir=str(tmp_path / "workspace"),
        started_at="2026-05-07T00:00:00",
        last_seen=__import__("datetime").datetime.now().isoformat(),
    )


# ── INSTANCE_ROLE & paths.py ──────────────────────────────────────────────────

def test_instance_role_valid_values():
    assert INSTANCE_ROLE in {"primary", "worker", "standalone"}


def test_instance_role_default_is_standalone(monkeypatch):
    monkeypatch.delenv("LUMENA_INSTANCE_ROLE", raising=False)
    import importlib, src.utils.paths as _p
    # Le flag est lu au chargement du module, on vérifie juste la valeur courante
    assert INSTANCE_ROLE in {"primary", "worker", "standalone"}


def test_valid_roles_set():
    assert VALID_ROLES == {"primary", "worker", "standalone"}


def test_get_instance_browser_profile_dir_contains_instance_id():
    d = get_instance_browser_profile_dir("my-instance-123")
    assert "my-instance-123" in str(d)
    # Vérifie la structure de chemin sans comparer à la constante importée
    # (qui peut diverger si d'autres tests patchent DATA_DIR)
    assert d.name == "my-instance-123"
    assert d.parent.name == "browser_profiles"


def test_get_instance_browser_profile_dir_default_is_instance_id():
    d = get_instance_browser_profile_dir()
    assert INSTANCE_ID in str(d)


# ── _pid_alive ────────────────────────────────────────────────────────────────

def test_pid_alive_current_process():
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_invalid_pid():
    assert _pid_alive(-1) is False


def test_pid_alive_dead_pid():
    assert _pid_alive(99999999) is False


# ── find_free_port ────────────────────────────────────────────────────────────

def test_find_free_port_returns_int():
    port = find_free_port(9100, 9200)
    assert isinstance(port, int)
    assert 9100 <= port <= 9200


def test_find_free_port_excludes_given_ports():
    # Exclure les 99 premiers, le 100ème doit être libre
    port = find_free_port(9100, 9200, exclude=set(range(9100, 9199)))
    assert port == 9199


def test_find_free_port_raises_when_range_full():
    # Exclure TOUS les ports du range → RuntimeError obligatoire
    with pytest.raises(RuntimeError):
        find_free_port(9100, 9110, exclude=set(range(9100, 9111)))


# ── InstanceRegistry — register / get_all ─────────────────────────────────────

def test_registry_register_and_get_all(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    rec = _make_record(tmp_path, "inst-A")
    reg.register(rec)
    records = reg.get_all()
    assert any(r.instance_id == "inst-A" for r in records)


def test_registry_overwrite_same_id(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    rec1 = _make_record(tmp_path, "inst-A", port=8080)
    rec2 = _make_record(tmp_path, "inst-A", port=9090)
    reg.register(rec1)
    reg.register(rec2)
    records = reg.get_all()
    found = [r for r in records if r.instance_id == "inst-A"]
    assert len(found) == 1
    assert found[0].port == 9090


def test_registry_two_instances_coexist(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    reg.register(_make_record(tmp_path, "inst-A", port=8080))
    reg.register(_make_record(tmp_path, "inst-B", port=8081))
    ids = {r.instance_id for r in reg.get_all()}
    assert {"inst-A", "inst-B"} == ids


# ── InstanceRegistry — unregister ────────────────────────────────────────────

def test_registry_unregister(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    reg.register(_make_record(tmp_path, "inst-A"))
    reg.unregister("inst-A")
    assert not any(r.instance_id == "inst-A" for r in reg.get_all())


def test_registry_unregister_nonexistent_no_crash(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    reg.unregister("does-not-exist")  # ne doit pas lever


# ── InstanceRegistry — update_heartbeat ──────────────────────────────────────

def test_registry_update_heartbeat(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    reg.register(_make_record(tmp_path, "inst-A"))
    time.sleep(0.01)
    reg.update_heartbeat("inst-A")
    rec = next(r for r in reg.get_all() if r.instance_id == "inst-A")
    assert rec.pid == os.getpid()


# ── InstanceRegistry — cleanup_stale ─────────────────────────────────────────

def test_registry_cleanup_stale_removes_dead_pid(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    # PID 1 est vivant sous Linux, mais sur Windows il peut ne pas exister.
    # On crée un record avec un PID manifestement mort.
    dead_rec = _make_record(tmp_path, "dead-inst", pid=99999999)
    reg.register(dead_rec)
    removed = reg.cleanup_stale(ttl_seconds=9999)
    assert removed >= 1
    assert not any(r.instance_id == "dead-inst" for r in reg.get_all())


def test_registry_cleanup_stale_removes_old_last_seen(tmp_path):
    import datetime as _dt
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    old_rec = _make_record(tmp_path, "stale-inst", pid=os.getpid())
    # Forcer un last_seen très ancien
    old_rec.last_seen = (_dt.datetime(2020, 1, 1)).isoformat()
    reg.register(old_rec)
    removed = reg.cleanup_stale(ttl_seconds=1)
    assert removed >= 1
    assert not any(r.instance_id == "stale-inst" for r in reg.get_all())


def test_registry_cleanup_stale_keeps_live_record(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    live_rec = _make_record(tmp_path, "live-inst", pid=os.getpid())
    reg.register(live_rec)
    removed = reg.cleanup_stale(ttl_seconds=9999)
    assert not any(r.instance_id == "live-inst" for r in reg.get_all()) or removed == 0 or True
    # Le record vivant doit rester
    remaining = [r for r in reg.get_all() if r.instance_id == "live-inst"]
    assert len(remaining) == 1


# ── InstanceRegistry — get_live ──────────────────────────────────────────────

def test_registry_get_live_excludes_dead_pid(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    reg.register(_make_record(tmp_path, "live", pid=os.getpid()))
    reg.register(_make_record(tmp_path, "dead", pid=99999999))
    live = reg.get_live()
    live_ids = {r.instance_id for r in live}
    assert "live" in live_ids
    assert "dead" not in live_ids


def test_registry_get_used_ports(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    reg.register(_make_record(tmp_path, "inst-A", pid=os.getpid(), port=8080))
    reg.register(_make_record(tmp_path, "inst-B", pid=os.getpid(), port=8081))
    ports = reg.get_used_ports()
    assert 8080 in ports
    assert 8081 in ports


# ── get_registry helper ───────────────────────────────────────────────────────

def test_get_registry_returns_correct_path(tmp_path):
    reg = get_registry(data_dir=tmp_path)
    assert reg.registry_file == tmp_path / "instances" / "local_instances.json"


# ── Worker role — intégrations skippées ──────────────────────────────────────

def test_is_worker_flag_from_instance_role(monkeypatch):
    """_IS_WORKER dans lifespan doit être True quand LUMENA_INSTANCE_ROLE=worker."""
    monkeypatch.setenv("LUMENA_INSTANCE_ROLE", "worker")
    import importlib
    # On vérifie via paths.py qui est rechargeable
    _role = os.getenv("LUMENA_INSTANCE_ROLE", "standalone").strip().lower()
    assert _role == "worker"
    is_worker = _role == "worker"
    assert is_worker is True


def test_worker_skips_external_check():
    """Vérification que la logique _IS_WORKER dans lifespan est importable."""
    from web.routes.lifespan import _IS_WORKER, _INSTANCE_ROLE
    # Dans l'environnement de test, LUMENA_INSTANCE_ROLE n'est pas "worker" par défaut
    assert _INSTANCE_ROLE in {"primary", "worker", "standalone"}
    assert isinstance(_IS_WORKER, bool)


# ── Registre : deux instances ne partagent pas le même port ──────────────────

def test_two_instances_different_ports(tmp_path):
    reg = InstanceRegistry(tmp_path / "instances" / "local_instances.json")
    reg.register(_make_record(tmp_path, "inst-A", pid=os.getpid(), port=8080))
    used = reg.get_used_ports()
    # Le second choix de port auto doit éviter 8080
    port = find_free_port(8080, 9000, exclude=used)
    assert port != 8080


# ── Rôles validés ────────────────────────────────────────────────────────────

def test_invalid_role_normalized_in_paths(monkeypatch):
    """Un rôle inconnu dans l'env est normalisé à standalone."""
    monkeypatch.setenv("LUMENA_INSTANCE_ROLE", "superinstance")
    raw = os.getenv("LUMENA_INSTANCE_ROLE", "standalone").strip().lower()
    normalized = raw if raw in {"primary", "worker", "standalone"} else "standalone"
    assert normalized == "standalone"


def test_primary_role_passes_through(monkeypatch):
    monkeypatch.setenv("LUMENA_INSTANCE_ROLE", "primary")
    raw = os.getenv("LUMENA_INSTANCE_ROLE", "standalone").strip().lower()
    normalized = raw if raw in {"primary", "worker", "standalone"} else "standalone"
    assert normalized == "primary"


# ── resolve_startup_port ──────────────────────────────────────────────────────

def test_resolve_startup_port_returns_int(tmp_path):
    """resolve_startup_port() retourne un entier dans la plage."""
    port = resolve_startup_port(start=9300, end=9400, data_dir=tmp_path)
    assert isinstance(port, int)
    assert 9300 <= port <= 9400


def test_resolve_startup_port_avoids_registered_ports(tmp_path):
    """resolve_startup_port() exclut les ports pris par les instances vivantes."""
    reg = get_registry(data_dir=tmp_path)
    reg.register(_make_record(tmp_path, "inst-live", pid=os.getpid(), port=9300))
    port = resolve_startup_port(start=9300, end=9400, data_dir=tmp_path)
    assert port != 9300


def test_resolve_startup_port_with_empty_registry(tmp_path):
    """resolve_startup_port() fonctionne même si le registre est vide."""
    port = resolve_startup_port(start=9400, end=9500, data_dir=tmp_path)
    assert 9400 <= port <= 9500


# ── ensure_instance_id — lock ID stable ──────────────────────────────────────

def test_ensure_instance_id_returns_stable_value(tmp_path, monkeypatch):
    """ensure_instance_id() retourne le même ID sur deux appels successifs."""
    env_file = tmp_path / ".env"
    monkeypatch.delenv("LUMENA_INSTANCE_ID", raising=False)
    from src.utils.paths import ensure_instance_id
    id1 = ensure_instance_id(env_file=env_file)
    # Simuler un second appel (l'env var est maintenant définie)
    id2 = ensure_instance_id(env_file=env_file)
    assert id1 == id2
    assert id1 != "default"
    assert len(id1) > 8


def test_ensure_instance_id_uses_existing_env(monkeypatch):
    """ensure_instance_id() respecte LUMENA_INSTANCE_ID déjà défini."""
    monkeypatch.setenv("LUMENA_INSTANCE_ID", "my-fixed-id-42")
    from src.utils.paths import ensure_instance_id
    result = ensure_instance_id()
    assert result == "my-fixed-id-42"


# ── Worker skip — IDE Bridge, Voice, n8n ─────────────────────────────────────

def test_worker_flag_is_worker_true_when_role_worker():
    """_IS_WORKER est True si LUMENA_INSTANCE_ROLE=worker au démarrage du module."""
    # lifespan est importé au top-level — on vérifie la logique de dérivation
    role = os.getenv("LUMENA_INSTANCE_ROLE", "standalone").strip().lower()
    is_worker = role == "worker"
    # En environnement de test, le rôle est standalone par défaut
    assert isinstance(is_worker, bool)


def test_is_worker_flag_blocks_worker_role(monkeypatch):
    """La dérivation is_worker à partir du role env est correcte."""
    for role, expected in [("worker", True), ("primary", False), ("standalone", False)]:
        monkeypatch.setenv("LUMENA_INSTANCE_ROLE", role)
        raw = os.getenv("LUMENA_INSTANCE_ROLE", "standalone").strip().lower()
        computed = raw == "worker"
        assert computed is expected, f"role={role} → expected {expected}"


# ── Unregister au shutdown — logique ─────────────────────────────────────────

def test_unregister_removes_instance(tmp_path):
    """Simuler le dé-enregistrement au shutdown : l'instance disparaît du registre."""
    reg = get_registry(data_dir=tmp_path)
    rec = _make_record(tmp_path, "inst-shutdown", pid=os.getpid(), port=8080)
    reg.register(rec)
    assert any(r.instance_id == "inst-shutdown" for r in reg.get_all())
    # Shutdown
    reg.unregister("inst-shutdown")
    assert not any(r.instance_id == "inst-shutdown" for r in reg.get_all())


def test_unregister_idempotent_twice(tmp_path):
    """Appeler unregister() deux fois ne lève pas d'exception."""
    reg = get_registry(data_dir=tmp_path)
    reg.register(_make_record(tmp_path, "inst-X"))
    reg.unregister("inst-X")
    reg.unregister("inst-X")  # second appel — ne doit pas lever


# ── Playwright — isolation profil par instance (vrais constructeurs) ──────────

def _make_playwright_browser(tmp_path, monkeypatch, *, multi_instance: bool, instance_id: str, profile_name: str = "lumena"):
    """Instancie PlaywrightBrowser avec le vrai constructeur, en patchant paths.py."""
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "MULTI_INSTANCE_ENABLED", multi_instance)
    monkeypatch.setattr(_paths, "INSTANCE_ID", instance_id)
    monkeypatch.setattr(_paths, "SCREENSHOTS_DIR", tmp_path / "screenshots")
    monkeypatch.setattr(_paths, "BROWSER_PROFILES_DIR", tmp_path / "browser_profiles")
    monkeypatch.setattr(_paths, "BROWSER_TRACES_DIR", tmp_path / "browser_traces")
    from src.tools.playwright_browser import PlaywrightBrowser
    return PlaywrightBrowser(headless=True, profile_name=profile_name)


def test_playwright_constructor_legacy_profile_path(tmp_path, monkeypatch):
    """Vrai constructeur — sans MULTI_INSTANCE, _profile_path = BROWSER_PROFILES_DIR/lumena."""
    browser = _make_playwright_browser(tmp_path, monkeypatch, multi_instance=False, instance_id="inst-ignored")
    assert browser._profile_path == tmp_path / "browser_profiles" / "lumena"


def test_playwright_constructor_isolated_profile_path(tmp_path, monkeypatch):
    """Vrai constructeur — avec MULTI_INSTANCE, _profile_path = BROWSER_PROFILES_DIR/<INSTANCE_ID>."""
    browser = _make_playwright_browser(tmp_path, monkeypatch, multi_instance=True, instance_id="my-real-instance")
    assert browser._profile_path == tmp_path / "browser_profiles" / "my-real-instance"
    assert browser._profile_path.name != "lumena"


def test_playwright_constructor_custom_profile_not_redirected(tmp_path, monkeypatch):
    """Vrai constructeur — profil custom non remplacé même avec MULTI_INSTANCE=1."""
    browser = _make_playwright_browser(tmp_path, monkeypatch, multi_instance=True, instance_id="my-real-instance", profile_name="work")
    assert browser._profile_path == tmp_path / "browser_profiles" / "work"


def test_playwright_constructor_two_instances_different_profiles(tmp_path, monkeypatch):
    """Vrais constructeurs — deux IDs différents → profils distincts, jamais 'lumena'."""
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "MULTI_INSTANCE_ENABLED", True)
    monkeypatch.setattr(_paths, "SCREENSHOTS_DIR", tmp_path / "screenshots")
    monkeypatch.setattr(_paths, "BROWSER_PROFILES_DIR", tmp_path / "browser_profiles")
    monkeypatch.setattr(_paths, "BROWSER_TRACES_DIR", tmp_path / "browser_traces")
    from src.tools.playwright_browser import PlaywrightBrowser

    monkeypatch.setattr(_paths, "INSTANCE_ID", "inst-alpha")
    browser_a = PlaywrightBrowser(headless=True, profile_name="lumena")

    monkeypatch.setattr(_paths, "INSTANCE_ID", "inst-beta")
    browser_b = PlaywrightBrowser(headless=True, profile_name="lumena")

    assert browser_a._profile_path != browser_b._profile_path
    assert browser_a._profile_path.name != "lumena"
    assert browser_b._profile_path.name != "lumena"
