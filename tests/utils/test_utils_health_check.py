"""Tests unitaires pour src/utils/health_check.py"""
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime

from src.utils.health_check import (
    HealthStatus,
    SystemHealth,
    HealthChecker,
    get_health_checker,
)


# ─── HealthStatus ──────────────────────────────────────────────────────────

class TestHealthStatus:
    def test_creation(self):
        hs = HealthStatus(name="python", healthy=True, message="Python 3.12")
        assert hs.name == "python"
        assert hs.healthy is True
        assert hs.message == "Python 3.12"
        assert isinstance(hs.timestamp, datetime)
        assert hs.details == {}

    def test_with_details(self):
        hs = HealthStatus(name="memory", healthy=False, message="low", details={"gb": 0.5})
        assert hs.details["gb"] == 0.5


# ─── SystemHealth ──────────────────────────────────────────────────────────

class TestSystemHealth:
    def test_to_dict(self):
        sh = SystemHealth(
            overall_healthy=True,
            components=[
                HealthStatus(name="python", healthy=True, message="ok"),
                HealthStatus(name="data", healthy=True, message="ok"),
            ],
        )
        d = sh.to_dict()
        assert d["healthy"] is True
        assert len(d["components"]) == 2
        assert d["components"][0]["name"] == "python"
        assert "timestamp" in d

    def test_to_dict_unhealthy(self):
        sh = SystemHealth(
            overall_healthy=False,
            components=[HealthStatus(name="data", healthy=False, message="fail")],
        )
        d = sh.to_dict()
        assert d["healthy"] is False


# ─── HealthChecker._check_python_version ───────────────────────────────────

class TestHealthCheckerPython:
    def test_current_version_passes(self):
        checker = HealthChecker()
        status = checker._check_python_version()
        assert status.name == "python_version"
        # Running on 3.10+ so should be healthy
        if sys.version_info >= (3, 10):
            assert status.healthy is True

    def test_old_version_fails(self):
        from collections import namedtuple
        checker = HealthChecker()
        VersionInfo = namedtuple('version_info', ['major', 'minor', 'micro', 'releaselevel', 'serial'])
        old_version = VersionInfo(3, 8, 0, 'final', 0)
        with patch.object(sys, "version_info", old_version):
            status = checker._check_python_version()
        assert status.healthy is False


# ─── HealthChecker._check_data_directory ───────────────────────────────────

class TestHealthCheckerDataDir:
    def test_writable_dir(self, tmp_path):
        checker = HealthChecker()
        with patch("src.utils.health_check.Path", side_effect=lambda p: tmp_path if p == "data" else Path(p)):
            status = checker._check_data_directory()
        # Should succeed since tmp_path is writable (or may use real "data" dir)
        # Just verify it returns a HealthStatus
        assert isinstance(status, HealthStatus)
        assert status.name == "data_directory"


# ─── HealthChecker._check_ollama ───────────────────────────────────────────

class TestHealthCheckerOllama:
    def test_ollama_not_running_returns_unhealthy(self):
        checker = HealthChecker()
        import httpx
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = httpx.ConnectError("refused")
            mock_client_cls.return_value = mock_client
            status = checker._check_ollama()
        assert status.name == "ollama"
        assert status.healthy is False

    def test_ollama_running_returns_healthy(self):
        checker = HealthChecker()
        import httpx
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"models": [{"name": "llama2"}, {"name": "mistral"}]}
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client
            status = checker._check_ollama()
        assert status.healthy is True
        assert "2 models" in status.message


# ─── HealthChecker._check_api_keys ─────────────────────────────────────────

class TestHealthCheckerApiKeys:
    def test_no_keys_unhealthy(self):
        checker = HealthChecker()
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "",
            "GOOGLE_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "NVIDIA_API_KEY": "",
            "MOONSHOT_API_KEY": "",
            "XAI_API_KEY": "",
            "MINIMAX_API_KEY": "",
        }):
            status = checker._check_api_keys()
        assert status.name == "api_keys"
        assert status.healthy is False

    def test_one_key_healthy(self):
        checker = HealthChecker()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test123"}):
            status = checker._check_api_keys()
        assert status.healthy is True
        assert "DeepSeek" in status.details.get("configured", [])


# ─── HealthChecker.check_quick ─────────────────────────────────────────────

class TestHealthCheckerQuick:
    def test_check_quick_returns_ok(self):
        checker = HealthChecker()
        result = checker.check_quick()
        assert result["status"] == "ok"
        assert "timestamp" in result


# ─── HealthChecker.check_all ───────────────────────────────────────────────

class TestHealthCheckerCheckAll:
    def test_check_all_returns_system_health(self):
        checker = HealthChecker()
        result = checker.check_all()
        assert isinstance(result, SystemHealth)
        assert len(result.components) > 0

    def test_check_all_has_python_component(self):
        checker = HealthChecker()
        result = checker.check_all()
        names = [c.name for c in result.components]
        assert "python_version" in names

    def test_overall_healthy_depends_on_critical(self):
        checker = HealthChecker()
        # Mock python check to healthy, data_directory to healthy
        with patch.object(checker, "_check_python_version",
                          return_value=HealthStatus("python_version", True, "ok")):
            with patch.object(checker, "_check_data_directory",
                              return_value=HealthStatus("data_directory", True, "ok")):
                with patch.object(checker, "_check_env_file",
                                  return_value=HealthStatus("env_file", False, "missing")):
                    with patch.object(checker, "_check_ollama",
                                      return_value=HealthStatus("ollama", False, "down")):
                        with patch.object(checker, "_check_memory",
                                          return_value=HealthStatus("memory", True, "ok")):
                            with patch.object(checker, "_check_api_keys",
                                              return_value=HealthStatus("api_keys", True, "ok")):
                                result = checker.check_all()
        # Critical components (python_version + data_directory) are healthy
        assert result.overall_healthy is True


# ─── get_health_checker singleton ──────────────────────────────────────────

class TestGetHealthChecker:
    def test_returns_health_checker(self):
        checker = get_health_checker()
        assert isinstance(checker, HealthChecker)
