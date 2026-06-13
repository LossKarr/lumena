"""
Tests Phase 10 — ApprovalQueue MCP.

Vérifie :
  - Init crée pending/ + decisions/ + utilise DATA_DIR par défaut
  - Clé Fernet auto-générée lazy, réutilisée entre instances
  - propose : id UUID4 hex, args chiffrés sur disque (forensique)
  - Validation action_id stricte (path traversal, slash, case)
  - list_pending / get / is_expired
  - approve : décrypte + supprime + enregistre dans decisions/
  - reject : supprime + enregistre reason
  - Expiration : auto + cleanup_expired
  - FileLock : approve concurrent → un seul APPROVED
  - Robustesse : fichier corrompu, ttl, types
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.mcp.approval_queue import (
    ApprovalDecision,
    ApprovalQueue,
    ApprovalQueueError,
    ApprovalRequest,
    ApprovalResult,
    PendingAction,
)
from src.mcp.policy import MCPPolicy
from src.services.secrets_service import SecretsService


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def secrets(tmp_path) -> SecretsService:
    """SecretsService isolé par test."""
    return SecretsService(
        secrets_dir=tmp_path / "secrets",
        master_key_path=tmp_path / ".secrets.key",
    )


@pytest.fixture
def queue(tmp_path, secrets) -> ApprovalQueue:
    return ApprovalQueue(
        queue_dir=tmp_path / "mcp_approvals",
        secrets_service=secrets,
    )


def _propose_sample(
    queue: ApprovalQueue,
    *,
    tool_name: str = "mcp__test__delete",
    args: Optional[Dict[str, Any]] = None,
    policy: MCPPolicy = MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
    caller_kind: str = "react",
    risk_summary: str = "Test action",
    ttl_s: Optional[float] = None,
) -> str:
    if args is None:
        args = {"path": "/test", "value": 42}
    return queue.propose(
        tool_name=tool_name,
        args=args,
        policy=policy,
        caller_kind=caller_kind,
        risk_summary=risk_summary,
        ttl_s=ttl_s,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Init & clé Fernet
# ──────────────────────────────────────────────────────────────────────────────


def test_init_creates_pending_and_decisions_dirs(tmp_path, secrets):
    q = ApprovalQueue(
        queue_dir=tmp_path / "queue", secrets_service=secrets,
    )
    assert q.pending_dir.exists()
    assert q.decisions_dir.exists()
    assert q.pending_dir.is_dir()
    assert q.decisions_dir.is_dir()


def test_fernet_key_auto_generated_on_first_propose(queue, secrets):
    """Clé Fernet absente initialement, générée au premier propose."""
    from src.mcp.approval_queue import _FERNET_KEY_NAME, _FERNET_KEY_SCOPE
    assert secrets.get(_FERNET_KEY_SCOPE, _FERNET_KEY_NAME) is None
    _propose_sample(queue)
    assert secrets.get(_FERNET_KEY_SCOPE, _FERNET_KEY_NAME) is not None


def test_fernet_key_reused_across_instances(tmp_path, secrets):
    """2 instances pointant sur les mêmes secrets utilisent la même clé."""
    q1 = ApprovalQueue(queue_dir=tmp_path / "q", secrets_service=secrets)
    aid = _propose_sample(q1, args={"x": "secret_payload_xyz"})
    # Nouvelle instance
    q2 = ApprovalQueue(queue_dir=tmp_path / "q", secrets_service=secrets)
    result = q2.approve(aid)
    assert result.decision == ApprovalDecision.APPROVED
    assert result.args == {"x": "secret_payload_xyz"}


def test_init_uses_data_dir_by_default():
    """Par défaut queue_dir pointe sous DATA_DIR/mcp_approvals (inspection)."""
    import inspect
    from src.mcp import approval_queue as mod
    source = inspect.getsource(mod.ApprovalQueue.__init__)
    assert "DATA_DIR" in source
    assert "_DEFAULT_QUEUE_DIRNAME" in source


# ──────────────────────────────────────────────────────────────────────────────
# Propose
# ──────────────────────────────────────────────────────────────────────────────


def test_propose_returns_uuid4_hex_id(queue):
    aid = _propose_sample(queue)
    assert isinstance(aid, str)
    assert len(aid) == 32
    # uuid4().hex est lowercase [0-9a-f]
    assert all(c in "0123456789abcdef" for c in aid)


def test_propose_creates_pending_file(queue):
    aid = _propose_sample(queue)
    assert (queue.pending_dir / f"{aid}.json").exists()


def test_propose_args_encrypted_not_plaintext_on_disk(queue):
    """Preuve forensique : les args ne sont JAMAIS sur disque en clair."""
    secret_marker = "ABSOLUTELY_NEVER_VISIBLE_ON_DISK_12345"
    aid = _propose_sample(queue, args={"payload": secret_marker})
    path = queue.pending_dir / f"{aid}.json"
    raw_text = path.read_text(encoding="utf-8")
    raw_bytes = path.read_bytes()
    assert secret_marker not in raw_text
    assert secret_marker.encode("utf-8") not in raw_bytes


def test_propose_two_actions_different_ids(queue):
    aid1 = _propose_sample(queue, tool_name="t1")
    aid2 = _propose_sample(queue, tool_name="t2")
    assert aid1 != aid2


def test_propose_with_unicode_and_nested_args(queue):
    args = {
        "msg": "Bonjour 🇫🇷 éàü",
        "nested": {"list": [1, 2, "trois"], "deep": {"key": "valué"}},
    }
    aid = _propose_sample(queue, args=args)
    result = queue.approve(aid)
    assert result.decision == ApprovalDecision.APPROVED
    assert result.args == args


# ──────────────────────────────────────────────────────────────────────────────
# Validation action_id
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_id",
    [
        "../etc/passwd",
        "a/b",
        "..\\windows",
        "",
        " ",
        "ABCDEF" * 5 + "ab",  # 32 chars mais uppercase
        "g" * 32,  # 32 chars mais 'g' hors [0-9a-f]
        "0123456789abcdef" * 2 + "x",  # 33 chars
        "0123456789abcdef",  # 16 chars (trop court)
        "0" * 32,  # 32 hex mais UUID version=0 (pas v4)
        "1" * 32,  # idem, version=1
        "12345678123412341234123456789abc",  # 32 hex random non-v4
    ],
)
def test_action_id_invalid_format_rejected(queue, bad_id):
    """get/approve/reject/is_expired refusent tout action_id non UUID4 hex."""
    with pytest.raises(ApprovalQueueError):
        queue.get(bad_id)
    with pytest.raises(ApprovalQueueError):
        queue.approve(bad_id)
    with pytest.raises(ApprovalQueueError):
        queue.reject(bad_id, reason="x")
    with pytest.raises(ApprovalQueueError):
        queue.is_expired(bad_id)


@pytest.mark.parametrize("non_str", [None, 123, 12.5, [], {}])
def test_action_id_non_string_rejected(queue, non_str):
    with pytest.raises(ApprovalQueueError):
        queue.get(non_str)  # type: ignore


def test_action_id_uuid4_real_accepted(queue):
    """Un vrai uuid4().hex passe la validation."""
    real_id = uuid.uuid4().hex
    # Pas d'erreur, retourne None car inexistant
    assert queue.get(real_id) is None


def test_action_id_uuid_version_other_than_4_rejected(queue):
    """UUID v1 / v3 / v5 sont rejetés."""
    v1 = uuid.uuid1().hex
    with pytest.raises(ApprovalQueueError):
        queue.get(v1)


def test_propose_returns_real_uuid4(queue):
    aid = _propose_sample(queue)
    parsed = uuid.UUID(aid)
    assert parsed.version == 4
    assert parsed.hex == aid


# ──────────────────────────────────────────────────────────────────────────────
# list_pending & get
# ──────────────────────────────────────────────────────────────────────────────


def test_list_pending_empty_initially(queue):
    assert queue.list_pending() == []


def test_list_pending_returns_proposed_actions(queue):
    a1 = _propose_sample(queue, tool_name="t1")
    a2 = _propose_sample(queue, tool_name="t2")
    pending = queue.list_pending()
    ids = {p.id for p in pending}
    assert a1 in ids
    assert a2 in ids
    assert len(pending) == 2


def test_get_unknown_returns_none(queue):
    # UUID hex valide mais inexistant
    aid = uuid.uuid4().hex
    assert queue.get(aid) is None


def test_get_returns_metadata_no_args(queue):
    aid = _propose_sample(queue, risk_summary="dangerous")
    p = queue.get(aid)
    assert isinstance(p, PendingAction)
    assert p.id == aid
    assert p.tool_name == "mcp__test__delete"
    assert p.policy == MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE
    assert p.risk_summary == "dangerous"
    # PendingAction dataclass : pas de champ args
    assert not hasattr(p, "args")


# ──────────────────────────────────────────────────────────────────────────────
# Approve
# ──────────────────────────────────────────────────────────────────────────────


def test_approve_returns_decrypted_args(queue):
    payload = {"key": "value", "n": 42, "list": [1, 2, 3]}
    aid = _propose_sample(queue, args=payload)
    result = queue.approve(aid)
    assert result.decision == ApprovalDecision.APPROVED
    assert result.args == payload


def test_approve_removes_from_pending(queue):
    aid = _propose_sample(queue)
    queue.approve(aid)
    assert not (queue.pending_dir / f"{aid}.json").exists()
    assert queue.get(aid) is None


def test_approve_unknown_raises(queue):
    with pytest.raises(ApprovalQueueError, match="Unknown"):
        queue.approve(uuid.uuid4().hex)


def test_approve_expired_returns_expired_decision(queue):
    # ttl très court → attend → approve doit retourner EXPIRED
    aid = _propose_sample(queue, ttl_s=0.05)
    time.sleep(0.15)
    result = queue.approve(aid)
    assert result.decision == ApprovalDecision.EXPIRED
    assert result.args is None
    assert "ttl" in (result.reason or "").lower() or "expired" in (result.reason or "").lower()


def test_approve_records_in_decisions_dir(queue):
    aid = _propose_sample(queue, tool_name="mcp__rec__approve")
    queue.approve(aid)
    decision_path = queue.decisions_dir / f"{aid}.json"
    assert decision_path.exists()
    data = json.loads(decision_path.read_text(encoding="utf-8"))
    assert data["decision"] == "approved"
    assert data["tool_name"] == "mcp__rec__approve"
    assert "decided_at" in data
    # PAS d'args dans decision audit
    assert "args" not in data
    assert "args_ciphertext" not in data


# ──────────────────────────────────────────────────────────────────────────────
# Reject
# ──────────────────────────────────────────────────────────────────────────────


def test_reject_removes_from_pending(queue):
    aid = _propose_sample(queue)
    ok = queue.reject(aid, reason="not approved by Charles")
    assert ok is True
    assert not (queue.pending_dir / f"{aid}.json").exists()
    assert queue.get(aid) is None


def test_reject_unknown_returns_false(queue):
    assert queue.reject(uuid.uuid4().hex, reason="x") is False


def test_reject_stores_reason_in_decisions_dir(queue):
    aid = _propose_sample(queue, tool_name="mcp__rec__reject")
    queue.reject(aid, reason="too risky")
    decision_path = queue.decisions_dir / f"{aid}.json"
    assert decision_path.exists()
    data = json.loads(decision_path.read_text(encoding="utf-8"))
    assert data["decision"] == "rejected"
    assert data["reason"] == "too risky"
    assert data["tool_name"] == "mcp__rec__reject"


def test_reject_decision_contains_no_args(queue):
    """Forensique : la décision rejected ne contient JAMAIS les args originaux."""
    secret_in_args = "FORENSIC_MARKER_REJECTED_999"
    aid = _propose_sample(queue, args={"payload": secret_in_args})
    queue.reject(aid, reason="reject")
    decision_path = queue.decisions_dir / f"{aid}.json"
    raw = decision_path.read_text(encoding="utf-8")
    assert secret_in_args not in raw


def test_reject_non_string_reason_raises(queue):
    aid = _propose_sample(queue)
    with pytest.raises(ApprovalQueueError):
        queue.reject(aid, reason=123)  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Expiration & cleanup
# ──────────────────────────────────────────────────────────────────────────────


def test_expired_not_in_list_pending(queue):
    aid_short = _propose_sample(queue, tool_name="short", ttl_s=0.05)
    aid_long = _propose_sample(queue, tool_name="long", ttl_s=60)
    time.sleep(0.15)
    pending_ids = {p.id for p in queue.list_pending()}
    assert aid_short not in pending_ids
    assert aid_long in pending_ids


def test_get_expired_returns_none(queue):
    aid = _propose_sample(queue, ttl_s=0.05)
    time.sleep(0.15)
    assert queue.get(aid) is None


def test_is_expired_true_after_ttl(queue):
    aid = _propose_sample(queue, ttl_s=0.05)
    time.sleep(0.15)
    assert queue.is_expired(aid) is True


def test_is_expired_false_for_unknown(queue):
    assert queue.is_expired(uuid.uuid4().hex) is False


def test_cleanup_expired_purges_pending_files_and_records(queue):
    aid_short = _propose_sample(queue, tool_name="short", ttl_s=0.05)
    aid_long = _propose_sample(queue, tool_name="long", ttl_s=60)
    time.sleep(0.15)
    count = queue.cleanup_expired()
    assert count == 1
    assert not (queue.pending_dir / f"{aid_short}.json").exists()
    assert (queue.pending_dir / f"{aid_long}.json").exists()
    # Audit decision créée
    decision_path = queue.decisions_dir / f"{aid_short}.json"
    assert decision_path.exists()
    data = json.loads(decision_path.read_text(encoding="utf-8"))
    assert data["decision"] == "expired"


def test_cleanup_expired_uses_action_lock(queue):
    """cleanup_expired prend le FileLock par action_id.

    Pattern testé : un thread tient le lock manuellement → cleanup_expired
    ne peut PAS supprimer l'action expirée tant que le lock est tenu.
    """
    from filelock import FileLock as _FL
    aid = _propose_sample(queue, ttl_s=0.05)
    time.sleep(0.15)
    # Simule un autre worker qui tient le lock
    lock_path = queue.pending_dir / f"{aid}.lock"
    external_lock = _FL(str(lock_path), timeout=0.1)
    with external_lock:
        # cleanup ne doit pas purger cette action (timeout sur le lock)
        count = queue.cleanup_expired()
        assert count == 0
        assert (queue.pending_dir / f"{aid}.json").exists()
    # Hors du with : le lock est libéré, cleanup peut purger
    count = queue.cleanup_expired()
    assert count == 1
    assert not (queue.pending_dir / f"{aid}.json").exists()


# ──────────────────────────────────────────────────────────────────────────────
# FileLock : concurrence
# ──────────────────────────────────────────────────────────────────────────────


def test_concurrent_approve_only_one_succeeds(queue):
    """Deux threads approve(id) sur le même action_id :
    un seul retourne APPROVED, l'autre lève ApprovalQueueError ('Unknown')."""
    aid = _propose_sample(queue, args={"payload": "concurrent_test"})

    results: List[Any] = []
    errors: List[Exception] = []
    lock = threading.Lock()

    def worker():
        try:
            r = queue.approve(aid)
            with lock:
                results.append(r)
        except Exception as e:  # noqa: BLE001
            with lock:
                errors.append(e)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Exactement 1 APPROVED, 1 erreur (Unknown après suppression)
    approved = [r for r in results if isinstance(r, ApprovalResult) and r.decision == ApprovalDecision.APPROVED]
    assert len(approved) == 1, (
        f"Expected exactly 1 APPROVED, got {len(approved)}. "
        f"results={results}, errors={errors}"
    )
    assert approved[0].args == {"payload": "concurrent_test"}
    # L'autre thread doit avoir levé une erreur (Unknown ou similaire)
    assert len(errors) == 1
    assert isinstance(errors[0], ApprovalQueueError)


