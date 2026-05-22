"""Tests unitaires DataGouvService — mocks httpx, pas d'appel réseau."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.datagouv import DataGouvService, get_datagouv_service


@pytest.fixture
def service():
    return DataGouvService()


# ─── search_datasets ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_datasets_returns_data(service):
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "data": [{"slug": "x", "title": "Test"}],
        "total": 1,
    }
    fake_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake_response)
        result = await service.search_datasets("population")
        assert result["total"] == 1
        assert result["data"][0]["slug"] == "x"


@pytest.mark.asyncio
async def test_search_datasets_caps_page_size_at_100(service):
    fake_response = MagicMock()
    fake_response.json.return_value = {"data": [], "total": 0}
    fake_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake_response)
        await service.search_datasets("x", page_size=500)
        kwargs = mock_ctx.get.call_args.kwargs
        assert kwargs["params"]["page_size"] == 100


# ─── get_dataset / organizations ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_dataset_builds_correct_path(service):
    fake_response = MagicMock()
    fake_response.json.return_value = {"slug": "my-ds"}
    fake_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake_response)
        await service.get_dataset("my-ds")
        called_url = mock_ctx.get.call_args.args[0]
        assert called_url.endswith("/datasets/my-ds/")


# ─── rate limiter ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_throttles(service):
    rl = service._rate_limiter
    rl._max = 3
    rl._period = 0.5
    t0 = time.monotonic()
    for _ in range(4):
        await rl.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.4, "rate limiter aurait dû throttler le 4e appel"


# ─── download_resource ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_resource_enforces_max_bytes(service, tmp_path):
    target = tmp_path / "big.csv"

    class FakeStream:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def raise_for_status(self):
            return None

        async def aiter_bytes(self, chunk_size=None):
            for _ in range(3):
                yield b"x" * (1024 * 1024)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def stream(self, method, url, headers=None):
            return FakeStream()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        with pytest.raises(ValueError, match="max_bytes"):
            await service.download_resource(
                "https://www.data.gouv.fr/x.csv",
                target,
                max_bytes=2 * 1024 * 1024,
            )
        assert not target.exists()


@pytest.mark.asyncio
async def test_download_resource_writes_file(service, tmp_path):
    target = tmp_path / "small.csv"

    class FakeStream:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def raise_for_status(self):
            return None

        async def aiter_bytes(self, chunk_size=None):
            yield b"hello,world\n1,2\n"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def stream(self, method, url, headers=None):
            return FakeStream()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        result = await service.download_resource(
            "https://www.data.gouv.fr/small.csv", target
        )
        assert result == target
        assert target.exists()
        assert target.read_bytes() == b"hello,world\n1,2\n"


# ─── 404 explicite ──────────────────────────────────────────────────────


def _fake_404_client():
    class FakeStream:
        status_code = 404

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def raise_for_status(self):
            raise AssertionError("ne doit pas être appelé : 404 capturé avant")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def stream(self, method, url, headers=None):
            return FakeStream()

    return FakeClient()


@pytest.mark.asyncio
async def test_download_404_on_direct_url_suggests_latest(service, tmp_path):
    """404 sur URL directe (Azure/S3) → suggérer `latest`."""
    with patch("httpx.AsyncClient", return_value=_fake_404_client()):
        with pytest.raises(FileNotFoundError) as exc_info:
            await service.download_resource(
                "https://dcat-server.azurewebsites.net/abc/file.csv",
                tmp_path / "x.csv",
            )
    msg = str(exc_info.value)
    assert "latest" in msg.lower()
    assert "404" in msg


@pytest.mark.asyncio
async def test_download_404_on_stable_url_does_not_suggest_latest(service, tmp_path):
    """404 sur /api/1/datasets/r/<id> est déjà l'URL latest → ne pas re-suggérer."""
    with patch("httpx.AsyncClient", return_value=_fake_404_client()):
        with pytest.raises(FileNotFoundError) as exc_info:
            await service.download_resource(
                "https://www.data.gouv.fr/api/1/datasets/r/abc-123",
                tmp_path / "x.csv",
            )
    msg = str(exc_info.value)
    assert "404" in msg
    assert "Choisir une autre ressource" in msg
    # Le message doit explicitement reconnaître que c'est déjà l'URL stable
    assert "URL data.gouv stable" in msg
    # Et NE PAS dire "réessayer avec latest" (déjà latest)
    assert "Réessayer avec l'URL `latest`" not in msg


# ─── SSRF guard ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_url_safety_blocks_metadata_endpoint(service, tmp_path):
    with pytest.raises(ValueError):
        await service.download_resource(
            "http://169.254.169.254/latest/meta-data/",
            tmp_path / "x",
        )


@pytest.mark.asyncio
async def test_url_safety_blocks_file_scheme(service, tmp_path):
    with pytest.raises(ValueError):
        await service.download_resource(
            "file:///etc/passwd",
            tmp_path / "x",
        )


# ─── singleton ──────────────────────────────────────────────────────────


def test_singleton_returns_same_instance():
    a = get_datagouv_service()
    b = get_datagouv_service()
    assert a is b
