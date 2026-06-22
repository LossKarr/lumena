"""A2 — Tests unitaires de la signature d'enveloppe inter-pairs (crypto pure)."""
from __future__ import annotations

import time

import pytest

from src.runtime.peer_signing import (
    NonceCache,
    canonical_payload,
    generate_signature_nonce,
    is_signing_enabled,
    is_timestamp_fresh,
    now_ts,
    reset_nonce_cache,
    seen_nonce,
    sign_envelope,
    verify_envelope_signature,
)

_KEY = "fleet-secret-abc123"
_A = "instance-aaaa"
_B = "instance-bbbb"


@pytest.fixture(autouse=True)
def _isolate_nonce_cache():
    reset_nonce_cache()
    yield
    reset_nonce_cache()


# ── Sérialisation canonique ──────────────────────────────────────────────────

def test_canonical_payload_stable_regardless_of_key_order():
    a = canonical_payload({"b": 1, "a": 2, "nested": {"y": 1, "x": 2}})
    b = canonical_payload({"a": 2, "nested": {"x": 2, "y": 1}, "b": 1})
    assert a == b


def test_canonical_payload_is_compact_and_unicode():
    assert canonical_payload({"msg": "héllo", "n": 1}) == '{"msg":"héllo","n":1}'


# ── Round-trip signature ─────────────────────────────────────────────────────

def test_sign_then_verify_roundtrip():
    canon = canonical_payload({"task": "x", "prompt": "salut"})
    ts, nonce = now_ts(), generate_signature_nonce()
    sig = sign_envelope(canon, from_id=_A, to_id=_B, ts=ts, nonce=nonce, fleet_key=_KEY)
    assert sig
    assert verify_envelope_signature(
        sig, canon, from_id=_A, to_id=_B, ts=ts, nonce=nonce, fleet_key=_KEY
    )


def test_signature_is_directional():
    """A→B et B→A ne produisent pas la même signature (clé dérivée directionnelle)."""
    canon = canonical_payload({"x": 1})
    ts, nonce = now_ts(), generate_signature_nonce()
    sig_ab = sign_envelope(canon, from_id=_A, to_id=_B, ts=ts, nonce=nonce, fleet_key=_KEY)
    sig_ba = sign_envelope(canon, from_id=_B, to_id=_A, ts=ts, nonce=nonce, fleet_key=_KEY)
    assert sig_ab != sig_ba
    # Vérifier avec les rôles inversés échoue.
    assert not verify_envelope_signature(
        sig_ab, canon, from_id=_B, to_id=_A, ts=ts, nonce=nonce, fleet_key=_KEY
    )


# ── Altérations → rejet ──────────────────────────────────────────────────────

@pytest.mark.parametrize("mutate", ["payload", "from", "to", "ts", "nonce"])
def test_tampering_breaks_signature(mutate):
    canon = canonical_payload({"task": "x"})
    ts, nonce = now_ts(), generate_signature_nonce()
    sig = sign_envelope(canon, from_id=_A, to_id=_B, ts=ts, nonce=nonce, fleet_key=_KEY)

    kw = dict(from_id=_A, to_id=_B, ts=ts, nonce=nonce, fleet_key=_KEY)
    body = canon
    if mutate == "payload":
        body = canonical_payload({"task": "y"})
    elif mutate == "from":
        kw["from_id"] = "instance-cccc"
    elif mutate == "to":
        kw["to_id"] = "instance-cccc"
    elif mutate == "ts":
        kw["ts"] = str(int(ts) + 1)
    elif mutate == "nonce":
        kw["nonce"] = generate_signature_nonce()

    assert not verify_envelope_signature(sig, body, **kw)


def test_wrong_fleet_key_fails():
    canon = canonical_payload({"x": 1})
    ts, nonce = now_ts(), generate_signature_nonce()
    sig = sign_envelope(canon, from_id=_A, to_id=_B, ts=ts, nonce=nonce, fleet_key=_KEY)
    assert not verify_envelope_signature(
        sig, canon, from_id=_A, to_id=_B, ts=ts, nonce=nonce, fleet_key="autre-cle"
    )


def test_absent_key_yields_empty_sig_and_false_verify():
    canon = canonical_payload({"x": 1})
    ts, nonce = now_ts(), generate_signature_nonce()
    assert sign_envelope(canon, from_id=_A, to_id=_B, ts=ts, nonce=nonce, fleet_key="") == ""
    assert not verify_envelope_signature(
        "", canon, from_id=_A, to_id=_B, ts=ts, nonce=nonce, fleet_key=""
    )


def test_empty_signature_rejected():
    canon = canonical_payload({"x": 1})
    ts, nonce = now_ts(), generate_signature_nonce()
    assert not verify_envelope_signature(
        "", canon, from_id=_A, to_id=_B, ts=ts, nonce=nonce, fleet_key=_KEY
    )


# ── Fenêtre temporelle ───────────────────────────────────────────────────────

def test_timestamp_fresh_within_window():
    now = 1_000_000.0
    assert is_timestamp_fresh(str(int(now)), now=now, window=120)
    assert is_timestamp_fresh(str(int(now) - 119), now=now, window=120)
    assert is_timestamp_fresh(str(int(now) + 119), now=now, window=120)


def test_timestamp_stale_or_future_rejected():
    now = 1_000_000.0
    assert not is_timestamp_fresh(str(int(now) - 200), now=now, window=120)
    assert not is_timestamp_fresh(str(int(now) + 200), now=now, window=120)


def test_timestamp_unparseable_rejected():
    assert not is_timestamp_fresh("pas-un-nombre", now=1_000_000.0, window=120)
    assert not is_timestamp_fresh("", now=1_000_000.0, window=120)


# ── Cache de nonces (anti-rejeu single-use) ──────────────────────────────────

def test_nonce_first_seen_then_replay():
    assert seen_nonce("nonce-1") is False  # première fois
    assert seen_nonce("nonce-1") is True   # rejeu


def test_empty_nonce_is_treated_as_replay():
    assert seen_nonce("") is True


def test_nonce_cache_expiry():
    cache = NonceCache(ttl_seconds=10)
    assert cache.seen("n", now=1000.0) is False
    assert cache.seen("n", now=1005.0) is True       # encore dans le TTL → rejeu
    assert cache.seen("n", now=1011.0) is False      # expiré → re-acceptable


def test_nonce_cache_bounded():
    cache = NonceCache(ttl_seconds=10_000, max_size=100)
    for i in range(500):
        cache.seen(f"n{i}", now=1000.0)
    assert len(cache._seen) <= 100


# ── Flag d'activation ────────────────────────────────────────────────────────

def test_signing_enabled_by_default(monkeypatch):
    monkeypatch.delenv("LUMENA_PEER_SIGNING", raising=False)
    assert is_signing_enabled() is True


def test_signing_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_SIGNING", "0")
    assert is_signing_enabled() is False
