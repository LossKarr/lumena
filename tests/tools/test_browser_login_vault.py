"""W1 — Login autonome : normalisation de domaine, stockage au coffre (simulé),
masquage du mot de passe, et non-exposition des valeurs.

Import de playwright_browser différé dans les fonctions/fixtures (évite le flake
d'ordre d'import sur MAX_TABS lié au chargement de .env).
"""
import pytest


class _FakeSecrets:
    """Coffre en mémoire (évite d'écrire dans le vrai store chiffré)."""
    def __init__(self):
        self.store = {}

    def set(self, scope, key, value):
        if value == "":
            self.store.pop((scope, key), None)
        else:
            self.store[(scope, key)] = value

    def get(self, scope, key):
        return self.store.get((scope, key))

    def delete(self, scope, key):
        return self.store.pop((scope, key), None) is not None

    def list_keys(self, scope):
        return sorted(k for (s, k) in self.store if s == scope)


@pytest.fixture
def fake_browser(monkeypatch):
    from src.tools.playwright_browser import PlaywrightBrowser
    b = PlaywrightBrowser()
    fake = _FakeSecrets()
    monkeypatch.setattr(PlaywrightBrowser, "_secrets", staticmethod(lambda: fake))
    return b, fake


@pytest.mark.parametrize("raw,expected", [
    ("https://www.github.com/login", "github.com"),
    ("http://example.com/path?x=1", "example.com"),
    ("www.LeMonde.fr", "lemonde.fr"),
    ("github.com", "github.com"),
    ("https://app.site.io:8443/x", "app.site.io:8443"),
])
def test_login_domain_normalise(raw, expected):
    from src.tools.playwright_browser import PlaywrightBrowser
    assert PlaywrightBrowser._login_domain(raw) == expected


def test_save_login_masque_le_mot_de_passe(fake_browser):
    b, fake = fake_browser
    res = b.save_login("github.com", "alice", "S3cr3t!", login_url="https://github.com/login")
    assert res["success"] is True
    assert res["password"] == "***"
    assert fake.get("web_login", "github.com::password") == "S3cr3t!"
    assert fake.get("web_login", "github.com::login_url") == "https://github.com/login"


def test_save_login_refuse_champs_vides(fake_browser):
    b, _ = fake_browser
    assert b.save_login("github.com", "", "x")["success"] is False
    assert b.save_login("", "u", "p")["success"] is False


def test_list_logins_ne_montre_que_les_domaines(fake_browser):
    b, _ = fake_browser
    b.save_login("github.com", "alice", "p1")
    b.save_login("gitlab.com", "bob", "p2")
    res = b.list_logins()
    assert set(res["domains"]) == {"github.com", "gitlab.com"}
    blob = str(res)
    assert "alice" not in blob and "p1" not in blob and "p2" not in blob


def test_delete_login_retire_tout(fake_browser):
    b, fake = fake_browser
    b.save_login("github.com", "alice", "p1", login_url="https://x")
    res = b.delete_login("github.com")
    assert res["success"] is True
    assert res["removed"] == 3
    assert b.list_logins()["domains"] == []
