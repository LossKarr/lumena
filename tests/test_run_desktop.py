import run_desktop
import urllib.error


class FakeWindow:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)


def test_desktop_zoom_default(monkeypatch):
    monkeypatch.delenv("LUMENA_DESKTOP_ZOOM", raising=False)
    assert run_desktop._desktop_zoom() == 0.90


def test_desktop_zoom_clamped(monkeypatch):
    monkeypatch.setenv("LUMENA_DESKTOP_ZOOM", "2")
    assert run_desktop._desktop_zoom() == 1.25

    monkeypatch.setenv("LUMENA_DESKTOP_ZOOM", "0.1")
    assert run_desktop._desktop_zoom() == 0.67


def test_desktop_zoom_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("LUMENA_DESKTOP_ZOOM", "nope")
    assert run_desktop._desktop_zoom() == 0.90


def test_desktop_splash_enabled_default(monkeypatch):
    monkeypatch.delenv("LUMENA_DESKTOP_SPLASH", raising=False)
    assert run_desktop._desktop_splash_enabled() is True


def test_desktop_splash_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LUMENA_DESKTOP_SPLASH", "0")
    assert run_desktop._desktop_splash_enabled() is False
    assert run_desktop._create_desktop_splash() is None


def test_create_desktop_splash_noops_when_tkinter_unavailable(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tkinter":
            raise ImportError("tk unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setenv("LUMENA_DESKTOP_SPLASH", "1")
    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert run_desktop._create_desktop_splash() is None


def test_wait_for_server_pumps_splash_tick(monkeypatch):
    ticks = []
    monotonic_values = iter([0.0, 0.1, 0.7])

    monkeypatch.setattr(run_desktop.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(run_desktop.time, "sleep", lambda _seconds: None)

    import urllib.request

    def fail_urlopen(*_args, **_kwargs):
        raise urllib.error.URLError("not ready")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    assert run_desktop._wait_for_server(8080, timeout=0.2, tick=lambda: ticks.append("tick")) is False
    assert ticks


def test_apply_desktop_zoom_uses_compensated_scale(monkeypatch):
    monkeypatch.setenv("LUMENA_DESKTOP_ZOOM", "0.9")
    window = FakeWindow()

    run_desktop._apply_desktop_zoom(window)

    assert len(window.scripts) == 1
    script = window.scripts[0]
    assert 'style.removeProperty("zoom")' in script
    assert ".style.zoom =" not in script
    assert "body.style.transform" in script
    assert "100 / zoom" in script
    assert ".shell" in script
    assert "calc(100vh / \" + zoom + \")" in script


# ── Delai de demarrage proportionnel aux serveurs MCP (24/09/2026) ──────────
#
# Mesure du boot reel, journal de 02:48:05 a 02:49:11 :
#   boot complet        66 s
#   dont 9 MCP          38 s   -> ~4,2 s par serveur (process Node + handshake)
#   le reste            ~28 s  (ChromaDB, 611 handlers, 35 skills, canaux)
#
# Le plafond fixe de 90 s ne laissait que 24 s de marge : depasse vers 14 serveurs.
# Et le symptome est le pire possible - Lumena ne demarre pas, sans dire pourquoi.
# Le nombre de serveurs est un FAIT lisible sur le disque avant le demarrage : on
# s'en sert, au lieu de relever une constante en attendant le prochain depassement.


def _catalogue(tmp_path, statuts):
    import json
    dossier = tmp_path / "data" / "mcp_server_catalog" / "servers"
    dossier.mkdir(parents=True)
    for i, statut in enumerate(statuts):
        (dossier / f"s{i}.json").write_text(
            json.dumps({"entry": {"server_id": f"s{i}", "status": statut}}), encoding="utf-8")
    return tmp_path


def test_seuls_les_serveurs_reellement_demarres_sont_comptes(monkeypatch, tmp_path):
    """`removed` ne demarre pas : le compter gonflerait le delai pour rien."""
    racine = _catalogue(tmp_path, ["active", "active", "installed", "removed", "removed", None])
    monkeypatch.setattr(run_desktop, "__file__", str(racine / "run_desktop.py"))
    assert run_desktop._mcp_a_demarrer() == 3


def test_le_delai_augmente_avec_le_nombre_de_mcp(monkeypatch, tmp_path):
    """Le coeur du correctif : 9 serveurs ne doivent plus tenir dans 90 s."""
    monkeypatch.delenv("LUMENA_DESKTOP_BOOT_TIMEOUT", raising=False)
    racine = _catalogue(tmp_path, ["active"] * 9)
    monkeypatch.setattr(run_desktop, "__file__", str(racine / "run_desktop.py"))
    delai = run_desktop._boot_timeout()
    assert delai == 150, delai
    assert delai > 90, "le delai n'a pas augmente : le defaut mesure reviendrait"


def test_le_plancher_de_90s_est_garanti(monkeypatch, tmp_path):
    """Sans aucun MCP, on ne descend pas sous l'ancienne valeur : jamais de regression."""
    monkeypatch.delenv("LUMENA_DESKTOP_BOOT_TIMEOUT", raising=False)
    racine = _catalogue(tmp_path, [])
    monkeypatch.setattr(run_desktop, "__file__", str(racine / "run_desktop.py"))
    assert run_desktop._boot_timeout() == 90


def test_le_plafond_borne_un_blocage(monkeypatch, tmp_path):
    """Au-dela, ce n'est plus un boot lent : inutile de faire attendre 20 minutes."""
    monkeypatch.delenv("LUMENA_DESKTOP_BOOT_TIMEOUT", raising=False)
    racine = _catalogue(tmp_path, ["active"] * 200)
    monkeypatch.setattr(run_desktop, "__file__", str(racine / "run_desktop.py"))
    assert run_desktop._boot_timeout() == 600


def test_un_catalogue_absent_ne_bloque_pas_le_demarrage(monkeypatch, tmp_path):
    """Le calcul d'un delai d'attente ne doit JAMAIS empecher de demarrer."""
    monkeypatch.delenv("LUMENA_DESKTOP_BOOT_TIMEOUT", raising=False)
    monkeypatch.setattr(run_desktop, "__file__", str(tmp_path / "run_desktop.py"))
    assert run_desktop._mcp_a_demarrer() == 0
    assert run_desktop._boot_timeout() == 90


def test_un_fichier_illisible_est_ignore_sans_tout_perdre(monkeypatch, tmp_path):
    """Une entree corrompue ne doit pas faire retomber le compte a zero."""
    racine = _catalogue(tmp_path, ["active", "active"])
    (racine / "data" / "mcp_server_catalog" / "servers" / "casse.json").write_text(
        "{ pas du json", encoding="utf-8")
    monkeypatch.setattr(run_desktop, "__file__", str(racine / "run_desktop.py"))
    assert run_desktop._mcp_a_demarrer() == 2


def test_la_variable_denvironnement_reste_souveraine(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMENA_DESKTOP_BOOT_TIMEOUT", "45")
    racine = _catalogue(tmp_path, ["active"] * 9)
    monkeypatch.setattr(run_desktop, "__file__", str(racine / "run_desktop.py"))
    assert run_desktop._boot_timeout() == 45


def test_une_variable_illisible_retombe_sur_le_calcul(monkeypatch, tmp_path):
    """Refuser de demarrer a cause d'une variable mal tapee serait absurde."""
    monkeypatch.setenv("LUMENA_DESKTOP_BOOT_TIMEOUT", "beaucoup")
    racine = _catalogue(tmp_path, ["active"] * 9)
    monkeypatch.setattr(run_desktop, "__file__", str(racine / "run_desktop.py"))
    assert run_desktop._boot_timeout() == 150


def test_le_calcul_ne_charge_jamais_le_coeur_de_lumena():
    """Charger Lumena pour mesurer son temps de chargement serait un comble.

    `run_desktop` est un point d'entree : le calcul doit rester en bibliotheque
    standard, sinon on paie precisement ce qu'on cherche a borner.
    """
    import inspect
    source = inspect.getsource(run_desktop._mcp_a_demarrer)
    for interdit in ("from src", "import src", "tool_registry", "LumenaCore"):
        assert interdit not in source, f"import lourd dans le calcul du delai : {interdit}"
