"""
browser_stealth.py — Anti-détection browser (Phase 2.4).

Pool de User-Agents, viewport randomisé, injection JS anti-webdriver,
support proxy, timezone/locale cohérents.

⚠️ Destiné à l'automatisation de tests et scraping autorisé uniquement.
"""

from __future__ import annotations

import os
import random
from typing import Any, Dict, Optional, Tuple

from loguru import logger

# ─── Pool de User-Agents récents (Chrome/Edge, Windows/Mac) ───────────────
# Mis à jour avril 2026 — Chrome 133-135, Edge 133-135

UA_POOL: list[str] = [
    # Chrome 133-135 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    # Edge 133-135 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 Edg/133.0.0.0",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    # Edge Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
]


def random_user_agent() -> str:
    """Retourne un User-Agent aléatoire du pool."""
    return random.choice(UA_POOL)


# ─── Viewport randomisé ───────────────────────────────────────────────────


def random_viewport(
    base_width: int = 1920,
    base_height: int = 1080,
    jitter: int = 10,
) -> Dict[str, int]:
    """Retourne un viewport randomisé (±jitter pixels).

    Évite le fingerprinting par viewport exact.
    """
    w = base_width + random.randint(-jitter, jitter)
    h = base_height + random.randint(-jitter, jitter)
    return {"width": w, "height": h}


# ─── Scripts JS anti-détection ────────────────────────────────────────────

