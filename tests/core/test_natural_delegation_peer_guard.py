"""Bug 2 (P2P) — Le pré-routeur de délégation naturelle ne happe plus l'inter-instance.

`_try_natural_delegation` route « demande à <X> de <tâche> » vers un sous-agent
LOCAL. Quand <X> est un AUTRE Lumena (pair), il doit RELÂCHER (return None) pour
laisser le rail peer (ReAct + peer_awareness) gérer la délégation.

Couvre :
- cible « ma Lumena salon » → relâché (None), CodeAgent local PAS appelé
- cible « agent code » / « code-moi… » → toujours délégué local (non-régression)
- cible = alias d'un pair du registre → relâché
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core_services.agent_service import AgentService


def _svc() -> AgentService:
    return AgentService(core=MagicMock())


# ── _targets_a_peer (unité) ───────────────────────────────────────────────────

class TestTargetsAPeer:

    @pytest.mark.parametrize("label", [
        "ma lumena salon", "mon autre lumena", "lumena bureau",
        "l'autre instance", "le pair", "peer salon",
    ])
    def test_keyword_targets_peer(self, label):
        assert _svc()._targets_a_peer(label) is True

    @pytest.mark.parametrize("label", [
        "code", "agent code", "développeur", "research", "fichier", "general",
    ])
    def test_local_agents_not_peer(self, label):
        assert _svc()._targets_a_peer(label) is False

    def test_alias_from_registry(self, tmp_path, monkeypatch):
        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "DATA_DIR", tmp_path)
        (tmp_path / "peer_registry.json").write_text(json.dumps({
            "peer-uuid-1": {"instance_id": "peer-uuid-1", "instance_name": "Atelier",
                            "alias": "salon", "host": "192.168.1.57", "port": 8081,
                            "trust": "trusted"},
        }), encoding="utf-8")
        # « salon » (alias) doit être reconnu comme pair
        assert _svc()._targets_a_peer("salon") is True
        # un label inconnu reste local
        assert _svc()._targets_a_peer("quelquechose") is False


# ── _try_natural_delegation (intégration) ─────────────────────────────────────

class TestNaturalDelegationGuard:

    @pytest.mark.asyncio
    async def test_peer_request_is_released_no_local_delegation(self):
        svc = _svc()
        with patch("src.agents.sub_agent.delegate_to_agent", new=AsyncMock()) as mock_delegate:
            out = await svc._try_natural_delegation(
                "Demande à ma Lumena salon de coder et exécuter un script Python."
            )
        assert out is None                      # relâché vers le rail peer
        mock_delegate.assert_not_called()       # CodeAgent local PAS appelé

    @pytest.mark.asyncio
    async def test_local_code_delegation_still_works(self):
        svc = _svc()
        with patch("src.agents.sub_agent.delegate_to_agent",
                   new=AsyncMock(return_value="fait")) as mock_delegate:
            out = await svc._try_natural_delegation(
                "demande à l'agent code de créer un script python"
            )
        assert out is not None                  # délégation locale conservée
        assert "ok" in out.lower() or "fait" in out.lower()
        mock_delegate.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_delegation_message_ignored(self):
        svc = _svc()
        out = await svc._try_natural_delegation("Quelle heure est-il ?")
        assert out is None
