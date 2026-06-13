"""Tests Phase I-3 — `schema_cascade.detect_schema` (orchestration 1→4)."""
from __future__ import annotations

from src.mcp.schema_cascade import detect_schema
from src.mcp.schema_prober import ProbeOutput


class TestCascadeLevels:
    def test_level1_curated_wins(self):
        # 'slack' est dans KNOWN_MCPS → Niveau 1 priorité
        s = detect_schema(server_id="slack", intent="slack",
                          package_spec="npm:something-else")
        assert s is not None
        assert s.detected_from == "curated"
        assert "SLACK_BOT_TOKEN" in s.field_names()

    def test_level2_package_when_no_curated(self):
        readme = "```bash\nexport CUSTOM_API_KEY=abc\n```"
        def fetch(url, t): return {"readme": readme}
        s = detect_schema(
            server_id="custom",
            intent="unknown-intent-12345",
            package_spec="npm:my-custom-mcp",
            fetch_json=fetch,
        )
        assert s.detected_from == "package"
        assert "CUSTOM_API_KEY" in s.field_names()

    def test_level3_probe_when_no_package(self):
        def runner(cmd, t):
            return ProbeOutput("export RUNTIME_TOKEN=x\n", "", 0)
        s = detect_schema(
            server_id="x",
            binary_path="/fake/bin",
            probe_runner=runner,
        )
        assert s.detected_from == "probe"
        assert "RUNTIME_TOKEN" in s.field_names()

    def test_level4_snippet_fallback(self):
        s = detect_schema(
            server_id="x",
            user_snippet="MY_OBSCURE_KEY=value",
        )
        assert s.detected_from == "user"
        assert "MY_OBSCURE_KEY" in s.field_names()


class TestCascadeFallthrough:
    def test_returns_none_if_nothing(self):
        s = detect_schema(server_id="x")
        assert s is None

    def test_level1_miss_falls_to_2(self):
        readme = "```bash\nexport FALLBACK_KEY=v\n```"
        def fetch(url, t): return {"readme": readme}
        s = detect_schema(
            server_id="x",
            intent="totally-unknown",
            package_spec="npm:x",
            fetch_json=fetch,
        )
        assert s.detected_from == "package"

    def test_disabled_levels_skipped(self):
        # Si seul level=4 est activé, l'intent slack ne résout pas par
        # KNOWN_MCPS.
        s = detect_schema(
            server_id="x", intent="slack", enable_levels=(4,),
        )
        assert s is None

    def test_level3_empty_falls_to_level4(self):
        def runner(cmd, t): return ProbeOutput("", "", 0)
        s = detect_schema(
            server_id="x", binary_path="/fake",
            probe_runner=runner,
            user_snippet="WHATEVER_KEY=x",
        )
        assert s.detected_from == "user"
        assert "WHATEVER_KEY" in s.field_names()