# Ce script est injecté via context.add_init_script() avant chaque page.
# Couvre: webdriver, plugins, chrome.*, iframe, WebGL, Canvas, AudioContext,
# permissions, hardware, languages — anti creep.js / fingerprint.js / datadome
STEALTH_JS = """
(() => {
  // 1. navigator.webdriver = undefined
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

  // 2. navigator.plugins — objets Plugin réalistes
  const _mkPlugin = (name, filename, desc, mimeType, mimeDesc, suffixes) => {
    const mt = { type: mimeType, description: mimeDesc, suffixes, enabledPlugin: null };
    const p = { name, filename, description: desc, length: 1, 0: mt, item: i => (i === 0 ? mt : null), namedItem: n => (n === mimeType ? mt : null) };
    mt.enabledPlugin = p;
    Object.setPrototypeOf(p, PluginArray.prototype);
    return p;
  };
  const _plugins = [
    _mkPlugin('Chrome PDF Plugin', 'internal-pdf-viewer', 'Portable Document Format', 'application/x-google-chrome-pdf', 'Portable Document Format', 'pdf'),
    _mkPlugin('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', '', 'application/pdf', '', 'pdf'),
    _mkPlugin('Native Client', 'internal-nacl-plugin', '', 'application/x-nacl', 'Native Client Executable', ''),
  ];
  Object.defineProperty(navigator, 'plugins', {
    get: () => {
      const arr = [..._plugins];
      arr.item = i => _plugins[i] || null;
      arr.namedItem = n => _plugins.find(p => p.name === n) || null;
      arr.refresh = () => {};
      Object.setPrototypeOf(arr, PluginArray.prototype);
      return arr;
    }
  });
  Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
      const mt = _plugins.map(p => p[0]);
      mt.item = i => mt[i] || null;
      mt.namedItem = n => mt.find(m => m.type === n) || null;
      Object.setPrototypeOf(mt, MimeTypeArray.prototype);
      return mt;
    }
  });

  // 3. window.chrome complet (app, csi, loadTimes, runtime)
  if (!window.chrome) window.chrome = {};
  window.chrome.app = { isInstalled: false, getDetails: () => null, getIsInstalled: () => false, installState: () => 'disabled', runningState: () => 'cannot_run' };
  window.chrome.csi = () => ({ onloadT: Date.now(), startE: Date.now(), pageT: Math.random() * 500 + 200, tran: 15 });
  window.chrome.loadTimes = () => ({ commitLoadTime: Date.now() / 1000, connectionInfo: 'h2', finishDocumentLoadTime: Date.now() / 1000 + 0.2, finishLoadTime: Date.now() / 1000 + 0.5, firstPaintAfterLoadTime: 0, firstPaintTime: Date.now() / 1000 + 0.1, navigationType: 'Other', npnNegotiatedProtocol: 'h2', requestTime: Date.now() / 1000 - 0.3, startLoadTime: Date.now() / 1000 - 0.3, wasAlternateProtocolAvailable: false, wasFetchedViaSpdy: true, wasNpnNegotiated: true });
  if (!window.chrome.runtime) window.chrome.runtime = {};

  // 4. Propagation chrome dans les iframes
  const _origCreateElement = document.createElement.bind(document);
  document.createElement = function(tag, opts) {
    const el = _origCreateElement(tag, opts);
    if (tag.toLowerCase() === 'iframe') {
      const _origAppend = Element.prototype.appendChild;
      const patchIframe = () => {
        try {
          if (el.contentWindow && !el.contentWindow.chrome) {
            el.contentWindow.chrome = window.chrome;
          }
        } catch(e) {}
      };
      el.addEventListener('load', patchIframe);
      setTimeout(patchIframe, 0);
    }
    return el;
  };

  // 5. WebGL — bruit dans readPixels pour unicité fingerprint variable
  const _origGetParam = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(p) {
    // UNMASKED_VENDOR_WEBGL / UNMASKED_RENDERER_WEBGL
    if (p === 0x9245) return 'Intel Inc.';
    if (p === 0x9246) return 'Intel Iris OpenGL Engine';
    return _origGetParam.call(this, p);
  };
  const _origReadPixels = WebGLRenderingContext.prototype.readPixels;
  WebGLRenderingContext.prototype.readPixels = function() {
    _origReadPixels.apply(this, arguments);
    // Injecter bruit ±1 dans quelques pixels
    if (arguments[6] && arguments[6].length) {
      const d = arguments[6];
      for (let i = 0; i < Math.min(d.length, 20); i += 4) {
        d[i] = d[i] ^ (Math.random() > 0.5 ? 1 : 0);
      }
    }
  };

  // 6. Canvas — bruit dans toDataURL / getImageData
  const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(type) {
    const ctx = this.getContext('2d');
    if (ctx) {
      const img = ctx.getImageData(0, 0, Math.min(this.width, 2), Math.min(this.height, 2));
      for (let i = 0; i < img.data.length; i += 4) {
        img.data[i] = img.data[i] ^ (Math.random() > 0.5 ? 1 : 0);
      }
      ctx.putImageData(img, 0, 0);
    }
    return _origToDataURL.apply(this, arguments);
  };

  // 7. AudioContext fingerprint noise
  const _origGetFloatFreq = AnalyserNode.prototype.getFloatFrequencyData;
  if (_origGetFloatFreq) {
    AnalyserNode.prototype.getFloatFrequencyData = function(array) {
      _origGetFloatFreq.call(this, array);
      for (let i = 0; i < array.length; i++) {
        array[i] += (Math.random() - 0.5) * 0.01;
      }
    };
  }

  // 8. Permissions — toutes les permissions, pas juste notifications
  const _origPermQuery = navigator.permissions.query.bind(navigator.permissions);
  navigator.permissions.query = (desc) => {
    if (desc.name === 'notifications') return Promise.resolve({ state: Notification.permission, onchange: null });
    return _origPermQuery(desc).catch(() => Promise.resolve({ state: 'prompt', onchange: null }));
  };

  // 9. Hardware / device coherence
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
  Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

  // 10. navigator.languages cohérent (sera mis à jour par build_stealth_config)
  // Placeholder — la vraie valeur est injectée dynamiquement ci-dessous
})();
"""


# ─── Proxy ─────────────────────────────────────────────────────────────────


def get_proxy_config() -> Optional[Dict[str, str]]:
    """Lit la config proxy depuis la variable d'env LUMENA_BROWSER_PROXY.

    Formats supportés:
        http://host:port
        socks5://host:port
        http://user:pass@host:port

    Returns:
        Dict compatible Playwright {'server': ...} ou None si non configuré.
    """
    proxy_url = os.environ.get("LUMENA_BROWSER_PROXY", "").strip()
    if not proxy_url:
        return None

    config: Dict[str, str] = {"server": proxy_url}

    # Extraire user:pass si présent
    if "@" in proxy_url:
        try:
            # Format: scheme://user:pass@host:port
            scheme_rest = proxy_url.split("://", 1)
            if len(scheme_rest) == 2:
                auth_host = scheme_rest[1]
                if "@" in auth_host:
                    auth, host = auth_host.rsplit("@", 1)
                    if ":" in auth:
                        username, password = auth.split(":", 1)
                        config["username"] = username
                        config["password"] = password
                    config["server"] = f"{scheme_rest[0]}://{host}"
        except Exception:
            pass  # Garder l'URL brute

    logger.info(f"🔒 Proxy browser configuré: {config.get('server', proxy_url)}")
    return config


