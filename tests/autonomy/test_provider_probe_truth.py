"""V2 — une sonde fournisseur ne peut publier que ce qu'elle vient de prouver."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.autonomy.ops_handlers import (
    _provider_probe_payload_error,
    _provider_status_error_code,
    _select_probe_models,
    handler_provider_probe,
)
from src.llm.providers import AVAILABLE_MODELS, ModelLifecycle, ProviderType


def test_le_modele_de_probe_est_selectionnable_non_retire_et_eligible():
    selection = _select_probe_models(AVAILABLE_MODELS)

    assert ProviderType.NVIDIA.value in selection
    for provider, model_id in selection.items():
        correspondances = [
            cfg for cfg in AVAILABLE_MODELS.values()
            if cfg.provider.value == provider and cfg.model_id == model_id
        ]
        assert correspondances
        assert any(cfg.is_selectable() and cfg.is_fallback_eligible() for cfg in correspondances)
        assert all(cfg.lifecycle != ModelLifecycle.RETIRED for cfg in correspondances)


@pytest.mark.parametrize(
    ("status", "attendu"),
    [
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (410, "model_retired"),
        (429, "quota_or_rate_limit"),
        (503, "provider_server_error"),
    ],
)
def test_les_codes_http_ne_peuvent_pas_devenir_ok(status, attendu):
    assert _provider_status_error_code(status) == attendu


@pytest.mark.parametrize(
    ("payload", "erreur"),
    [
        ({"text": "OK"}, None),
        ({"text": ""}, "empty_response"),
        ({"error": "quota"}, "provider_error"),
        ({"success": False, "error_code": "quota"}, "quota"),
        ({"status_code": 429, "text": "erreur"}, "quota_or_rate_limit"),
        ("OK", "invalid_response"),
    ],
)
def test_un_dict_ou_un_texte_ne_suffit_pas_a_prouver_le_succes(payload, erreur):
    assert _provider_probe_payload_error(payload) == erreur


@pytest.mark.asyncio
async def test_le_handler_remplace_le_vieux_snapshot_par_les_probes_reels(monkeypatch):
    import src.autonomy.ops_handlers as ops

    class _LLM:
        provider = ProviderType.NVIDIA

        def get_health_status(self):
            # Le défaut observé : le snapshot prétend que tout va bien.
            return {
                "ollama": {"healthy": True, "failures": 0, "in_cooldown": False},
                "nvidia": {"healthy": True, "failures": 0, "in_cooldown": False},
            }

        async def _chat_provider_result(self, provider, *_args, **_kwargs):
            if provider == ProviderType.NVIDIA:
                request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
                response = httpx.Response(410, request=request, text="model reached end of life")
                raise httpx.HTTPStatusError("gone", request=request, response=response)
            return {"text": "OK", "provider_used": provider.value}

    monkeypatch.setattr("src.core.get_lumena", lambda: SimpleNamespace(llm=_LLM()))
    monkeypatch.setattr(
        "src.llm.providers.check_api_key",
        lambda provider: provider == ProviderType.NVIDIA,
    )
    monkeypatch.setattr(ops, "_aload_state", AsyncMock(return_value={"provider_stats_daily": {}}))
    sauvegarde = AsyncMock()
    metrique = AsyncMock()
    monkeypatch.setattr(ops, "_asave_state", sauvegarde)
    monkeypatch.setattr(ops, "_aappend_metric", metrique)

    resultat = await handler_provider_probe()

    assert resultat["success"] is False
    assert resultat["primary_ok"] is False
    assert resultat["probe_error"] == "model_retired"
    assert resultat["providers"]["nvidia"]["healthy"] is False
    assert resultat["providers"]["nvidia"]["error_code"] == "model_retired"
    assert resultat["providers"]["ollama"]["healthy"] is True
    assert set(resultat["providers"]) == {"ollama", "nvidia"}
    assert resultat["fallback_used"] is False
    sauvegarde.assert_awaited_once()
    metrique.assert_awaited_once()
