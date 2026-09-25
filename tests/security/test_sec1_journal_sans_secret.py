"""Lot SEC-1 - le journal ne porte aucun secret.

Exigence du poste `revue_securite` de CONN-9 : « revue securite et **journal sans
secret** ». Le harnais le declarait « non verifie, revue humaine requise » et personne
n'avait regarde le journal.

--- La mesure du 24 septembre 2026 ---

Une recherche de motifs de jetons sur `data/logs/lumena.log` a rendu UN resultat :

    whsec_8895ae0e...   le webhook signing secret Stripe, EN CLAIR

Il venait de la sortie brute du CLI Stripe, journalisee telle quelle :

    [StripeCLI] Ready! ... Your webhook signing secret is whsec_8895ae0e<...>

Et la ligne SUIVANTE, ecrite par Lumena, disait :

    [StripeCLI] Webhook secret capture: whsec_***

Le masquage existait donc - applique au secret que Lumena manipule, pas a la sortie
du processus. Mieux : `_WHSEC_RE`, le motif qui sait reconnaitre ces jetons, etait
dans le fichier **depuis le debut**. Il servait a les CAPTURER, jamais a les cacher.
Septieme occurrence du meme motif dans cette journee : le fait existait, et il ne
decidait pas.

Un secret dans un journal est durable : les journaux sont archives, compresses,
copies et lus longtemps apres. Ce test empeche la recidive.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.services.stripe_cli import _masquer_secrets

RACINE = Path(__file__).resolve().parents[2]

# Le secret reel a fuite sous cette forme. On garde sa forme, pas sa valeur.
LIGNE_REELLE = (
    "Ready! You are using Stripe API Version [2024-12-18.acacia]. Your webhook "
    "signing secret is whsec_0123456789abcdef0123456789abcdef0123456789abcdef "
    "(^C to quit)"
)


# ── 1. Le masquage fait son travail ─────────────────────────────────────────

def test_le_secret_qui_a_fuite_est_desormais_masque():
    propre = _masquer_secrets(LIGNE_REELLE)
    assert "whsec_0123456789abcdef" not in propre, "le secret passe encore en clair"
    assert "whsec_***" in propre, f"masquage absent : {propre}"


def test_le_prefixe_reste_lisible_pour_le_diagnostic():
    """Masquer ne doit pas rendre le journal inutile : on doit savoir QUEL secret."""
    propre = _masquer_secrets(LIGNE_REELLE)
    assert propre.startswith("Ready! You are using Stripe API Version")
    assert "(^C to quit)" in propre, "le reste de la ligne doit survivre"


@pytest.mark.parametrize("jeton", [
    "whsec_aaaaaaaaaaaaaaaaaaaa",
    "sk_live_bbbbbbbbbbbbbbbbbbbb",
    "sk_test_cccccccccccccccccccc",
    "rk_live_dddddddddddddddddddd",
])
def test_toutes_les_familles_de_jetons_sont_masquees(jeton):
    """Un secret qu'on n'a pas encore vu fuir n'est pas un secret qui ne fuitera pas."""
    propre = _masquer_secrets(f"clef={jeton} suite")
    assert jeton not in propre, f"{jeton[:8]} passe en clair"
    assert "***" in propre
    assert propre.endswith(" suite"), "le masquage a mange le reste de la ligne"


def test_une_ligne_sans_secret_nest_pas_alteree():
    """Le masquage ne doit pas abimer le journal ordinaire."""
    ligne = "[StripeCLI] --> charge.succeeded [evt_1234] 200 OK"
    assert _masquer_secrets(ligne) == ligne


# ── 2. Le point d'emission est bien protege ─────────────────────────────────

def test_la_sortie_brute_du_cli_nest_jamais_journalisee_telle_quelle():
    """Le defaut etait a l'emission, pas dans le masquage : c'est la qu'on le fige.

    Un test qui verifie seulement `_masquer_secrets` laisserait revenir le defaut
    par un `logger.debug(text)` ajoute ailleurs dans la boucle de lecture.
    """
    source = (RACINE / "src" / "services" / "stripe_cli.py").read_text(encoding="utf-8")
    debut = source.index("def _reader_thread")
    corps = source[debut:source.index("def ", debut + 10)]
    fautes = [ligne.strip() for ligne in corps.splitlines()
              if re.search(r"logger\.\w+\(f?\"[^\"]*\{text\}", ligne)]
    assert not fautes, f"la ligne brute est journalisee : {fautes}"
    assert "_masquer_secrets(text)" in corps, "la sortie du CLI n'est plus masquee"


# ── 3. Le journal REEL de cette machine ─────────────────────────────────────

MOTIFS = re.compile(
    r"\b(?:whsec|sk_live|sk_test|rk_live|rk_test)_[A-Za-z0-9]{20,}"
    r"|\bxoxb-[A-Za-z0-9-]{20,}"
    r"|\bghp_[A-Za-z0-9]{30,}"
    r"|\bAIza[A-Za-z0-9_-]{30,}"
)


def test_le_journal_courant_ne_contient_aucun_secret():
    """Le constat sur le reel. Il se skippe si le journal n'existe pas - jamais
    il ne fait passer une absence de fichier pour une absence de fuite."""
    journal = RACINE / "data" / "logs" / "lumena.log"
    if not journal.exists() or journal.stat().st_size == 0:
        pytest.skip("aucun journal sur cette machine — rien a constater")
    trouves = set()
    with journal.open(encoding="utf-8", errors="ignore") as flux:
        for ligne in flux:
            for m in MOTIFS.findall(ligne):
                trouves.add(m[:10])
    assert not trouves, (
        f"{len(trouves)} secret(s) en clair dans le journal : {sorted(trouves)} — "
        "purger le journal et verifier le point d'emission"
    )
