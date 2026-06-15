"""W3 — Actions vérifiées : un download n'est "réussi" que si le fichier est
réellement présent/non vide/lisible ; un formulaire n'est "soumis avec succès"
que s'il n'y a pas d'erreur et qu'un signal de réussite existe.

Import de playwright_browser différé dans les fonctions (évite le flake d'ordre
d'import sur MAX_TABS lié au chargement de .env).
"""
import asyncio


# ─── Vérification de téléchargement ──────────────────────────────────────────

def test_verify_download_fichier_absent():
    from src.tools.playwright_browser import PlaywrightBrowser
    res = PlaywrightBrowser._verify_downloaded_file("C:/nope/inexistant_xyz.bin")
    assert res["verified"] is False
    assert res["size"] == 0


def test_verify_download_fichier_vide(tmp_path):
    from src.tools.playwright_browser import PlaywrightBrowser
    empty = tmp_path / "vide.pdf"
    empty.write_bytes(b"")
    res = PlaywrightBrowser._verify_downloaded_file(str(empty))
    assert res["verified"] is False
    assert "vide" in res["reason"]


def test_verify_download_fichier_valide(tmp_path):
    from src.tools.playwright_browser import PlaywrightBrowser
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.7 hello world contenu reel")
    res = PlaywrightBrowser._verify_downloaded_file(str(f))
    assert res["verified"] is True
    assert res["size"] > 0
    assert "magic" in res


# ─── Vérification de soumission de formulaire ────────────────────────────────

class _FakePage:
    def __init__(self, url: str, body: str):
        self.url = url
        self._body = body

    async def inner_text(self, _sel):
        return self._body


def _verify(page, **kw):
    from src.tools.playwright_browser import PlaywrightBrowser
    b = PlaywrightBrowser()
    b._page = page
    return asyncio.run(b.verify_submission(**kw))


def test_submission_echoue_si_message_erreur():
    page = _FakePage("https://site/login", "Identifiants incorrects, réessayer")
    res = _verify(page, before_url="https://site/login")
    assert res["confirmed"] is False
    assert any("erreur" in s.lower() for s in res["signals"])


def test_submission_reussit_sur_changement_url():
    page = _FakePage("https://site/dashboard", "Bienvenue sur votre espace")
    res = _verify(page, before_url="https://site/login")
    assert res["confirmed"] is True
    assert res["url_changed"] is True


def test_submission_reussit_sur_texte_attendu():
    page = _FakePage("https://site/x", "Votre compte a bien été créé. Merci !")
    res = _verify(page, before_url="https://site/x", expect_text="compte a bien été créé")
    assert res["confirmed"] is True


def test_submission_erreur_prime_sur_url_changee():
    page = _FakePage("https://site/login?err=1", "Mot de passe incorrect")
    res = _verify(page, before_url="https://site/login")
    assert res["confirmed"] is False


def test_submission_aucun_signal_non_confirme():
    page = _FakePage("https://site/login", "Connexion à votre compte")
    res = _verify(page, before_url="https://site/login")
    assert res["confirmed"] is False
    assert "aucun signal" in res["reason"]
