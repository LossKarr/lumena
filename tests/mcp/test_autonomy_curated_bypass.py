"""Phase I-7 — Verrouille le bypass auto-approve pour MCPs curated.

Le bug initial : AutoApproveEngine valide tool_name_pattern au format strict
`^mcp__server__tool$` (regex), ce qui ne peut matcher les tickets d'install
(`mcp_catalog_add:slack`, `mcp_install:slack`, `mcp_activate:slack`). Résultat :
aucun ticket d'install ne pouvait être auto-approuvé → user click forcé dans
l'UI → pas d'autonomie.

Le fix : `_is_curated_install_ticket()` reconnaît ces 3 préfixes pour les
MCPs présents dans KNOWN_MCPS. Le bypass passe par un evaluator dédié qui
ne consulte pas l'engine, mais cross-vérifie quand même le `package_spec`.

Ces tests verrouillent :
  - reconnaissance des 3 préfixes
  - rejet si server_id pas curated
  - rejet si package_spec ne matche pas le curated
  - acceptation propre slack curated
"""
from __future__ import annotations

import pytest

from src.mcp.react_integration import _is_curated_install_ticket


class TestIsCuratedInstallTicket:

    def test_catalog_add_slack_curated_ok(self):
        assert _is_curated_install_ticket(
            "mcp_catalog_add:slack",
            "slack",
            {"package_spec": "npm:@modelcontextprotocol/server-slack"},
        )

    def test_install_slack_curated_ok(self):
        assert _is_curated_install_ticket(
            "mcp_install:slack",
            "slack",
            {"package_spec": "npm:@modelcontextprotocol/server-slack"},
        )

    def test_activate_slack_curated_ok(self):
        # mcp_activate ne nécessite pas de cross-check package_spec
        assert _is_curated_install_ticket(
            "mcp_activate:slack",
            "slack",
            {},
        )

    def test_rejects_unknown_server_id(self):
        assert not _is_curated_install_ticket(
            "mcp_catalog_add:random-uncurated",
            "random-uncurated",
            {"package_spec": "npm:random-package"},
        )

    def test_rejects_wrong_package_spec(self):
        # slack curated MAIS package_spec impostor
        assert not _is_curated_install_ticket(
            "mcp_catalog_add:slack",
            "slack",
            {"package_spec": "npm:@evil/fake-slack"},
        )

    def test_rejects_unknown_prefix(self):
        # mcp_local_create n'est PAS dans la whitelist (création locale = risqué)
        assert not _is_curated_install_ticket(
            "mcp_local_create:slack",
            "slack",
            {},
        )

    def test_rejects_runtime_tool_format(self):
        # Format mcp__slack__post_message = tool runtime, pas un ticket d'install
        assert not _is_curated_install_ticket(
            "mcp__slack__post_message",
            "slack",
            {},
        )

    def test_rejects_none_inputs(self):
        assert not _is_curated_install_ticket(None, "slack", {})
        assert not _is_curated_install_ticket("mcp_install:slack", None, {})
        assert not _is_curated_install_ticket("", "", {})

    def test_accepts_install_without_package_spec_in_payload(self):
        # Le payload peut ne pas exposer package_spec ; on accepte (cross-check
        # impossible mais le server_id curated suffit comme garantie).
        assert _is_curated_install_ticket(
            "mcp_install:slack",
            "slack",
            {},
        )

    def test_all_known_mcps_recognized(self):
        # Vérifie qu'au moins les 5 plus communs sont reconnus
        from src.mcp.known_mcps import list_known_mcp_slugs
        slugs = list_known_mcp_slugs()
        assert "slack" in slugs
        assert "github" in slugs
        assert "filesystem" in slugs
        assert "memory" in slugs
        for slug in ("slack", "github", "filesystem", "memory"):
            assert _is_curated_install_ticket(
                f"mcp_catalog_add:{slug}",
                slug,
                {},
            ), f"slug={slug} devrait être reconnu curated"
