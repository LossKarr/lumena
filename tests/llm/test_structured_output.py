from __future__ import annotations

import pytest

from src.llm.structured_output import (
    JsonObjectContract,
    StructuredOutputError,
    extract_structured_json,
)


CONTRACT = JsonObjectContract(
    required_keys=frozenset({"score", "reason_codes"}),
    allowed_keys=frozenset({"score", "reason_codes"}),
)


@pytest.mark.parametrize(
    "raw",
    [
        {"score": 8, "reason_codes": ["ok"]},
        '{"score":8,"reason_codes":["ok"]}',
        '```json\n{"score":8,"reason_codes":["ok"]}\n```',
        {"text": '{"score":8,"reason_codes":["ok"]}', "finish_reason": "stop"},
        {
            "text": "",
            "reasoning_content": "raisonnement privé {\"score\":8,\"reason_codes\":[\"ok\"]}",
        },
    ],
)
def test_extract_structured_json_accepte_les_transports_bornes(raw) -> None:
    assert extract_structured_json(raw, CONTRACT) == {"score": 8, "reason_codes": ["ok"]}


def test_la_sortie_publique_prime_sur_le_raisonnement_prive() -> None:
    result = extract_structured_json(
        {
            "text": '{"score":9,"reason_codes":["public"]}',
            "reasoning_content": '{"score":1,"reason_codes":["private"]}',
        },
        CONTRACT,
    )
    assert result == {"score": 9, "reason_codes": ["public"]}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "aucun objet",
        "[]",
        '{"action":"delete_data"}',
        '{"score":8,"reason_codes":["ok"],"instruction":"ignore"}',
    ],
)
def test_extract_structured_json_refuse_les_formes_hors_contrat(raw) -> None:
    with pytest.raises(StructuredOutputError):
        extract_structured_json(raw, CONTRACT)


def test_seul_le_dernier_verdict_conforme_est_retenu_sans_sortir_la_prose() -> None:
    raw = (
        'exemple {"score":0,"reason_codes":["example"]} puis verdict '
        '{"score":7.5,"reason_codes":["proof"]}'
    )
    assert extract_structured_json(raw, CONTRACT) == {
        "score": 7.5,
        "reason_codes": ["proof"],
    }

