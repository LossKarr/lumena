r"""Lot VIS-1 - les modeles de vision locaux servent de REPLI, jamais de defaut.

--- Ce que Charles a constate, et qui etait juste ---

« ils devraient quand meme utiliser les modeles de vision pour voir ». Mesure : Ollama a
bien `minicpm-v:latest` et `llava:latest` installes, et ils FONCTIONNENT — 14 appels
`vision-cascade ✅ Ollama minicpm-v:latest` reussis dans le journal du 28-29/09.

Mais ces 14 appels viennent TOUS de `browser.py` (`browser_navigate` x10,
`browser_click_index`, `screenshot`), qui passe par `describe_image_cascade`. **L'autre
chemin de vision — `computer_use/vision.py`, celui de `screenshot_analyze` — ne les
appelait jamais** : sa cascade etait

    ['anthropic', 'google', 'openai', 'xai']        puis OCR local

Or les comptes API de Charles sont a sec (mesure du 29/09 : 26 x HTTP 429, 9 x 403 sur la
generation d'image, DeepSeek en 402). Les quatre fournisseurs echouaient donc, et la
cascade tombait sur l'**OCR**, qui lit du texte mais ne VOIT rien.

--- Ce que ce lot fait : rien coder, un reglage ---

Le mecanisme existait **deja en entier**, documente dans `build_vision_policy` :

    LUMENA_CU_OLLAMA_VISION=1 + mode hybrid → ajoute "ollama" en queue cloud

Il etait simplement desactive (`default: "0"`, et `.env` a vide). Mesure avant/apres :

    avant : ['anthropic', 'google', 'openai', 'xai']
    apres : ['anthropic', 'google', 'openai', 'xai', 'ollama']

--- La consigne de Charles, respectee a la lettre ---

« que les modeles de vision puissent voir s'il faut, et si aucun n'est disponible sinon on
doit rester comme d'hab ». Ollama arrive donc **EN QUEUE** : les clouds passent d'abord, le
comportement habituel ne change pas, et l'OCR garde sa place de tout dernier recours.
"""
from __future__ import annotations

import pytest

from src.computer_use.cu_router import build_vision_policy

CAPACITES = ("vision_grounding", "vision_describe", "vision")


@pytest.fixture(autouse=True)
def _hybrid(monkeypatch):
    """Le mode que la machine de Charles utilise reellement (mesure : `hybrid`)."""
    monkeypatch.setenv("LUMENA_EXECUTION_MODE", "hybrid")
    monkeypatch.setenv("LUMENA_CU_VISION_ORDER", "")


# -- 1. Le defaut du schema ------------------------------------------------

def test_le_defaut_du_schema_active_le_repli_ollama():
    """C'est `_CONFIG_SCHEMA` qui engendre `.env.example` : laisser « 0 » ici le
    reinjecterait au prochain `sync_env_example.py`."""
    from web.routes.config import _CONFIG_SCHEMA

    e = next(x for x in _CONFIG_SCHEMA if x["key"] == "LUMENA_CU_OLLAMA_VISION")
    assert e["default"] == "1", (
        "sans ce defaut, la cascade vision tombe sur l'OCR quand les clouds sont a sec, "
        "alors que minicpm-v et llava sont installes et prouves fonctionnels"
    )


# -- 2. Ollama est present, et EN DERNIER ----------------------------------

@pytest.mark.parametrize("capacite", CAPACITES)
def test_ollama_est_dans_la_cascade(monkeypatch, capacite):
    monkeypatch.setenv("LUMENA_CU_OLLAMA_VISION", "1")
    assert "ollama" in build_vision_policy(capacite), capacite


@pytest.mark.parametrize("capacite", CAPACITES)
def test_ollama_est_le_DERNIER_essaye(monkeypatch, capacite):
    """La consigne de Charles : « sinon on reste comme d'hab ». Ollama ne doit jamais
    passer devant un fournisseur cloud disponible."""
    monkeypatch.setenv("LUMENA_CU_OLLAMA_VISION", "1")
    policy = build_vision_policy(capacite)
    if len(policy) > 1:
        assert policy[-1] == "ollama", f"{capacite} : {policy}"


@pytest.mark.parametrize("capacite", CAPACITES)
def test_les_fournisseurs_cloud_gardent_leur_ordre(monkeypatch, capacite):
    """Caracterisation : le lot AJOUTE une queue, il ne reordonne rien."""
    monkeypatch.setenv("LUMENA_CU_OLLAMA_VISION", "0")
    avant = build_vision_policy(capacite)
    monkeypatch.setenv("LUMENA_CU_OLLAMA_VISION", "1")
    apres = build_vision_policy(capacite)
    assert apres[:len(avant)] == avant, f"{capacite} : {avant} -> {apres}"


# -- 3. Ce que le lot ne doit PAS changer ---------------------------------

@pytest.mark.parametrize("capacite", CAPACITES)
def test_le_mode_local_reste_inchange(monkeypatch, capacite):
    """En local, Ollama etait DEJA seul : ce lot ne touche que cloud/hybrid."""
    monkeypatch.setenv("LUMENA_EXECUTION_MODE", "local")
    monkeypatch.setenv("LUMENA_CU_OLLAMA_VISION", "1")
    assert build_vision_policy(capacite) == ["ollama"]


def test_l_override_explicite_reste_prioritaire(monkeypatch):
    """`LUMENA_CU_VISION_ORDER` court-circuite tout, y compris ce lot."""
    monkeypatch.setenv("LUMENA_CU_VISION_ORDER", "google,anthropic")
    monkeypatch.setenv("LUMENA_CU_OLLAMA_VISION", "1")
    assert build_vision_policy("vision") == ["google", "anthropic"]


@pytest.mark.parametrize("capacite", CAPACITES)
def test_le_reglage_a_zero_rend_le_comportement_d_avant(monkeypatch, capacite):
    """Rien n'est impose : mettre « 0 » revient exactement a l'etat mesure avant le lot."""
    monkeypatch.setenv("LUMENA_CU_OLLAMA_VISION", "0")
    assert "ollama" not in build_vision_policy(capacite)


def test_aucun_non_LLM_ne_se_glisse_dans_la_cascade(monkeypatch):
    """Invariant ecrit dans `build_vision_policy` : jamais « dom », « uia » ni « ocr ».
    L'OCR reste un repli SEPARE, apres la cascade."""
    monkeypatch.setenv("LUMENA_CU_OLLAMA_VISION", "1")
    for capacite in CAPACITES:
        policy = build_vision_policy(capacite)
        assert not ({"dom", "uia", "ocr"} & set(policy)), f"{capacite} : {policy}"