# ──────────────────────────────────────────────────────────────────────────────
# Robustesse
# ──────────────────────────────────────────────────────────────────────────────


def test_corrupted_pending_file_skipped_gracefully(queue):
    # Crée manuellement un .json corrompu dans pending avec un UUID4 réel
    aid_bad = uuid.uuid4().hex
    (queue.pending_dir / f"{aid_bad}.json").write_text("not json {", encoding="utf-8")
    # list_pending ne doit pas crasher
    pending = queue.list_pending()
    assert all(p.id != aid_bad for p in pending)
    # get retourne None proprement (id valide, fichier corrompu → None)
    assert queue.get(aid_bad) is None


def test_default_ttl_is_30_minutes(queue):
    assert queue.default_ttl_s == 1800.0


def test_ttl_s_param_overrides_default(queue):
    aid = _propose_sample(queue, ttl_s=60)
    p = queue.get(aid)
    assert p is not None
    # Pas de crash : expires_at parsable
    from datetime import datetime
    exp = datetime.fromisoformat(p.expires_at)
    prop = datetime.fromisoformat(p.proposed_at)
    delta = (exp - prop).total_seconds()
    assert 50 < delta < 70  # ~60s


# ──────────────────────────────────────────────────────────────────────────────
# Garde-fous types propose()
# ──────────────────────────────────────────────────────────────────────────────


