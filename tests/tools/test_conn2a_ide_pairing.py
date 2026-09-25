"""Authentication proofs independent of transport and OS secret persistence."""

import hashlib
import hmac

import pytest

from src.tools.ide_pairing import PairingAuthority, PairingCredentials, PairingError


def response(challenge, secret=b"k" * 32):
    nonce = "ab" * 32
    transcript = "\n".join((
        "lumena-ide/auth/1", "client", challenge["key_id"],
        challenge["session_id"], challenge["nonce"], nonce,
    )).encode("ascii")
    return {
        "type": "auth_response", "session_id": challenge["session_id"],
        "client_nonce": nonce, "proof": hmac.new(secret, transcript, hashlib.sha256).hexdigest(),
    }


@pytest.fixture
def authority():
    current = [PairingCredentials("01" * 16, b"k" * 32)]
    clock = [10.0]
    return PairingAuthority(lambda: current[0], monotonic=lambda: clock[0]), current, clock


def test_challenge_proves_both_roles_and_does_not_contain_secret(authority):
    auth, _, _ = authority
    challenge = auth.begin("127.0.0.1")
    reply = response(challenge)
    session, acknowledgement = auth.finish(challenge["session_id"], reply)
    server_transcript = "\n".join((
        "lumena-ide/auth/1", "server", challenge["key_id"],
        challenge["session_id"], challenge["nonce"], reply["client_nonce"],
    )).encode("ascii")
    assert acknowledgement == {
        "type": "auth_ok", "session_id": session.session_id,
        "key_id": session.key_id,
        "proof": hmac.new(b"k" * 32, server_transcript, hashlib.sha256).hexdigest(),
    }
    assert auth.is_current(session)
    assert acknowledgement["proof"] != reply["proof"]
    assert "secret" not in challenge
    assert "kkkk" not in repr(session)


@pytest.mark.parametrize("peer", ["192.168.1.3", "example.org", "0.0.0.0", "127.0.0.1.evil"])
def test_only_literal_loopback_peers_can_begin(authority, peer):
    with pytest.raises(PairingError):
        authority[0].begin(peer)


def test_wrong_secret_consumes_challenge_and_does_not_allow_retry(authority):
    auth, _, _ = authority
    challenge = auth.begin("::1")
    with pytest.raises(PairingError):
        auth.finish(challenge["session_id"], response(challenge, b"z" * 32))
    with pytest.raises(PairingError):
        auth.finish(challenge["session_id"], response(challenge))


def test_successful_response_cannot_be_replayed(authority):
    auth, _, _ = authority
    challenge = auth.begin("127.0.0.1")
    reply = response(challenge)
    auth.finish(challenge["session_id"], reply)
    with pytest.raises(PairingError):
        auth.finish(challenge["session_id"], reply)


def test_response_from_another_socket_challenge_is_rejected(authority):
    auth, _, _ = authority
    first = auth.begin("127.0.0.1")
    second = auth.begin("127.0.0.1")
    with pytest.raises(PairingError):
        auth.finish(first["session_id"], response(second))
    assert auth.finish(second["session_id"], response(second))[0]


def test_expired_challenge_is_rejected(authority):
    auth, _, clock = authority
    challenge = auth.begin("127.0.0.1")
    clock[0] += 60
    with pytest.raises(PairingError):
        auth.finish(challenge["session_id"], response(challenge))


@pytest.mark.parametrize("mutate", [
    lambda reply: {**reply, "extra": "ignored?"},
    lambda reply: {**reply, "proof": "0" * 64},
    lambda reply: {**reply, "proof": 10},
    lambda reply: {**reply, "client_nonce": "ab"},
    lambda reply: {**reply, "type": "command"},
    lambda reply: [],
])
def test_malformed_proofs_fail_closed(authority, mutate):
    auth, _, _ = authority
    challenge = auth.begin("127.0.0.1")
    with pytest.raises(PairingError):
        auth.finish(challenge["session_id"], mutate(response(challenge)))


def test_rotation_and_revocation_invalidate_pending_and_active_sessions(authority):
    auth, current, _ = authority
    challenge = auth.begin("127.0.0.1")
    session, _ = auth.finish(challenge["session_id"], response(challenge))
    pending = auth.begin("127.0.0.1")
    current[0] = PairingCredentials("02" * 16, b"z" * 32)
    assert not auth.is_current(session)
    with pytest.raises(PairingError):
        auth.finish(pending["session_id"], response(pending))
    current[0] = None
    assert not auth.is_current(session)
    with pytest.raises(PairingError):
        auth.begin("127.0.0.1")


def test_pending_challenges_are_bounded_and_expired_entries_are_reclaimed(authority):
    _, current, clock = authority
    auth = PairingAuthority(lambda: current[0], monotonic=lambda: clock[0], max_pending=2)
    auth.begin("127.0.0.1")
    auth.begin("127.0.0.1")
    with pytest.raises(PairingError):
        auth.begin("127.0.0.1")
    clock[0] += 60
    assert auth.begin("127.0.0.1")


def test_credentials_reject_weak_keys_and_do_not_print_them():
    with pytest.raises(ValueError):
        PairingCredentials("01" * 16, b"short")
    assert "kkkk" not in repr(PairingCredentials("01" * 16, b"k" * 32))


def test_storage_failure_does_not_disclose_sensitive_error():
    def unavailable():
        raise RuntimeError("secret material")

    with pytest.raises(PairingError, match="^pairing_unavailable$"):
        PairingAuthority(unavailable).begin("127.0.0.1")


def test_disconnected_socket_releases_challenge_capacity(authority):
    _, current, _ = authority
    auth = PairingAuthority(lambda: current[0], max_pending=1)
    challenge = auth.begin("127.0.0.1")
    auth.discard(challenge["session_id"])
    assert auth.begin("127.0.0.1")
    with pytest.raises(PairingError):
        auth.finish(challenge["session_id"], response(challenge))
