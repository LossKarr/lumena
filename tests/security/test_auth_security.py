"""Tests P0.3 — Auth fail-open + route protection."""

import os
import pytest
from unittest.mock import patch


class TestVerifyAdminToken:
    """Test the verify_admin_token dependency."""

    @pytest.fixture(autouse=True)
    def _import_deps(self):
        from web.routes.deps import verify_admin_token
        self.verify = verify_admin_token

    @pytest.mark.asyncio
    async def test_no_token_setup_done_raises_401(self):
        """If LUMENA_ADMIN_TOKEN is empty and setup is complete, refuse."""
        with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": "1", "LUMENA_ADMIN_TOKEN": ""}):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await self.verify(authorization=None, )
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_token_setup_not_done_fails_closed(self):
        """Before setup, protected routes stay closed without a token."""
        with patch.dict(os.environ, {"LUMENA_SETUP_COMPLETE": "", "LUMENA_ADMIN_TOKEN": ""}, clear=False):
            os.environ.pop("LUMENA_SETUP_COMPLETE", None)
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await self.verify(authorization=None, )
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_bearer_token(self):
        """Valid Bearer token passes."""
        with patch.dict(os.environ, {"LUMENA_ADMIN_TOKEN": "secret123", "LUMENA_SETUP_COMPLETE": "1"}):
            result = await self.verify(authorization="Bearer secret123", )
            assert result is None

    @pytest.mark.asyncio
    async def test_wrong_token_raises_403(self):
        """Wrong token raises 403."""
        with patch.dict(os.environ, {"LUMENA_ADMIN_TOKEN": "secret123", "LUMENA_SETUP_COMPLETE": "1"}):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await self.verify(authorization="Bearer wrongtoken", )
            assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_token_raises_401(self):
        """No token provided when one is configured raises 401."""
        with patch.dict(os.environ, {"LUMENA_ADMIN_TOKEN": "secret123", "LUMENA_SETUP_COMPLETE": "1"}):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                await self.verify(authorization=None, )
            assert exc_info.value.status_code == 401


class TestSetupCompleteLocalhost:
    """Test the localhost guard on setup_complete."""

    @pytest.mark.asyncio
    async def test_setup_complete_from_localhost_ok(self):
        """setup_complete from 127.0.0.1 should not raise 403."""
        from unittest.mock import AsyncMock, MagicMock
        from web.routes.setup import setup_complete

        mock_request = AsyncMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.json = AsyncMock(return_value={"preview": True, "config": {}})

        result = await setup_complete(mock_request)
        assert result.get("preview") is True

    @pytest.mark.asyncio
    async def test_setup_complete_from_lan_blocked(self):
        """setup_complete from LAN IP should raise 403."""
        from unittest.mock import AsyncMock, MagicMock
        from web.routes.setup import setup_complete
        from fastapi import HTTPException

        mock_request = AsyncMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "192.168.1.100"
        mock_request.json = AsyncMock(return_value={"config": {"LUMENA_ADMIN_TOKEN": "hack"}})

        with pytest.raises(HTTPException) as exc_info:
            await setup_complete(mock_request)
        assert exc_info.value.status_code == 403


class TestSetupLocalDependency:
    """Le bypass de bootstrap est strictement borné au pair TCP loopback."""

    @staticmethod
    def _request(host: str):
        from starlette.requests import Request
        return Request({"type": "http", "method": "GET", "path": "/api/setup/status",
                        "headers": [], "client": (host, 50000), "server": ("lumena", 8080),
                        "scheme": "http", "query_string": b""})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("host", ["127.0.0.1", "127.42.0.8", "::1", "localhost"])
    async def test_loopback_is_allowed(self, host):
        from web.routes.deps import verify_setup_local_request
        assert await verify_setup_local_request(self._request(host)) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("host", ["192.168.1.25", "10.0.0.4", "127.0.0.1.evil"])
    async def test_remote_peer_is_rejected(self, host):
        from fastapi import HTTPException
        from web.routes.deps import verify_setup_local_request
        with pytest.raises(HTTPException) as exc_info:
            await verify_setup_local_request(self._request(host))
        assert exc_info.value.status_code == 403

    def test_every_setup_route_uses_the_local_guard(self):
        from web.routes.deps import verify_setup_local_request
        from web.routes.setup import router

        setup_routes = [route for route in router.routes if route.path.startswith("/api/setup/")]
        assert len(setup_routes) == 7
        for route in setup_routes:
            guards = {dependency.call for dependency in route.dependant.dependencies}
            assert verify_setup_local_request in guards, route.path


class TestStripeRoutesAuth:
    """Verify the Stripe dashboard routes now require auth."""

    def test_stripe_routes_have_depends(self):
        """All 5 Stripe routes should have Depends in their signature."""
        import inspect
        from web.routes import stripe_dashboard

        route_funcs = [
            stripe_dashboard.stripe_summary,
            stripe_dashboard.stripe_payments,
            stripe_dashboard.stripe_subscriptions,
            stripe_dashboard.stripe_products,
            stripe_dashboard.create_payment_link_quick,
        ]

        for func in route_funcs:
            sig = inspect.signature(func)
            param_names = list(sig.parameters.keys())
            assert "_auth" in param_names, f"{func.__name__} missing _auth param"
