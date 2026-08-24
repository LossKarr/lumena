"""
Lumena Web API — v2 modular entrypoint.

Slim assembler that imports route modules and mounts them on a single FastAPI app.
Functional equivalent of server.py but split into ~11 modules under web/routes/.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the lumena root is in sys.path when running as `python web/server.py`
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.version import __version__

# ── Lifespan (startup / shutdown logic) ──
from web.routes.lifespan import lifespan  # noqa: E402

# ── Route modules ──
from web.routes import system, chat, sessions, tasks, config, models, content, advanced, setup, docs, product_docs, stripe_webhook, stripe_dashboard, workspaces, finetuning, whatsapp, emotion, ionos, image_gen, peers, mcp, missions, document_studio, codex_subscription, updates  # noqa: E402

# ── App creation ──
_SETUP_DONE = os.getenv("LUMENA_SETUP_COMPLETE", "") == "1"

app = FastAPI(
    title="Lumena Web API",
    version=__version__,
    lifespan=lifespan,
    docs_url=None if _SETUP_DONE else "/docs",
    redoc_url=None if _SETUP_DONE else "/redoc",
    openapi_url=None if _SETUP_DONE else "/openapi.json",
)

# ── CORS — restricted to localhost (derived from LUMENA_PORT) ──
_LUMENA_PORT = int(os.getenv("LUMENA_PORT", "8080"))
_ALLOWED_ORIGINS = os.getenv("LUMENA_CORS_ORIGINS", "").split(",") if os.getenv("LUMENA_CORS_ORIGINS") else [
    f"http://localhost:{_LUMENA_PORT}",
    f"http://127.0.0.1:{_LUMENA_PORT}",
    f"http://localhost:{_LUMENA_PORT + 1}",
    f"http://127.0.0.1:{_LUMENA_PORT + 1}",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── GZip compression (bypass SSE to avoid buffering) ──
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

_SSE_PATHS = frozenset({"/api/chat/stream", "/api/trace/stream", "/api/finetuning/progress", "/api/finetuning/install-deps"})


class _GZipNoSSE:
    """GZip middleware that bypasses SSE streaming endpoints."""

    def __init__(self, app, *, minimum_size: int = 500):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "") in _SSE_PATHS:
            await self.app(scope, receive, send)
        else:
            await self.gzip(scope, receive, send)


app.add_middleware(_GZipNoSSE, minimum_size=500)

# ── Security headers + CSP (pure ASGI — no BaseHTTPMiddleware) ──
_CSP_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://unpkg.com 'unsafe-inline'",
    "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "connect-src 'self' https://unpkg.com ws://localhost:* ws://127.0.0.1:*",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])
_CSP_BYTES = _CSP_POLICY.encode()

_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"content-security-policy", _CSP_BYTES),
    (b"permissions-policy", b"camera=(), microphone=(self), geolocation=()"),
]

# No-cache for static JS/CSS so changes are picked up immediately
_NO_CACHE_HEADER = (b"cache-control", b"no-cache, must-revalidate")


class _SecurityHeadersMiddleware:
    """Inject security headers without buffering streaming responses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(_SECURITY_HEADERS)
                # Force no-cache on static assets + root page (pywebview cache)
                if path.startswith("/static/") or path.startswith("/assets/") or path == "/":
                    headers.append(_NO_CACHE_HEADER)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send)


app.add_middleware(_SecurityHeadersMiddleware)

# ── Rate limiting (pure ASGI — in-memory token bucket per IP) ──
import time as _time  # noqa: E402
from starlette.responses import JSONResponse as _JSONResponse  # noqa: E402

_RATE_BUCKETS: dict = {}
_RATE_LOCK = __import__("threading").Lock()
_RATE_WINDOW = int(os.getenv("LUMENA_RATE_WINDOW", "60"))  # seconds
# Per-category rate limits (requests per window)
_RATE_LIMITS = {
    "expensive": int(os.getenv("LUMENA_RATE_EXPENSIVE", "20")),   # /api/chat, /api/upload
    "default":  int(os.getenv("LUMENA_RATE_DEFAULT", "200")),     # most API endpoints
    "health":   int(os.getenv("LUMENA_RATE_HEALTH", "600")),     # /api/health, /api/status
}
_EXPENSIVE_PREFIXES = (
    "/api/chat",
    "/api/upload",
    "/api/mcp/approvals/",
    "/api/mcp/install/",
    "/api/mcp/local-create/",
    "/api/mcp/activation/",
    "/api/mcp/catalog/",
    "/api/mcp/autoapprove/",
    # Phase 21 — Hardening MCP : observabilité / keys / audit-integrity /
    # coherence / readiness (lectures qui font tail jsonl ou agrégat
    # multi-singletons, donc traitées comme expensive pour throttling)
    "/api/mcp/observability/",
    "/api/mcp/keys/",
    "/api/mcp/audit-integrity/",
    "/api/mcp/coherence/",
    "/api/mcp/readiness/",
)
_HEALTH_PREFIXES = ("/api/health", "/api/status")


