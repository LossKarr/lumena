r"""Lots VOICE-1 et VOICE-3 - la langue forcee et le modele configure arrivent au STT.

--- Ce que la comparaison avec la backup du 28/09 18:35 a mesure ---

Charles : « la voix marche super bien » sur la backup, moins bien maintenant. La backup
date de 18 h 35, le plan V3 de 18 h 41 : elle capture donc l'etat juste AVANT V3.

**VOICE-1 — la regression, une seule ligne.**

    .env.example backup  ->  LUMENA_STT_LANGUAGE   ABSENT
    .env.example actuel  ->  LUMENA_STT_LANGUAGE=auto     (ajoute par V3)

Effet sur le code qui parle a Whisper :

    backup :  language=self.language                       ->  'fr' FORCE
    actuel :  language=self._effective_language(language)   ->  None = AUTO-DETECTION

car `_effective_language` traduit « auto » en `None`, et `None` signifie « devine la
langue » pour Whisper. V3 a introduit le multilingue (scenarios H11 FR->EN->ES->FR et H12
code-switch) et, avec lui, ce reglage par defaut.

Consequence dans le journal du 28/09 :

    👂 'Romance service, ferme un petit message dans le Discord.'
    🚀 Declenchement par 'lumena' — trailing: 'grand moins service mets un petit message'
    🗣️ → Lumena: 'grand moins service mets un petit message dans le discord'

Whisper a cru entendre une autre langue, et le bruit est parti tel quel comme commande.
Deux `⚠️ Hallucination Whisper rejetee` apres 18 h 41, zero avant.

**VOICE-3 — le plafond, present AUSSI dans la backup.**

Le `.env` demande `LUMENA_STT_MODEL=large-v3-turbo`, et le journal dit
`STT initialise (modele: small, device: cpu)`. Deux causes cumulees :

1. `stt.py` l.96 : `model_size: str = os.getenv("LUMENA_STT_MODEL", "small")` — un defaut
   de parametre est evalue **a l'import du module**, pas a l'appel. Comme `stt.py` est
   importe avant le chargement du `.env`, la valeur est figee a `'small'` pour toute la
   vie du processus. Mesure : `model_size fige a l import = 'small'`.
2. `live.py` l.847 : `LumenaSTT(device=device, compute_type=compute)` passe le device et la
   precision resolus par le superviseur — **mais pas le modele**.

`device` et `compute` echappent donc au vice de forme ; le modele, non. Ce defaut existe
a l'identique dans la backup : ce n'est pas la regression, c'est un plafond permanent.

--- Ce que ces lots ne traitent PAS ---

Les bibliotheques CUDA (`cublas`, `cudnn`) sont absentes du venv, donc `cuda_ready` est
faux et le STT tombe sur CPU/int8 malgre une RTX 3060 de 12 Go (dont 1,2 Go libre
seulement). C'est une affaire d'environnement, pas de code.
"""
from __future__ import annotations

import pytest


# -- VOICE-1 : la langue ----------------------------------------------------