# ─── Timezone & Locale ─────────────────────────────────────────────────────

# Paires cohérentes timezone/locale pour éviter les incohérences
LOCALE_TIMEZONE_PAIRS: list[Tuple[str, str]] = [
    ("fr-FR", "Europe/Paris"),
    ("en-US", "America/New_York"),
    ("en-GB", "Europe/London"),
    ("de-DE", "Europe/Berlin"),
    ("es-ES", "Europe/Madrid"),
]


def get_locale_timezone() -> Tuple[str, str]:
    """Retourne une paire (locale, timezone) cohérente.

    Utilise LUMENA_BROWSER_LOCALE si défini, sinon fr-FR par défaut.
    """
    env_locale = os.environ.get("LUMENA_BROWSER_LOCALE", "").strip()
    if env_locale:
        # Chercher la paire correspondante
        for locale, tz in LOCALE_TIMEZONE_PAIRS:
            if locale == env_locale:
                return locale, tz
        # Locale inconnue, utiliser Europe/Paris par défaut
        return env_locale, "Europe/Paris"
    return "fr-FR", "Europe/Paris"


# ─── Accept-Language cohérent pour chaque locale ──────────────────────────

_ACCEPT_LANGUAGE_MAP: dict[str, str] = {
    "fr-FR": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-US": "en-US,en;q=0.9",
    "en-GB": "en-GB,en;q=0.9,en-US;q=0.8",
    "de-DE": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "es-ES": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _languages_js(locale: str) -> str:
    """JS snippet qui force navigator.languages cohérent avec la locale."""
    lang = locale.split("-")[0]  # "fr-FR" → "fr"
    return f"""
Object.defineProperty(navigator, 'languages', {{ get: () => ['{locale}', '{lang}', 'en-US', 'en'] }});
Object.defineProperty(navigator, 'language', {{ get: () => '{locale}' }});
"""


# ─── Humanisation — délais aléatoires ─────────────────────────────────────


def human_delay(min_ms: int = 80, max_ms: int = 350) -> int:
    """Retourne un délai humain en ms pour simulate des pauses réalistes."""
    return random.randint(min_ms, max_ms)


# ─── Configuration stealth complète ───────────────────────────────────────


def build_stealth_config(
    headless: bool = True,
    base_viewport: Tuple[int, int] = (1920, 1080),
    viewport_jitter: int = 10,
) -> Dict[str, Any]:
    """Construit la configuration stealth complète pour Playwright.

    Returns:
        Dict avec les clés: user_agent, viewport, locale, timezone_id,
        proxy, args, stealth_js, accept_language, extra_http_headers.
    """
    locale, timezone = get_locale_timezone()
    proxy = get_proxy_config()
    viewport = random_viewport(base_viewport[0], base_viewport[1], viewport_jitter)
    ua = random_user_agent()

    accept_lang = _ACCEPT_LANGUAGE_MAP.get(locale, "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7")

    # Script stealth = base + navigator.languages dynamique
    full_stealth = STEALTH_JS + _languages_js(locale)

    args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-infobars",
        "--disable-dev-shm-usage",
    ]

    config: Dict[str, Any] = {
        "user_agent": ua,
        "viewport": viewport,
        "locale": locale,
        "timezone_id": timezone,
        "args": args,
        "stealth_js": full_stealth,
        "accept_language": accept_lang,
        "extra_http_headers": {
            "Accept-Language": accept_lang,
            "Sec-CH-UA-Platform": '"Windows"' if "Windows" in ua else ('"macOS"' if "Mac" in ua else '"Linux"'),
        },
    }

    if proxy:
        config["proxy"] = proxy

    return config
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
