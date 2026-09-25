import pytest

from src.local_models.identifiers import parse_model_reference
from src.local_models.ticket_store import DeleteTicketError, DeleteTicketStore


def test_ticket_is_one_use_bound_to_exact_model_and_not_stored_raw(tmp_path):
    store = DeleteTicketStore(tmp_path / "tickets.json")
    ref = parse_model_reference("qwen3:8b")
    ticket, _ = store.issue(ref, {"size_bytes": 10})
    assert ticket not in (tmp_path / "tickets.json").read_text(encoding="utf-8")
    assert store.consume(ticket, ref)["canonical"] == "qwen3:8b"
    with pytest.raises(DeleteTicketError, match="delete_ticket_invalid_or_used"):
        store.consume(ticket, ref)


def test_ticket_cannot_be_retargeted(tmp_path):
    store = DeleteTicketStore(tmp_path / "tickets.json")
    ticket, _ = store.issue(parse_model_reference("qwen3:8b"), {})
    with pytest.raises(DeleteTicketError, match="delete_ticket_target_mismatch"):
        store.consume(ticket, parse_model_reference("qwen3:14b"))
