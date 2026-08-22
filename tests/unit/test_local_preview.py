"""Preview locale contrôlée (P1, 2026-07-02) — cf. run todolist.

L'agent a servi sur `http.server 8081` (hors allowlist statique) → `localhost:8081`
bloqué par le SSRF guard → 20 itérations de contournement, vérif DOM jamais faite.
On autorise UNIQUEMENT les previews loopback que Lumena a délibérément servies :
pas d'allow-localhost large, jamais l'IP LAN.
"""
import pytest

import src.utils.local_preview as lp
import src.utils.url_safety as us


@pytest.fixture(autouse=True)
def _clean_registry():
    lp.clear_previews()
    yield
    lp.clear_previews()


# ── registry : register / unregister / list ───────────────────────────────────

def test_register_and_is_allowed():
    assert lp.register_preview(8081, workspace="workspace/todolist", task_id="task_x") is True
    assert lp.is_preview_allowed("localhost", 8081) is True
    assert lp.is_preview_allowed("127.0.0.1", 8081) is True
    assert lp.is_preview_allowed("::1", 8081) is True
    # port non enregistré → refusé
    assert lp.is_preview_allowed("localhost", 9999) is False


def test_unregister():
    lp.register_preview(8081)
    assert lp.is_preview_allowed("localhost", 8081) is True
    assert lp.unregister_preview(8081) is True
    assert lp.is_preview_allowed("localhost", 8081) is False
    assert lp.unregister_preview(8081) is False  # déjà retiré


def test_lan_ip_never_allowed_even_if_registered():
    # Garde-fou absolu : l'IP LAN n'est JAMAIS une preview, même port enregistré.
    lp.register_preview(8081)
    assert lp.is_preview_allowed("192.168.1.166", 8081) is False
    assert lp.is_preview_allowed("10.0.0.5", 8081) is False
    assert lp.is_preview_allowed("example.com", 8081) is False


def test_bad_ports_rejected():
    assert lp.register_preview("abc") is False
    assert lp.register_preview(0) is False
    assert lp.register_preview(70000) is False
    assert lp.is_preview_allowed("localhost", None) is False


def test_list_previews_copy():
    lp.register_preview(8081, workspace="ws", task_id="t")
    snap = lp.list_previews()
    assert snap[8081]["workspace"] == "ws" and snap[8081]["task_id"] == "t"
    snap[8081]["workspace"] = "muté"  # ne doit pas affecter le registry interne
    assert lp.list_previews()[8081]["workspace"] == "ws"


# ── url_safety : intégration SSRF guard ───────────────────────────────────────

def test_assert_url_safe_allows_registered_loopback_port():
    # Avant enregistrement : 8081 hors allowlist statique → bloqué.
    with pytest.raises(ValueError):
        us.assert_url_safe("http://localhost:8081/")
    # Après enregistrement : autorisé (preview contrôlée).
    lp.register_preview(8081)
    us.assert_url_safe("http://localhost:8081/")          # ne lève pas
    us.assert_url_safe("http://127.0.0.1:8081/index.html")  # ne lève pas


def test_assert_url_safe_static_allowlist_still_works():
    # Non-régression : les ports dev conventionnels restent autorisés sans registry.
    us.assert_url_safe("http://localhost:3000/")
    us.assert_url_safe("http://localhost:8000/")


def test_assert_url_safe_blocks_lumena_control_port():
    # LOT E (run CéramiShop) : 8080 est le port de CONTRÔLE de Lumena — retiré de
    # l'allowlist, l'agent ne peut plus atteindre sa propre UI/API.
    import pytest as _pytest
    with _pytest.raises(ValueError, match="réservé Lumena"):
        us.assert_url_safe("http://localhost:8080/")


def test_start_stop_preview_server_registers_bound_port(tmp_path):
    # Boundary authoritatif : start_preview_server enregistre le port RÉELLEMENT
    # lié (pas le port demandé), stop_preview_server le désenregistre. Cf. note de
    # revue P1 — l'enregistrement vit au cycle de vie serveur, pas au handler.
    wb = pytest.importorskip("src.tools.website_builder")
    (tmp_path / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    res = wb.start_preview_server(tmp_path, port=8090)
    bound = None
    try:
        assert res.get("success") is True, res
        bound = res["port"]
        assert lp.is_preview_allowed("localhost", bound) is True
        # …et le SSRF guard laisse donc passer cette preview.
        us.assert_url_safe(f"http://localhost:{bound}/")
    finally:
        wb.stop_preview_server()
    # Après stop → désenregistré.
    if bound is not None:
        assert lp.is_preview_allowed("localhost", bound) is False


def test_assert_url_safe_still_blocks_lan_and_external_and_unregistered():
    lp.register_preview(8081)
    # IP LAN : bloquée même si 8081 est enregistré (preview = loopback only).
    with pytest.raises(ValueError):
        us.assert_url_safe("http://192.168.1.166:8081/")
    # port loopback non enregistré → bloqué.
    with pytest.raises(ValueError):
        us.assert_url_safe("http://localhost:7777/")
    # metadata cloud → toujours bloqué.
    with pytest.raises(ValueError):
        us.assert_url_safe("http://169.254.169.254/latest/meta-data/")
