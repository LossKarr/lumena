"""L1 — num_ctx Ollama STABLE (fonction pure).

Leçon d'un log runtime : un num_ctx qui VARIE entre 2 requêtes force Ollama à
RECHARGER le modèle (taille de contexte gravée dans le runner) → annule
keep_alive. On envoie donc une valeur STABLE par modèle = min(ctx_modèle, CAP).

N'affecte QUE le chemin Ollama (dispatch séparé des providers cloud).
"""
import pytest

from src.llm.multi_provider import (
    _resolve_ollama_num_ctx as num_ctx,
    _OLLAMA_NUM_CTX_CAP,
)


def test_stable_independant_du_prompt():
    """Cœur du fix : la valeur ne dépend QUE du modèle, jamais du prompt →
    deux requêtes au même modèle donnent le MÊME num_ctx → pas de reload."""
    a = num_ctx(262144)   # gros ctx modèle (gemma4)
    b = num_ctx(262144)   # même modèle, autre requête
    assert a == b == _OLLAMA_NUM_CTX_CAP  # 16384, stable


def test_cap_par_defaut():
    assert num_ctx(262144) == _OLLAMA_NUM_CTX_CAP  # capé à 16384
    assert num_ctx(32768) == _OLLAMA_NUM_CTX_CAP    # qwen 32k → capé à 16384


def test_borne_au_ctx_du_modele_si_plus_petit():
    # un modèle 8k ne peut pas faire 16384 → on respecte sa limite
    assert num_ctx(8192) == 8192
    assert num_ctx(4096) == 4096


def test_jamais_32768_aveugle():
    # garde-fou anti-régression : l'ancien bug = 32768 fixe (débordait + churn)
    assert num_ctx(262144) != 32768
    assert num_ctx(262144) < 32768


def test_override_env_power_user():
    assert num_ctx(262144, env_override="32768") == 32768   # gros GPU veut +
    assert num_ctx(262144, env_override="8192") == 8192      # GPU modeste veut -


def test_override_env_invalide_ignore():
    assert num_ctx(262144, env_override="abc") == _OLLAMA_NUM_CTX_CAP
    assert num_ctx(262144, env_override="0") == _OLLAMA_NUM_CTX_CAP
    assert num_ctx(262144, env_override=None) == _OLLAMA_NUM_CTX_CAP


def test_ctx_modele_inconnu_fallback_cap():
    assert num_ctx(None) == _OLLAMA_NUM_CTX_CAP
    assert num_ctx(0) == _OLLAMA_NUM_CTX_CAP
