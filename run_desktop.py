"""
Lumena Desktop — Native window launcher via pywebview.

Starts the FastAPI server in a background thread, then opens a native
window (no browser).  The web version remains accessible at the same URL.

Usage:
    python run_desktop.py          # windowed (default)
    python run_desktop.py --web    # fallback: open browser instead
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

# ── Ensure project root on sys.path ──
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _start_server(host: str, port: int) -> None:
    """Run uvicorn in a daemon thread."""
    import uvicorn
    from web.server import app

    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


def _wait_for_server(port: int, timeout: int = 30) -> bool:
    """Poll /api/health until the server responds."""
    import urllib.request
    import urllib.error

    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def _desktop_zoom() -> float:
    """Return the pywebview zoom factor, clamped to a safe readable range."""
    raw = os.getenv("LUMENA_DESKTOP_ZOOM", "0.90").strip()
    try:
        zoom = float(raw)
    except ValueError:
        zoom = 0.90
    return min(1.25, max(0.67, zoom))


def _apply_desktop_zoom(window) -> None:
    """Apply zoom only inside the native desktop webview.

    CSS `zoom` shrinks the paint surface without resizing 100vh layouts,
    which leaves an empty band at the bottom of pywebview. A compensated
    transform keeps the same visual scale while expanding the layout box.
    """
    zoom = _desktop_zoom()
    script = f"""
    (function() {{
      const zoom = {zoom:.4f};
      const root = document.documentElement;
      const body = document.body;
      let style = document.getElementById("lumena-desktop-zoom-style");
      if (!style) {{
        style = document.createElement("style");
        style.id = "lumena-desktop-zoom-style";
        document.head.appendChild(style);
      }}
      root.dataset.lumenaDesktopZoom = String(zoom);

      root.style.removeProperty("zoom");
      body.style.removeProperty("zoom");

      if (Math.abs(zoom - 1) < 0.001) {{
        root.classList.remove("lumena-desktop-zoom");
        style.textContent = "";
        root.style.removeProperty("overflow");
        body.style.removeProperty("transform");
        body.style.removeProperty("transform-origin");
        body.style.removeProperty("width");
        body.style.removeProperty("height");
        body.style.removeProperty("overflow");
        return;
      }}

      root.classList.add("lumena-desktop-zoom");
      style.textContent = [
        "html.lumena-desktop-zoom .shell {{",
        "  height: calc(100vh / " + zoom + ") !important;",
        "  min-height: calc(100vh / " + zoom + ") !important;",
        "}}",
        "@supports(height: 100dvh) {{",
        "  html.lumena-desktop-zoom .shell {{",
        "    height: calc(100dvh / " + zoom + ") !important;",
        "    min-height: calc(100dvh / " + zoom + ") !important;",
        "  }}",
        "}}"
      ].join("\\n");
      root.style.overflow = "hidden";
      body.style.transform = `scale(${{zoom}})`;
      body.style.transformOrigin = "top left";
      body.style.width = `${{100 / zoom}}%`;
      body.style.height = `${{100 / zoom}}%`;
      body.style.overflow = "hidden";
    }})();
    """
    try:
        window.evaluate_js(script)
        print(f"[OK] Zoom Desktop applique: {zoom:.2f}")
    except Exception as exc:
        print(f"[WARN] Zoom Desktop non applique: {exc}")


def main() -> None:
    _raw_port = os.getenv("LUMENA_PORT", "8080").strip().lower()
    if _raw_port == "auto":
        from src.runtime.instance_registry import resolve_startup_port
        port = resolve_startup_port()
        os.environ["LUMENA_PORT"] = str(port)
        print(f"[PORT] Mode auto — port {port} sélectionné")
    else:
        port = int(_raw_port)
    host = os.getenv("LUMENA_HOST", "0.0.0.0")

    # ── Launch server in background ──
    srv = threading.Thread(target=_start_server, args=(host, port), daemon=True)
    srv.start()

    print(f"[..] Démarrage du serveur sur le port {port}...")
    if not _wait_for_server(port):
        print("[ERREUR] Le serveur n'a pas démarré dans les 30s.")
        sys.exit(1)
    print(f"[OK] Serveur prêt — http://localhost:{port}")

    url = f"http://localhost:{port}"

    # ── Fallback: browser mode ──
    if "--web" in sys.argv:
        import webbrowser
        webbrowser.open(url)
        print("[OK] Ouvert dans le navigateur. Ctrl+C pour arrêter.")
        try:
            srv.join()
        except KeyboardInterrupt:
            pass
        return

    # ── Clear stale WebView2 cache (ensures HTML/CSS/JS changes apply) ──
    try:
        import shutil
        _wv2_cache = Path(os.environ.get("LOCALAPPDATA", "")) / "pywebview"
        if _wv2_cache.is_dir():
            for _sub in ("EBWebView/Cache", "EBWebView/Code Cache"):
                _cd = _wv2_cache / _sub
                if _cd.is_dir():
                    shutil.rmtree(_cd, ignore_errors=True)
                    print(f"[OK] Cache WebView2 vidé: {_cd}")
    except Exception as _ce:
        print(f"[WARN] Nettoyage cache WebView2 échoué: {_ce}")

    # ── Native window via pywebview ──
    try:
        import webview  # pywebview
    except ImportError:
        print("[WARN] pywebview non installé — fallback navigateur.")
        import webbrowser
        webbrowser.open(url)
        try:
            srv.join()
        except KeyboardInterrupt:
            pass
        return

    # Detect best GUI backend — need EdgeChromium for ES modules support
    gui_backend = None
    if sys.platform == "win32":
        try:
            import clr_loader
            gui_backend = "edgechromium"
            print("[OK] Moteur EdgeChromium (WebView2) détecté")
        except Exception:
            # EdgeChromium unavailable — MSHTML (IE11) can't run ES modules
            print("[WARN] WebView2 Runtime non disponible — fallback navigateur.")
            print("       Installez-le depuis: https://developer.microsoft.com/en-us/microsoft-edge/webview2/")
            import webbrowser
            webbrowser.open(url)
            try:
                srv.join()
            except KeyboardInterrupt:
                pass
            return

    window = webview.create_window(
        title="Lumena",
        url=url,
        width=1280,
        height=820,
        min_size=(800, 600),
        resizable=True,
        text_select=True,
        confirm_close=False,
    )

    # When the window is closed, exit the process (kills the daemon server thread)
    def _on_closed():
        os._exit(0)

    window.events.closed += _on_closed
    window.events.loaded += lambda *args: _apply_desktop_zoom(window)

    # webview.start() blocks until the window is closed
    webview.start(
        gui=gui_backend,
        private_mode=False,  # persist cookies / local storage
        debug=os.getenv("LUMENA_DEBUG", "") == "1",
    )


if __name__ == "__main__":
    main()
