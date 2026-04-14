"""
Tests unitaires pour browser_stealth.py (Phase 2.4 — Anti-détection browser).

Teste:
- UA_POOL : 10 User-Agents valides
- random_user_agent() : retourne un élément du pool
- random_viewport() : dimensions dans les bornes
- STEALTH_JS : script non vide, contient les bonnes injections
- get_proxy_config() : parsing de la variable d'env
- get_locale_timezone() : paires cohérentes
- build_stealth_config() : configuration complète
"""

import os
from unittest.mock import patch

import pytest

from src.tools.browser_stealth import (
    LOCALE_TIMEZONE_PAIRS,
    STEALTH_JS,
    UA_POOL,
    build_stealth_config,
    get_locale_timezone,
    get_proxy_config,
    human_delay,
    random_user_agent,
    random_viewport,
)


# ─── UA Pool ─────────────────────────────────────────────────────────────


class TestUAPool:
    def test_ten_agents(self):
        assert len(UA_POOL) == 10

    def test_all_strings(self):
        for ua in UA_POOL:
            assert isinstance(ua, str)
            assert len(ua) > 50

    def test_contains_chrome(self):
        chrome_uas = [ua for ua in UA_POOL if "Chrome" in ua]
        assert len(chrome_uas) >= 4

    def test_contains_edge(self):
        edge_uas = [ua for ua in UA_POOL if "Edg/" in ua]
        assert len(edge_uas) >= 2

    def test_contains_linux(self):
        linux_uas = [ua for ua in UA_POOL if "Linux" in ua]
        assert len(linux_uas) >= 1

    def test_no_duplicates(self):
        assert len(UA_POOL) == len(set(UA_POOL))


# ─── random_user_agent ───────────────────────────────────────────────────


class TestRandomUserAgent:
    def test_returns_string(self):
        ua = random_user_agent()
        assert isinstance(ua, str)

    def test_from_pool(self):
        for _ in range(20):
            assert random_user_agent() in UA_POOL

    def test_not_always_same(self):
        """Sur 50 tirages, il devrait y avoir plus d'un UA unique."""
        results = {random_user_agent() for _ in range(50)}
        assert len(results) > 1


# ─── random_viewport ────────────────────────────────────────────────────


class TestRandomViewport:
    def test_returns_dict_with_keys(self):
        vp = random_viewport()
        assert "width" in vp
        assert "height" in vp

    def test_default_within_bounds(self):
        for _ in range(30):
            vp = random_viewport()
            assert 1910 <= vp["width"] <= 1930
            assert 1070 <= vp["height"] <= 1090

    def test_custom_base(self):
        vp = random_viewport(base_width=1280, base_height=720, jitter=5)
        assert 1275 <= vp["width"] <= 1285
        assert 715 <= vp["height"] <= 725

    def test_zero_jitter(self):
        vp = random_viewport(jitter=0)
        assert vp["width"] == 1920
        assert vp["height"] == 1080

    def test_not_always_same(self):
        results = {(random_viewport()["width"], random_viewport()["height"]) for _ in range(50)}
        # Avec jitter=10, beaucoup de combinaisons possibles
        assert len(results) > 1


# ─── STEALTH_JS ──────────────────────────────────────────────────────────


class TestStealthJS:
    def test_non_empty(self):
        assert len(STEALTH_JS) > 100

    def test_masks_webdriver(self):
        assert "navigator" in STEALTH_JS
        assert "webdriver" in STEALTH_JS

    def test_masks_plugins(self):
        assert "plugins" in STEALTH_JS

    def test_masks_chrome_runtime(self):
        assert "chrome" in STEALTH_JS
        assert "runtime" in STEALTH_JS

    def test_masks_permissions(self):
        assert "permissions" in STEALTH_JS

    def test_masks_webgl(self):
        assert "WebGLRenderingContext" in STEALTH_JS

    def test_masks_canvas(self):
        assert "toDataURL" in STEALTH_JS

    def test_masks_chrome_app(self):
        assert "chrome.app" in STEALTH_JS

    def test_masks_hardware(self):
        assert "hardwareConcurrency" in STEALTH_JS


# ─── get_proxy_config ────────────────────────────────────────────────────


