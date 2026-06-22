"""Tests A1 — identité de flotte (peer_fleet).

Vérifie : activation par clé, preuve HMAC (valide/invalide/anti-rejeu/
anti-réflexion), dérivation déterministe des tokens, clé jamais requise en clair.
"""
import pytest

from src.runtime import peer_fleet as pf


FK = "une-cle-de-flotte-longue-et-aleatoire-XYZ"
A = "instance-A"
B = "instance-B"


# ── Activation ───────────────────────────────────────────────────────────────

def test_disabled_when_no_key(monkeypatch):
    monkeypatch.delenv("LUMENA_FLEET_KEY", raising=False)
    assert pf.is_fleet_pairing_enabled() is False
    assert pf.get_fleet_key() == ""


def test_enabled_with_key(monkeypatch):
    monkeypatch.setenv("LUMENA_FLEET_KEY", FK)
    assert pf.is_fleet_pairing_enabled() is True
    assert pf.get_fleet_key() == FK


def test_key_is_stripped(monkeypatch):
    monkeypatch.setenv("LUMENA_FLEET_KEY", f"  {FK}  ")
    assert pf.get_fleet_key() == FK


# ── Nonces ───────────────────────────────────────────────────────────────────

def test_nonce_unique_and_hex():
    n1, n2 = pf.generate_nonce(), pf.generate_nonce()
    assert n1 != n2
    assert len(n1) == 32  # 128 bits hex
    int(n1, 16)  # ne lève pas


# ── Preuve : aller-retour valide ─────────────────────────────────────────────

def test_proof_roundtrip_valid():
    n_init, n_resp = "aa", "bb"
    # B (répondeur) prouve à A
    proof_b = pf.compute_proof(B, A, n_init, n_resp, fleet_key=FK)
    # A vérifie la preuve de B
    assert pf.verify_proof(proof_b, B, A, n_init, n_resp, fleet_key=FK) is True


def test_proof_mutual_distinct():
    """Mêmes nonces, mais la preuve de A ≠ celle de B (anti-réflexion)."""
    n_init, n_resp = "aa", "bb"
    proof_a = pf.compute_proof(A, B, n_init, n_resp, fleet_key=FK)
    proof_b = pf.compute_proof(B, A, n_init, n_resp, fleet_key=FK)
    assert proof_a != proof_b
    # une preuve de A ne valide pas comme si c'était B
    assert pf.verify_proof(proof_a, B, A, n_init, n_resp, fleet_key=FK) is False


# ── Preuve : rejets ──────────────────────────────────────────────────────────

def test_proof_wrong_key_rejected():
    n_init, n_resp = "aa", "bb"
    proof_b = pf.compute_proof(B, A, n_init, n_resp, fleet_key=FK)
    assert pf.verify_proof(proof_b, B, A, n_init, n_resp, fleet_key="AUTRE-CLE") is False


def test_proof_tampered_nonce_rejected():
    proof_b = pf.compute_proof(B, A, "aa", "bb", fleet_key=FK)
    # nonce modifié → rejet (anti-rejeu)
    assert pf.verify_proof(proof_b, B, A, "aa", "CC", fleet_key=FK) is False
    assert pf.verify_proof(proof_b, B, A, "XX", "bb", fleet_key=FK) is False


def test_proof_tampered_identity_rejected():
    proof_b = pf.compute_proof(B, A, "aa", "bb", fleet_key=FK)
    assert pf.verify_proof(proof_b, "instance-C", A, "aa", "bb", fleet_key=FK) is False


def test_proof_empty_or_no_key():
    assert pf.verify_proof("", B, A, "aa", "bb", fleet_key=FK) is False
    assert pf.verify_proof("deadbeef", B, A, "aa", "bb", fleet_key="") is False


# ── Tokens dérivés ───────────────────────────────────────────────────────────

def test_token_deterministic_both_sides():
    """A et B calculent le même token A→B (sans le transmettre)."""
    t1 = pf.derive_peer_token(A, B, fleet_key=FK)
    t2 = pf.derive_peer_token(A, B, fleet_key=FK)
    assert t1 == t2 and len(t1) == 64


def test_token_asymmetric():
    assert pf.derive_peer_token(A, B, fleet_key=FK) != pf.derive_peer_token(B, A, fleet_key=FK)


def test_token_depends_on_key():
    assert pf.derive_peer_token(A, B, fleet_key=FK) != pf.derive_peer_token(A, B, fleet_key="AUTRE")


def test_env_key_used_by_default(monkeypatch):
    monkeypatch.setenv("LUMENA_FLEET_KEY", FK)
    # sans fleet_key explicite → utilise l'env
    assert pf.derive_peer_token(A, B) == pf.derive_peer_token(A, B, fleet_key=FK)
    proof = pf.compute_proof(B, A, "aa", "bb")
    assert pf.verify_proof(proof, B, A, "aa", "bb") is True