def test_propose_rejects_invalid_tool_name(queue):
    with pytest.raises(ApprovalQueueError):
        _propose_sample(queue, tool_name="")
    with pytest.raises(ApprovalQueueError):
        _propose_sample(queue, tool_name="   ")


def test_propose_rejects_non_dict_args(queue):
    with pytest.raises(ApprovalQueueError):
        queue.propose(
            tool_name="t",
            args="not_a_dict",  # type: ignore
            policy=MCPPolicy.READ_ONLY,
            caller_kind="react",
            risk_summary="r",
        )


def test_propose_rejects_non_mcppolicy_type(queue):
    with pytest.raises(ApprovalQueueError):
        queue.propose(
            tool_name="t",
            args={},
            policy="external_write_irreversible",  # type: ignore
            caller_kind="react",
            risk_summary="r",
        )


def test_propose_rejects_invalid_ttl(queue):
    with pytest.raises(ApprovalQueueError):
        queue.propose(
            tool_name="t",
            args={},
            policy=MCPPolicy.READ_ONLY,
            caller_kind="react",
            risk_summary="r",
            ttl_s=0,
        )
    with pytest.raises(ApprovalQueueError):
        queue.propose(
            tool_name="t",
            args={},
            policy=MCPPolicy.READ_ONLY,
            caller_kind="react",
            risk_summary="r",
            ttl_s=-10,
        )