def _rate_category(path: str) -> str:
    for pfx in _HEALTH_PREFIXES:
        if path == pfx or path.startswith(pfx + "/"):
            return "health"
    for pfx in _EXPENSIVE_PREFIXES:
        if path == pfx or path.startswith(pfx + "/"):
            return "expensive"
    return "default"


class _RateLimitMiddleware:
    """Rate-limit /api/ endpoints with per-category limits."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        path = scope.get("path", "")
        category = _rate_category(path)
        # P3.2 — Hot-reload: re-lit os.environ à chaque requête pour prendre en compte
        # les changements de config appliqués sans redémarrage.
        _live_limits = {
            "expensive": int(os.getenv("LUMENA_RATE_EXPENSIVE", "20")),
            "default":   int(os.getenv("LUMENA_RATE_DEFAULT",   "200")),
            "health":    int(os.getenv("LUMENA_RATE_HEALTH",    "600")),
        }
        limit = _live_limits[category]
        bucket_key = f"{client_ip}:{category}"
        now = _time.monotonic()

        blocked = False
        retry = 0
        with _RATE_LOCK:
            bucket = _RATE_BUCKETS.get(bucket_key)
            window = int(os.getenv("LUMENA_RATE_WINDOW", "60"))  # hot-reload
            if bucket is None or now - bucket["reset"] >= window:
                _RATE_BUCKETS[bucket_key] = {"count": 1, "reset": now}
            else:
                bucket["count"] += 1
                if bucket["count"] > limit:
                    retry = int(window - (now - bucket["reset"])) + 1
                    blocked = True

        if blocked:
            resp = _JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Retry in {retry}s."},
                headers={"Retry-After": str(retry)},
            )
            await resp(scope, receive, send)
            return

        await self.app(scope, receive, send)


app.add_middleware(_RateLimitMiddleware)

# ── Include route modules ──
app.include_router(system.router)
app.include_router(chat.router)
app.include_router(sessions.router)
app.include_router(tasks.router)
app.include_router(config.router)
app.include_router(models.router)
app.include_router(content.router)
app.include_router(advanced.router)
app.include_router(setup.router)
app.include_router(docs.router)
app.include_router(product_docs.router)
app.include_router(stripe_webhook.router)
app.include_router(stripe_dashboard.router)
app.include_router(workspaces.router)
app.include_router(finetuning.router)
app.include_router(whatsapp.router)
app.include_router(emotion.router)
app.include_router(ionos.router)
app.include_router(image_gen.router)
app.include_router(peers.router)
app.include_router(mcp.router)
app.include_router(missions.router)
app.include_router(document_studio.router)
app.include_router(codex_subscription.router)
app.include_router(updates.router)

# ── Static files and root page ──
_WEB_DIR = Path(__file__).parent
_DIST_DIR = _WEB_DIR / "dist"
_STATIC_DIR = _WEB_DIR / "static"
_ASSETS_DIR = _DIST_DIR / "assets"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
if _ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")


# ── Main ──
if __name__ == "__main__":
    import sys, io
    # pythonw.exe has no console — fix C-level file descriptors THEN Python streams
    if sys.stdout is None or sys.stderr is None:
        _devnull_fd = os.open(os.devnull, os.O_RDWR)
        for _fd in (0, 1, 2):
            try:
                os.fstat(_fd)
            except OSError:
                os.dup2(_devnull_fd, _fd)
        os.close(_devnull_fd)
        if sys.stdout is None:
            sys.stdout = open(1, "w", encoding="utf-8", errors="replace")
        if sys.stderr is None:
            sys.stderr = open(2, "w", encoding="utf-8", errors="replace")
        if sys.stdin is None:
            sys.stdin = open(0, "r")
    elif sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    _host = os.getenv("LUMENA_HOST", "0.0.0.0")
    _port = _LUMENA_PORT
    uvicorn.run(app, host=_host, port=_port, log_level="info", access_log=False)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