def test_le_defaut_du_schema_de_config_est_le_francais():
    """C'est `_CONFIG_SCHEMA` qui engendre `.env.example` (« Do not edit manually »).
    Laisser « auto » ici le reinjecterait au prochain `sync_env_example.py`."""
    from web.routes.config import _CONFIG_SCHEMA

    entree = next(e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_STT_LANGUAGE")
    assert entree["default"] == "fr", (
        "le defaut doit etre une langue EXPLICITE : « auto » fait deviner Whisper a "
        "chaque enonce, ce qui a produit « Romance service » au lieu de « Lumena »"
    )


def test_auto_reste_PROPOSE_pour_qui_le_veut():
    """Le multilingue de V3 n'est pas retire — il devient un choix, non un defaut."""
    from web.routes.config import _CONFIG_SCHEMA

    entree = next(e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_STT_LANGUAGE")
    assert "auto" in entree["options"]
    assert "fr" in entree["options"]


@pytest.mark.parametrize("valeur,attendu", [
    ("fr", "fr"),
    ("en", "en"),
    ("auto", None),          # comportement conserve : « auto » = detection Whisper
    ("", None),
    ("detect", None),
])
def test_effective_language_traduit_comme_avant(valeur, attendu):
    """Caracterisation : la fonction n'est PAS modifiee. Seul le defaut change."""
    from src.voice.stt import LumenaSTT

    assert LumenaSTT._effective_language(  # type: ignore[arg-type]
        type("_", (), {"language": "fr"})(), valeur) == attendu


def test_le_superviseur_resout_la_langue_a_l_APPEL(monkeypatch):
    """Lue a l'appel, pas figee a l'import : c'est ce qui distingue ce champ du modele."""
    from src.voice.v2 import supervisor

    monkeypatch.setenv("LUMENA_STT_LANGUAGE", "fr")
    assert supervisor.resolve_voice_runtime_options()["language"] == "fr"
    monkeypatch.setenv("LUMENA_STT_LANGUAGE", "es")
    assert supervisor.resolve_voice_runtime_options()["language"] == "es"


# -- VOICE-3 : le modele ----------------------------------------------------

def test_le_superviseur_resout_AUSSI_le_modele(monkeypatch):
    """Le manque mesure : `device` et `compute` etaient resolus, le modele non."""
    from src.voice.v2 import supervisor

    monkeypatch.setenv("LUMENA_STT_MODEL", "large-v3-turbo")
    options = supervisor.resolve_voice_runtime_options()
    assert "model" in options, "le superviseur ne resout toujours pas le modele"
    assert options["model"] == "large-v3-turbo"


def test_le_modele_est_lu_a_l_APPEL_pas_a_l_import(monkeypatch):
    """Le coeur du vice de forme : `os.getenv` dans une valeur par defaut est evalue une
    seule fois, au chargement du module. Le `.env` lu ensuite n'y change rien."""
    from src.voice.v2 import supervisor

    monkeypatch.setenv("LUMENA_STT_MODEL", "medium")
    assert supervisor.resolve_voice_runtime_options()["model"] == "medium"
    monkeypatch.setenv("LUMENA_STT_MODEL", "small")
    assert supervisor.resolve_voice_runtime_options()["model"] == "small"


def test_un_modele_absent_de_l_env_garde_un_defaut_sain(monkeypatch):
    from src.voice.v2 import supervisor

    monkeypatch.delenv("LUMENA_STT_MODEL", raising=False)
    modele = supervisor.resolve_voice_runtime_options()["model"]
    assert modele and isinstance(modele, str)


def test_live_PASSE_le_modele_au_STT():
    """Une option resolue que l'appelant ignore ne corrige rien — c'est exactement le
    motif de `device`/`compute` passes et de `model_size` oublie."""
    import inspect

    from src.voice.v2 import live

    source = inspect.getsource(live)
    assert "model_size=" in source, (
        "`live.py` ne passe pas `model_size` : le STT retombera sur le defaut fige a "
        "l'import, c'est-a-dire `small`"
    )


# -- Ce que les lots ne doivent PAS changer ---------------------------------

def test_le_device_et_la_precision_restent_resolus(monkeypatch):
    """Caracterisation : ils marchaient deja, ils doivent continuer."""
    from src.voice.v2 import supervisor

    monkeypatch.setenv("LUMENA_STT_DEVICE", "cpu")
    options = supervisor.resolve_voice_runtime_options()
    assert options["device"] == "cpu"
    assert options["compute"] == "int8", "le fallback CPU doit rester en int8"


def test_le_fallback_CUDA_absent_reste_en_place(monkeypatch):
    """Les DLL NVIDIA manquent sur cette machine : demander `cuda` doit rendre `cpu`
    sans lever, comme aujourd'hui."""
    from src.voice.v2 import supervisor

    monkeypatch.setenv("LUMENA_STT_DEVICE", "cuda")
    options = supervisor.resolve_voice_runtime_options()
    assert options["device"] in {"cuda", "cpu"}
    if options["device"] == "cpu":
        assert options["compute"] == "int8"
