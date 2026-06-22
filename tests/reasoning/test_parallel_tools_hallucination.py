"""parallel_tools : les sous-outils RÉUSSIS doivent compter pour le guard
anti-hallucination (sinon faux positif → double-envoi mail/telegram, log 21/06)."""
from __future__ import annotations

import re

from src.reasoning.hallucination_guard import hallucination_retry_query


def _extract_parallel_subtools(obs_content: str) -> set[str]:
    """Réplique la regex du fix react.py (format obs « ✅ N. <tool>: … »)."""
    return set(re.findall(r"✅\s*\d+\.\s*([A-Za-z_]\w*)", obs_content))


def test_parallel_obs_subtools_extracted():
    obs = (
        "⚡ parallel_tools: 2 appel(s) exécuté(s)\n"
        "✅ 1. telegram_send_document: ✅ Document Telegram envoyé\n"
        "✅ 2. mail_send: ✅ Email envoyé - to: x@y.com\n"
    )
    subs = _extract_parallel_subtools(obs)
    assert subs == {"telegram_send_document", "mail_send"}


def test_guard_no_false_positive_when_subtool_present():
    # Avec mail_send propagé dans les outils réussis → PAS de retry hallucination
    tools = {"parallel_tools", "memory_search", "create_pdf", "mail_send", "telegram_send_document"}
    query = "envoie le rapport par email"
    combined = "C'est fait ! L'email a bien été envoyé à compte@x.com ✅"
    retry_q, used = hallucination_retry_query(combined, query, tools, 0)
    assert retry_q is None      # pas de faux positif
    assert used == 0


def test_guard_still_fires_when_truly_missing():
    # Sans mail_send (ni dans parallel) → le guard doit TOUJOURS détecter la fausse claim
    tools = {"memory_search", "create_pdf"}
    query = "envoie le rapport par email"
    combined = "C'est fait ! L'email a bien été envoyé à compte@x.com ✅"
    retry_q, used = hallucination_retry_query(combined, query, tools, 0)
    assert retry_q is not None  # vraie détection conservée
    assert used == 1
