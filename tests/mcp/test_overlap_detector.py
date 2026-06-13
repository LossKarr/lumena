"""Tests Phase E — `overlap_detector.detect_overlaps` (pure, deterministe)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from src.mcp.overlap_detector import (
    OverlapMatch,
    detect_overlaps,
    group_overlaps_by_mcp,
    group_overlaps_by_native,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers : MCPTool duck-typed minimal (le detector ne depend pas du vrai type)
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class _T:
    name: str
    description: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — validation des arguments
# ══════════════════════════════════════════════════════════════════════════════


class TestValidation:
    @pytest.mark.parametrize("bad", [-0.01, 1.01, "0.5", None])
    def test_threshold_out_of_range_raises(self, bad):
        with pytest.raises(ValueError, match="threshold"):
            detect_overlaps(
                server_name="srv",
                mcp_tools=[_T("t", "desc")],
                native_handler_names=["n"],
                native_descriptions={"n": "desc"},
                threshold=bad,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("bad", [0, -1, "2", 1.5])
    def test_min_shared_invalid_raises(self, bad):
        with pytest.raises(ValueError, match="min_shared_keywords"):
            detect_overlaps(
                server_name="srv",
                mcp_tools=[_T("t", "desc")],
                native_handler_names=["n"],
                native_descriptions={"n": "desc"},
                min_shared_keywords=bad,  # type: ignore[arg-type]
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — cas plan : send_email ↔ gmail send_message
# ══════════════════════════════════════════════════════════════════════════════


class TestPlanCases:
    def test_send_email_overlap_with_gmail_send_message(self):
        m = detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T(
                "send_message",
                "Send an email message to a recipient",
            )],
            native_handler_names=["send_email", "read_file"],
            native_descriptions={
                "send_email": "Send an email via SMTP to a recipient",
                "read_file": "Read a file from disk",
            },
        )
        assert len(m) == 1
        assert m[0].mcp_tool_name == "mcp__gmail__send_message"
        assert m[0].native_tool_name == "send_email"
        assert m[0].similarity_score > 0.3
        assert "email" in m[0].shared_keywords
        assert "send" in m[0].shared_keywords

    def test_web_fetch_does_not_overlap_with_weather_get_current(self):
        m = detect_overlaps(
            server_name="weather",
            mcp_tools=[_T(
                "get_current",
                "Get current weather conditions for a city",
            )],
            native_handler_names=["web_fetch"],
            native_descriptions={
                "web_fetch": "Fetch a URL and return content",
            },
        )
        assert m == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — comportements de base
# ══════════════════════════════════════════════════════════════════════════════


class TestBasicBehaviour:
    def test_empty_inputs_return_empty(self):
        assert detect_overlaps(
            server_name="",
            mcp_tools=[],
            native_handler_names=[],
            native_descriptions={},
        ) == []

    def test_no_natives_returns_empty(self):
        assert detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T("send_message", "send email")],
            native_handler_names=[],
            native_descriptions={},
        ) == []

    def test_no_mcp_tools_returns_empty(self):
        assert detect_overlaps(
            server_name="gmail",
            mcp_tools=[],
            native_handler_names=["send_email"],
            native_descriptions={"send_email": "send email"},
        ) == []

    def test_mcp_tool_without_name_is_skipped(self):
        assert detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T("", "send email")],
            native_handler_names=["send_email"],
            native_descriptions={"send_email": "send email"},
        ) == []

    def test_mcp_tool_with_already_namespaced_name_is_preserved(self):
        m = detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T("mcp__gmail__send_message", "send email message")],
            native_handler_names=["send_email"],
            native_descriptions={"send_email": "send email"},
        )
        assert len(m) == 1
        assert m[0].mcp_tool_name == "mcp__gmail__send_message"

    def test_returns_overlapmatch_instances(self):
        m = detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T("send_message", "send email to a recipient")],
            native_handler_names=["send_email"],
            native_descriptions={"send_email": "send email to recipient"},
        )
        assert all(isinstance(x, OverlapMatch) for x in m)


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — seuils
# ══════════════════════════════════════════════════════════════════════════════


class TestThreshold:
    def test_high_threshold_suppresses_weak_match(self):
        m = detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T("send_message", "send a message")],
            native_handler_names=["send_email"],
            native_descriptions={
                "send_email": "send an email via SMTP to a contact",
            },
            threshold=0.99,
        )
        assert m == []

    def test_low_threshold_allows_weaker_match(self):
        m = detect_overlaps(
            server_name="acme",
            mcp_tools=[_T("create_invoice", "create new invoice for customer")],
            native_handler_names=["billing_create_invoice"],
            native_descriptions={
                "billing_create_invoice": "create a customer invoice",
            },
            threshold=0.1,
        )
        assert len(m) >= 1

    def test_min_shared_keywords_blocks_single_token_match(self):
        # 1 seul token partage → bloque par defaut (min_shared=2).
        m = detect_overlaps(
            server_name="ops",
            mcp_tools=[_T("tool_one", "manage system inventory")],
            native_handler_names=["unrelated_tool"],
            native_descriptions={
                "unrelated_tool": "system status check",
            },
            threshold=0.05,
            min_shared_keywords=2,
        )
        # Tokens: {manage, system, inventory} & {system, status, check}
        # → 1 partage → bloque.
        assert m == []

    def test_min_shared_keywords_one_allows_single_token_match(self):
        m = detect_overlaps(
            server_name="ops",
            mcp_tools=[_T("tool_one", "manage system inventory")],
            native_handler_names=["unrelated_tool"],
            native_descriptions={
                "unrelated_tool": "system status check",
            },
            threshold=0.05,
            min_shared_keywords=1,
        )
        assert len(m) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — determinisme + tri
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_output_sorted_by_mcp_then_native(self):
        m = detect_overlaps(
            server_name="gmail",
            mcp_tools=[
                _T("send_message", "send email message recipient"),
                _T("read_message", "read email message inbox"),
            ],
            native_handler_names=["zzz_read_mail", "aaa_send_mail"],
            native_descriptions={
                "zzz_read_mail": "read email message from inbox",
                "aaa_send_mail": "send email message to recipient",
            },
        )
        # Tri stable : (mcp_name, native_name)
        keys = [(x.mcp_tool_name, x.native_tool_name) for x in m]
        assert keys == sorted(keys)

    def test_same_input_produces_same_output(self):
        inputs = dict(
            server_name="gmail",
            mcp_tools=[_T("send_message", "send email message")],
            native_handler_names=["send_email"],
            native_descriptions={"send_email": "send email message"},
        )
        first = detect_overlaps(**inputs)
        second = detect_overlaps(**inputs)
        assert first == second


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — server_name enrichit les tokens MCP
# ══════════════════════════════════════════════════════════════════════════════


class TestServerNameEnrichment:
    def test_server_name_tokens_count_for_similarity(self):
        """Sans le server_name `gmail`, `tool_x` ne matcherait pas
        `send_email`. Avec lui, le partage `gmail`↔`email`/`send` reste
        absent — donc on verifie juste qu'un nom de serveur tres specifique
        peut pousser le score au-dessus du seuil quand le natif partage des
        tokens directs."""
        with_srv = detect_overlaps(
            server_name="gmail mail send",
            mcp_tools=[_T("op", "do something")],
            native_handler_names=["send_email_mail"],
            native_descriptions={
                "send_email_mail": "send a mail via gmail",
            },
        )
        # On verifie que le path d'enrichissement par server_name fonctionne :
        # il y a un match grace aux tokens du server_name.
        assert len(with_srv) == 1
        assert "gmail" in with_srv[0].shared_keywords or "mail" in with_srv[0].shared_keywords


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — helpers group_*
# ══════════════════════════════════════════════════════════════════════════════


class TestGrouping:
    def test_group_by_mcp(self):
        m = [
            OverlapMatch("mcp__gmail__send", "send_email", 0.5, frozenset({"send"})),
            OverlapMatch("mcp__gmail__send", "send_message", 0.4, frozenset({"send"})),
            OverlapMatch("mcp__gmail__read", "read_email", 0.6, frozenset({"read"})),
        ]
        g = group_overlaps_by_mcp(m)
        assert g["mcp__gmail__send"] == frozenset({"send_email", "send_message"})
        assert g["mcp__gmail__read"] == frozenset({"read_email"})

    def test_group_by_native(self):
        m = [
            OverlapMatch("mcp__a__t", "send_email", 0.5, frozenset({"x"})),
            OverlapMatch("mcp__b__t", "send_email", 0.5, frozenset({"x"})),
        ]
        g = group_overlaps_by_native(m)
        assert g["send_email"] == frozenset({"mcp__a__t", "mcp__b__t"})

    def test_group_empty_returns_empty_dict(self):
        assert group_overlaps_by_mcp([]) == {}
        assert group_overlaps_by_native([]) == {}


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — robustesse aux entrees pourries
# ══════════════════════════════════════════════════════════════════════════════


class TestRobustness:
    def test_native_with_missing_description_uses_empty(self):
        m = detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T("send_message", "send email to recipient")],
            native_handler_names=["send_email"],
            native_descriptions={},  # description absente
        )
        # Sans description, tokens natifs = juste son nom → match possible
        # via "send" + "email" du nom.
        assert isinstance(m, list)

    def test_non_string_native_names_are_skipped(self):
        m = detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T("send_message", "send email")],
            native_handler_names=["", None, 42, "send_email"],  # type: ignore[list-item]
            native_descriptions={"send_email": "send email"},
        )
        # Seul send_email valide est considere.
        assert all(x.native_tool_name == "send_email" for x in m)

    def test_mcp_tool_with_none_description_does_not_crash(self):
        m = detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T("send_message", None)],  # type: ignore[arg-type]
            native_handler_names=["send_email"],
            native_descriptions={"send_email": "send email"},
        )
        assert isinstance(m, list)


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — contrat OverlapMatch (frozen + hashable)
# ══════════════════════════════════════════════════════════════════════════════


class TestOverlapMatchContract:
    def test_is_frozen(self):
        m = OverlapMatch("a", "b", 0.5, frozenset({"x"}))
        with pytest.raises(Exception):
            m.similarity_score = 0.9  # type: ignore[misc]

    def test_is_hashable(self):
        m = OverlapMatch("a", "b", 0.5, frozenset({"x"}))
        assert hash(m) == hash(OverlapMatch("a", "b", 0.5, frozenset({"x"})))

    def test_score_is_in_range(self):
        m = detect_overlaps(
            server_name="gmail",
            mcp_tools=[_T("send_message", "send email")],
            native_handler_names=["send_email"],
            native_descriptions={"send_email": "send email"},
        )
        for x in m:
            assert 0.0 <= x.similarity_score <= 1.0
