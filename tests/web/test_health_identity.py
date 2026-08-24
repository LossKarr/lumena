from __future__ import annotations

import pytest

from src.version import __version__
from web.routes.system import health


@pytest.mark.asyncio
async def test_health_keeps_liveness_and_exposes_build_identity() -> None:
    result = await health()

    assert result["status"] == "ok"
    assert result["version"] == __version__
    assert result["commit"]
    assert result["guard_smoke_profile"] == "update-v1"
    assert isinstance(result["managed_manifest_sha256"], str)
    assert result["data_schema_version"] >= 1
