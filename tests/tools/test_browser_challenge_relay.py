"""W2 — Relais humain : la logique de décision de detect_challenge
(captcha vs 2FA/OTP vs rien) à partir des signaux DOM relevés côté page.

NB : l'import de playwright_browser est différé DANS les fonctions pour ne pas
figer MAX_TABS avant le chargement de .env (flake d'ordre d'import sinon).
"""
import asyncio


class _FakePage:
    def __init__(self, flags):
        self.url = "https://site/login"
        self._flags = flags

    async def evaluate(self, _script):
        return self._flags


def _detect(flags):
    from src.tools.playwright_browser import PlaywrightBrowser
    b = PlaywrightBrowser()
    b._page = _FakePage({
        "recaptcha": False, "hcaptcha": False, "turnstile": False,
        "otpField": False, "twofa": False, "title": "x", **flags,
    })
    return asyncio.run(b.detect_challenge())


def test_recaptcha_donne_captcha():
    res = _detect({"recaptcha": True})
    assert res["kind"] == "captcha"
    assert res["provider"] == "reCAPTCHA"
    assert "done=true" in res["needs"]


def test_hcaptcha_donne_captcha():
    assert _detect({"hcaptcha": True})["provider"] == "hCaptcha"


def test_turnstile_donne_captcha():
    assert _detect({"turnstile": True})["provider"] == "Cloudflare Turnstile"


def test_otp_field_donne_otp():
    res = _detect({"otpField": True})
    assert res["kind"] == "otp"
    assert "code" in res["needs"].lower()


def test_texte_2fa_donne_otp():
    assert _detect({"twofa": True})["kind"] == "otp"


def test_rien_donne_none():
    assert _detect({})["kind"] == "none"


def test_captcha_prioritaire_sur_otp():
    assert _detect({"recaptcha": True, "otpField": True})["kind"] == "captcha"
