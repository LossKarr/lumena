"""Tests Phase I-3 Niveau 3 — `schema_prober.probe_schema_from_binary`."""
from __future__ import annotations

import pytest

from src.mcp.schema_prober import ProbeOutput, probe_schema_from_binary


class TestProbeBasic:
    def test_help_output_with_env_vars(self):
        def fake_runner(cmd, t):
            return ProbeOutput(
                stdout="Usage: mcp-server\n\n```bash\nexport MY_API_KEY=abc\n```\n",
                stderr="",
                returncode=0,
            )
        s = probe_schema_from_binary(
            server_id="x", binary_path="/fake/bin", runner=fake_runner,
        )
        assert s is not None
        assert "MY_API_KEY" in s.field_names()
        assert s.detected_from == "probe"

    def test_stderr_is_also_parsed(self):
        def fake_runner(cmd, t):
            return ProbeOutput(
                stdout="",
                stderr="```bash\nexport FOO_TOKEN=x\n```",
                returncode=0,
            )
        s = probe_schema_from_binary(
            server_id="x", binary_path="/fake", runner=fake_runner,
        )
        assert s is not None
        assert "FOO_TOKEN" in s.field_names()

    def test_runner_failure_returns_none(self):
        def boom(cmd, t):
            raise RuntimeError("subprocess crashed")
        assert probe_schema_from_binary(
            server_id="x", binary_path="/fake", runner=boom,
        ) is None

    def test_no_output_returns_none(self):
        def empty(cmd, t):
            return ProbeOutput("", "", 0)
        assert probe_schema_from_binary(
            server_id="x", binary_path="/fake", runner=empty,
        ) is None

    def test_output_without_env_vars_returns_none(self):
        def runner(cmd, t):
            return ProbeOutput("Usage: x [options]\nHelp text only.", "", 0)
        assert probe_schema_from_binary(
            server_id="x", binary_path="/fake", runner=runner,
        ) is None


class TestProbeArgsBounds:
    def test_empty_server_id(self):
        assert probe_schema_from_binary(
            server_id="", binary_path="/fake", runner=lambda c, t: ProbeOutput("", "", 0),
        ) is None

    def test_empty_binary_path(self):
        assert probe_schema_from_binary(
            server_id="x", binary_path="", runner=lambda c, t: ProbeOutput("", "", 0),
        ) is None