# ─────────────────────────────────────────────────────────────────────────────
# approve_if() — auto-approval atomique côté serveur local
# ─────────────────────────────────────────────────────────────────────────────


def test_approve_if_false_leaves_pending_without_decision(queue):
    aid = _propose_sample(
        queue,
        tool_name="mcp_install:github_srv",
        args={"server_id": "github_srv"},
        policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
        caller_kind="react",
        risk_summary="mcp_install:npm:70",
    )

    result = queue.approve_if(aid, lambda request: False)

    assert result.decision == ApprovalDecision.PENDING
    assert result.args is None
    assert result.reason == "auto_approve_not_matched"
    assert queue.get(aid) is not None
    assert not any(p.name == f"{aid}.json" for p in queue.decisions_dir.glob("*.json"))


def test_approve_if_true_approves_and_returns_args(queue):
    args = {"server_id": "github_srv", "transport": "npm"}
    aid = _propose_sample(
        queue,
        tool_name="mcp_install:github_srv",
        args=args,
        policy=MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
        caller_kind="react",
        risk_summary="mcp_install:npm:70",
    )

    result = queue.approve_if(aid, lambda request: True)

    assert result.decision == ApprovalDecision.APPROVED
    assert result.args == args
    assert result.reason == "auto_approved"
    assert queue.get(aid) is None
    decisions = list(queue.decisions_dir.glob(f"{aid}.json"))
    assert len(decisions) == 1
    decision_raw = json.loads(decisions[0].read_text(encoding="utf-8"))
    assert decision_raw["decision"] == "approved"
    assert decision_raw["reason"] == "auto_approved"


