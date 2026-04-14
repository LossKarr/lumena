"""Test exhaustif: TOUTES les routes non-publiques exigent verify_admin_token.

Ce test empêche l'ajout accidentel de routes sans auth.
Il scanne les décorateurs FastAPI et vérifie que chaque route a soit:
- un Depends(verify_admin_token) dans ses dependencies
- un paramètre _auth=Depends(verify_admin_token) dans sa signature
- est dans la liste blanche des routes publiques
"""

import ast
import inspect
import re
from pathlib import Path

import pytest

# Routes intentionnellement publiques (avec justification)
_PUBLIC_ROUTES = frozenset({
    # Liveness & monitoring
    "GET /api/health",
    # Root redirect
    "GET /",
    # Auth config (needed before login)
    "GET /api/auth/config",
    # Setup wizard (before token exists)
    "GET /api/setup/status",
    "GET /api/setup/schema",
    "GET /api/setup/ollama-models",
    # Stripe webhook (signature-based auth)
    "POST /api/stripe/webhook",
    # Public documentation
    "GET /api/product-docs",
    # WhatsApp webhook (Meta Cloud API — challenge GET, message POST with signature auth).
    # Note: path as declared in router decorator (prefix /api/whatsapp is added at mount).
    "GET /webhook",
    "POST /webhook",
    # WhatsApp status (monitoring dashboard — like /api/health)
    "GET /status",
    # Emotion dashboard — read-only state/history (like /api/health, no sensitive action)
    "GET /api/emotion",
    "GET /api/emotion/history",
    # Emotion WebSocket — real-time mood stream, read-only
    "WEBSOCKET /ws/emotion",
})


class TestAuthCoverageExhaustive:
    """Scans all route modules to verify auth coverage."""

    def _get_all_routes(self):
        """Parse all route files and extract route info."""
        routes_dir = Path(__file__).parent.parent / "web" / "routes"
        results = []

        for py_file in sorted(routes_dir.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "deps.py":
                continue

            source = py_file.read_text(encoding="utf-8", errors="replace")
            lines = source.splitlines()

            for i, line in enumerate(lines):
                # Match @router.get/post/put/delete/patch/websocket("...")
                # Must start at beginning of line (with optional indentation)
                m = re.match(
                    r'\s*@router\.(get|post|put|delete|patch|websocket)\(\s*["\']([^"\']+)["\']',
                    line,
                )
                if not m:
                    continue

                method = m.group(1).upper()
                path = m.group(2)
                route_key = f"{method} {path}"

                # Check if auth is present in decorator line (dependencies=[...])
                has_auth_decorator = "verify_admin_token" in line

                # Check next 5 lines for Depends(verify_admin_token) in function signature
                has_auth_param = False
                for j in range(i + 1, min(i + 6, len(lines))):
                    if "verify_admin_token" in lines[j]:
                        has_auth_param = True
                        break

                has_auth = has_auth_decorator or has_auth_param

                results.append({
                    "file": py_file.name,
                    "line": i + 1,
                    "method": method,
                    "path": path,
                    "key": route_key,
                    "has_auth": has_auth,
                })

        return results

    def test_all_non_public_routes_require_auth(self):
        """Every route not in _PUBLIC_ROUTES must have verify_admin_token."""
        routes = self._get_all_routes()
        assert len(routes) >= 50, f"Expected 50+ routes, found {len(routes)} — parser broken?"

        unprotected = []
        for r in routes:
            if r["key"] in _PUBLIC_ROUTES:
                continue
            if not r["has_auth"]:
                unprotected.append(f"  {r['key']}  ({r['file']}:{r['line']})")

        assert not unprotected, (
            f"{len(unprotected)} route(s) without auth:\n"
            + "\n".join(unprotected)
            + "\n\nFix: add dependencies=[Depends(deps.verify_admin_token)] "
            + "or add route to _PUBLIC_ROUTES with justification."
        )

    def test_public_routes_whitelist_is_valid(self):
        """Every route in _PUBLIC_ROUTES must actually exist."""
        routes = self._get_all_routes()
        existing_keys = {r["key"] for r in routes}

        phantom = _PUBLIC_ROUTES - existing_keys
        assert not phantom, (
            f"Public whitelist contains routes that don't exist: {phantom}"
        )

    def test_no_route_is_doubly_registered(self):
        """No route path should be registered twice with the same method."""
        routes = self._get_all_routes()
        seen = {}
        dupes = []
        for r in routes:
            if r["key"] in seen:
                dupes.append(f"  {r['key']}  ({seen[r['key']]} AND {r['file']}:{r['line']})")
            seen[r["key"]] = f"{r['file']}:{r['line']}"

        assert not dupes, f"Duplicate route registrations:\n" + "\n".join(dupes)

    def test_route_count_guard(self):
        """Guard: total route count should not drop (deletion = intentional)."""
        routes = self._get_all_routes()
        protected = sum(1 for r in routes if r["has_auth"])
        public = sum(1 for r in routes if r["key"] in _PUBLIC_ROUTES)

        # Current: 80+ total, 69+ protected, 11 public (emotion routes read-only ajoutées)
        assert len(routes) >= 70, f"Route count dropped to {len(routes)} — accidental deletion?"
        assert protected >= 60, f"Protected routes dropped to {protected} — auth removed?"
        assert public <= 15, f"Public routes increased to {public} — review whitelist"