class TestGetProxyConfig:
    def test_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_proxy_config() is None

    def test_empty_env(self):
        with patch.dict(os.environ, {"LUMENA_BROWSER_PROXY": ""}):
            assert get_proxy_config() is None

    def test_simple_http(self):
        with patch.dict(os.environ, {"LUMENA_BROWSER_PROXY": "http://proxy.example.com:8080"}):
            result = get_proxy_config()
            assert result is not None
            assert result["server"] == "http://proxy.example.com:8080"

    def test_socks5(self):
        with patch.dict(os.environ, {"LUMENA_BROWSER_PROXY": "socks5://localhost:1080"}):
            result = get_proxy_config()
            assert result["server"] == "socks5://localhost:1080"

    def test_with_auth(self):
        with patch.dict(os.environ, {"LUMENA_BROWSER_PROXY": "http://user:pass@proxy.com:3128"}):
            result = get_proxy_config()
            assert result["server"] == "http://proxy.com:3128"
            assert result["username"] == "user"
            assert result["password"] == "pass"

    def test_whitespace_stripped(self):
        with patch.dict(os.environ, {"LUMENA_BROWSER_PROXY": "  http://p.com:80  "}):
            result = get_proxy_config()
            assert result["server"] == "http://p.com:80"


# ─── get_locale_timezone ─────────────────────────────────────────────────


class TestGetLocaleTimezone:
    def test_default_is_fr(self):
        with patch.dict(os.environ, {}, clear=True):
            locale, tz = get_locale_timezone()
            assert locale == "fr-FR"
            assert tz == "Europe/Paris"

    def test_env_locale_known(self):
        with patch.dict(os.environ, {"LUMENA_BROWSER_LOCALE": "en-US"}):
            locale, tz = get_locale_timezone()
            assert locale == "en-US"
            assert tz == "America/New_York"

    def test_env_locale_unknown(self):
        with patch.dict(os.environ, {"LUMENA_BROWSER_LOCALE": "ja-JP"}):
            locale, tz = get_locale_timezone()
            assert locale == "ja-JP"
            assert tz == "Europe/Paris"  # fallback

    def test_pairs_are_tuples(self):
        for pair in LOCALE_TIMEZONE_PAIRS:
            assert isinstance(pair, tuple)
            assert len(pair) == 2

    def test_at_least_5_pairs(self):
        assert len(LOCALE_TIMEZONE_PAIRS) >= 5


# ─── build_stealth_config ────────────────────────────────────────────────


class TestBuildStealthConfig:
    def test_has_all_keys(self):
        config = build_stealth_config()
        assert "user_agent" in config
        assert "viewport" in config
        assert "locale" in config
        assert "timezone_id" in config
        assert "args" in config
        assert "stealth_js" in config

    def test_user_agent_from_pool(self):
        config = build_stealth_config()
        assert config["user_agent"] in UA_POOL

    def test_viewport_is_dict(self):
        config = build_stealth_config()
        assert isinstance(config["viewport"], dict)
        assert "width" in config["viewport"]

    def test_args_has_no_sandbox(self):
        config = build_stealth_config()
        assert "--no-sandbox" in config["args"]

    def test_args_has_automation_disabled(self):
        config = build_stealth_config()
        assert "--disable-blink-features=AutomationControlled" in config["args"]

    def test_no_proxy_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            config = build_stealth_config()
            assert "proxy" not in config

    def test_proxy_when_env_set(self):
        with patch.dict(os.environ, {"LUMENA_BROWSER_PROXY": "http://p.com:80"}):
            config = build_stealth_config()
            assert "proxy" in config
            assert config["proxy"]["server"] == "http://p.com:80"

    def test_custom_viewport(self):
        config = build_stealth_config(base_viewport=(1280, 720), viewport_jitter=0)
        assert config["viewport"]["width"] == 1280

    def test_has_accept_language(self):
        config = build_stealth_config()
        assert "accept_language" in config
        assert "fr" in config["accept_language"] or "en" in config["accept_language"]

    def test_has_extra_http_headers(self):
        config = build_stealth_config()
        assert "extra_http_headers" in config
        assert "Accept-Language" in config["extra_http_headers"]
        assert "Sec-CH-UA-Platform" in config["extra_http_headers"]

    def test_stealth_js_includes_languages(self):
        config = build_stealth_config()
        assert "navigator.languages" in config["stealth_js"] or "languages" in config["stealth_js"]


# ─── human_delay ─────────────────────────────────────────────────────────


class TestHumanDelay:
    def test_within_bounds(self):
        for _ in range(50):
            d = human_delay(100, 300)
            assert 100 <= d <= 300

    def test_default_bounds(self):
        for _ in range(50):
            d = human_delay()
            assert 80 <= d <= 350


# ─── build_stealth_config (suite) ────────────────────────────────────────


class TestBuildStealthConfigExtra:
    def test_custom_viewport_height(self):
        config = build_stealth_config(base_viewport=(1280, 720), viewport_jitter=0)
        assert config["viewport"]["height"] == 720

    def test_stealth_js_included(self):
        config = build_stealth_config()
        # stealth_js = STEALTH_JS + languages dynamique
        assert "webdriver" in config["stealth_js"]
