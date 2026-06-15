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
from typing import Callable

# ── Ensure project root on sys.path ──
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _start_server(host: str, port: int) -> None:
    """Run uvicorn in a daemon thread."""
    import uvicorn
    from web.server import app

    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


def _boot_timeout() -> int:
    """Délai max d'attente du serveur. Augmenté à 90s par défaut car activer
    les serveurs MCP au démarrage rallonge le boot (chacun = process Node +
    handshake) et dépassait l'ancien plafond de 30s → Lumena ne démarrait pas.
    Surchargeable via LUMENA_DESKTOP_BOOT_TIMEOUT."""
    try:
        return max(10, int(os.getenv("LUMENA_DESKTOP_BOOT_TIMEOUT", "90")))
    except (ValueError, TypeError):
        return 90


def _env_flag(name: str, default: bool = False) -> bool:
    """Return a permissive boolean flag from the environment."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _desktop_splash_enabled() -> bool:
    """Desktop splash is enabled by default, but can be disabled for debug."""
    return _env_flag("LUMENA_DESKTOP_SPLASH", True)


class _DesktopSplash:
    """Small Tk splash displayed while the local server is booting."""

    def __init__(self, tk_module) -> None:
        self._tk = tk_module
        self._start = time.monotonic()
        self._root = tk_module.Tk()
        self._root.title("Lumena")
        self._root.configure(bg="#070a10")
        self._root.resizable(False, False)
        self._root.overrideredirect(True)
        try:
            self._root.attributes("-topmost", True)
        except Exception:
            pass

        width, height = 460, 250
        try:
            screen_w = self._root.winfo_screenwidth()
            screen_h = self._root.winfo_screenheight()
            x = max(0, int((screen_w - width) / 2))
            y = max(0, int((screen_h - height) / 2))
            self._root.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            self._root.geometry(f"{width}x{height}")

        self._canvas = tk_module.Canvas(
            self._root,
            width=width,
            height=height,
            bg="#070a10",
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._draw_static(width, height)
        self._root.update_idletasks()
        self.update()

    def _draw_static(self, width: int, height: int) -> None:
        c = self._canvas
        self._draw_simple_background(width, height)
        self._draw_simple_card(width, height)

        logo_path = _ROOT / "web" / "static" / "branding" / "lumena-logo.png"
        self._logo_image = None
        if logo_path.is_file():
            try:
                image = self._tk.PhotoImage(file=str(logo_path))
                max_size = 52
                factor = max(1, int(max(image.width(), image.height()) / max_size))
                if factor > 1:
                    image = image.subsample(factor, factor)
                self._logo_image = image
                c.create_image(width // 2, 66, image=image)
            except Exception:
                self._draw_mark(width // 2, 66)
        else:
            self._draw_mark(width // 2, 66)

        c.create_text(
            width // 2,
            122,
            text="Lumena",
            fill="#fff7ed",
            font=("Segoe UI", 24, "bold"),
        )
        c.create_text(
            width // 2,
            153,
            text="Initialisation du noyau local",
            fill="#b9c0cc",
            font=("Segoe UI", 10),
        )
        c.create_rectangle(86, 188, 374, 196, fill="#111722", outline="#2f3948", width=1)
        self._bar = c.create_rectangle(88, 190, 148, 194, fill="#f28c28", outline="")
        self._status = c.create_text(
            width // 2,
            219,
            text="",
            fill="#f6a13a",
            font=("Segoe UI", 9),
        )

    def _draw_simple_background(self, width: int, height: int) -> None:
        c = self._canvas
        c.create_rectangle(0, 0, width, height, fill="#060910", outline="")
        c.create_oval(142, -128, 318, 48, fill="#160b05", outline="")
        c.create_oval(-80, 74, 108, 280, fill="#0b111c", outline="")
        c.create_oval(346, 62, 552, 292, fill="#05131a", outline="")
        self._spark_items = [
            c.create_oval(72, 76, 75, 79, fill="#f28c28", outline=""),
            c.create_oval(382, 56, 384, 58, fill="#f28c28", outline=""),
            c.create_oval(326, 207, 329, 210, fill="#f28c28", outline=""),
        ]

    def _draw_simple_card(self, width: int, height: int) -> None:
        c = self._canvas
        x1, y1, x2, y2 = 54, 34, width - 54, height - 34
        c.create_rectangle(x1 + 8, y1 + 8, x2 + 8, y2 + 8, fill="#02040a", outline="")
        c.create_rectangle(x1, y1, x2, y2, fill="#0b1019", outline="#2d3746", width=1)
        c.create_rectangle(x1 + 1, y1 + 1, x2 - 1, y2 - 1, outline="#1d2633", width=1)
        c.create_line(x1 + 22, y1 + 26, x2 - 22, y1 + 26, fill="#2b3442", width=1)
        c.create_line(x1 + 22, y2 - 26, x2 - 22, y2 - 26, fill="#2b3442", width=1)

    def _draw_mark(self, cx: int, cy: int) -> None:
        c = self._canvas
        orange = "#f28c28"
        dark = "#160b05"
        points = [
            cx - 38, cy - 34, cx + 6, cy - 56, cx + 58, cy - 26,
            cx + 58, cy + 28, cx + 6, cy + 58, cx - 38, cy + 36,
        ]
        c.create_polygon(points, fill=orange, outline="#ffb15b", width=2)
        c.create_polygon(
            [cx - 36, cy - 33, cx + 5, cy - 54, cx + 5, cy + 56, cx - 36, cy + 35],
            fill=dark,
            outline=orange,
            width=2,
        )
        c.create_line(cx - 30, cy - 22, cx - 2, cy - 8, cx - 30, cy + 22, fill=orange, width=3)

    def update(self) -> None:
        elapsed = time.monotonic() - self._start
        phase = int(elapsed * 72) % 286
        x1 = 88 + phase
        x2 = min(372, x1 + 64)
        if x1 > 372:
            x1 = 88
            x2 = 152
        self._canvas.coords(self._bar, x1, 190, x2, 194)
        for idx, item in enumerate(getattr(self, "_spark_items", [])):
            offset = ((int(elapsed * (7 + idx)) + idx * 9) % 18) - 9
            coords = self._canvas.coords(item)
            if len(coords) == 4:
                x1s, y1s, x2s, y2s = coords
                base_h = y2s - y1s
                cx = (x1s + x2s) / 2
                cy = (y1s + y2s) / 2
                self._canvas.coords(
                    item,
                    cx - base_h / 2,
                    cy + (offset * 0.02) - base_h / 2,
                    cx + base_h / 2,
                    cy + (offset * 0.02) + base_h / 2,
                )
        dots = "." * ((int(elapsed * 2) % 3) + 1)
        self._canvas.itemconfigure(self._status, text=f"")
        try:
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._root.destroy()
        except Exception:
            pass


def _create_desktop_splash() -> _DesktopSplash | None:
    if not _desktop_splash_enabled():
        return None
    try:
        import tkinter as tk
    except Exception:
        return None
    try:
        return _DesktopSplash(tk)
    except Exception as exc:
        print(f"[WARN] Splash Desktop non affiche: {exc}")
        return None


def _wait_for_server(port: int, timeout: int = 0, tick: Callable[[], None] | None = None) -> bool:
    """Poll /api/health until the server responds."""
    import urllib.request
    import urllib.error

    if timeout <= 0:
        timeout = _boot_timeout()
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tick is not None:
            tick()
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            if tick is not None:
                tick()
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

    _bt = _boot_timeout()
    print(f"[..] Démarrage du serveur sur le port {port} (timeout {_bt}s)...")
    splash = _create_desktop_splash()
    server_ready = _wait_for_server(port, _bt, tick=splash.update if splash else None)
    if splash:
        splash.close()
    if not server_ready:
        print(f"[ERREUR] Le serveur n'a pas démarré dans les {_bt}s.")
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