def test_approve_if_evaluator_receives_decrypted_request(queue):
    seen: List[ApprovalRequest] = []
    aid = _propose_sample(
        queue,
        tool_name="mcp_activate:github_srv",
        args={"server_id": "github_srv", "action": "activate"},
        policy=MCPPolicy.LOCAL_WRITE,
        caller_kind="react",
        risk_summary="activation_required",
    )

    result = queue.approve_if(aid, lambda request: seen.append(request) or True)

    assert result.decision == ApprovalDecision.APPROVED
    assert len(seen) == 1
    request = seen[0]
    assert request.id == aid
    assert request.tool_name == "mcp_activate:github_srv"
    assert request.policy == MCPPolicy.LOCAL_WRITE
    assert request.caller_kind == "react"
    assert request.risk_summary == "activation_required"
    assert request.args == {"server_id": "github_srv", "action": "activate"}


def test_approve_if_evaluator_exception_keeps_pending(queue):
    aid = _propose_sample(queue, tool_name="mcp_install:github_srv")

    def boom(_request):
        raise RuntimeError("SECRET_AUTO_APPROVE_EVALUATOR_LEAK")

    with pytest.raises(ApprovalQueueError) as exc:
        queue.approve_if(aid, boom)

    assert "Auto-approval evaluator failed" in str(exc.value)
    assert "SECRET_AUTO_APPROVE_EVALUATOR_LEAK" not in str(exc.value)
    assert queue.get(aid) is not None


def test_approve_if_expired_records_expired_without_evaluator(queue):
    aid = _propose_sample(queue, ttl_s=0.01)
    time.sleep(0.03)
    called = False

    def evaluator(_request):
        nonlocal called
        called = True
        return True

    result = queue.approve_if(aid, evaluator)

    assert result.decision == ApprovalDecision.EXPIRED
    assert result.reason == "ttl reached before approval"
    assert called is False
    assert queue.get(aid) is None
