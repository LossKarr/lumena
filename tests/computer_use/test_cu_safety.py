"""CU-1 — Couche de gouvernance Computer Use : classify/enforce + intégration
contrôleur (blocage d'une commande destructrice / d'un mode observe-only).
"""
import pytest

from src.computer_use.safety import classify, enforce, require_approval, CUBlockedError


# ─── classify (pur) ──────────────────────────────────────────────────────────

def test_texte_normal_est_autorise():
    assert classify("type_text", {"text": "bonjour le monde"}).level == "allow"


@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -rf ~",
    "del /f /s /q C:\\Windows",
    "format C:",
    "diskpart",
    "shutdown /s",
    "reg delete HKLM\\Software",
    "Remove-Item -Recurse -Force C:\\",
    ":(){ :|:& };:",
])
def test_commandes_destructrices_bloquees(cmd):
    assert classify("type_text", {"text": cmd}).level == "block"


def test_clic_normal_autorise():
    assert classify("click", {"x": 10, "y": 20}).level == "allow"


def test_close_window_est_haut_risque():
    assert classify("close_window").level == "approve"


def test_kill_switch_bloque_tout(monkeypatch):
    monkeypatch.setenv("LUMENA_CU_DISABLED", "1")
    assert classify("click", {"x": 1, "y": 1}).level == "block"
    assert classify("type_text", {"text": "hi"}).level == "block"


def test_observe_only_bloque_les_actions_mutantes(monkeypatch):
    monkeypatch.setenv("LUMENA_CU_OBSERVE_ONLY", "1")
    assert classify("click", {"x": 1, "y": 1}).level == "block"
    assert classify("type_text", {"text": "hi"}).level == "block"
    # une action de LECTURE reste autorisée
    assert classify("screenshot").level == "allow"


# ─── enforce ─────────────────────────────────────────────────────────────────

def test_enforce_leve_sur_block():
    with pytest.raises(CUBlockedError):
        enforce("type_text", text="rm -rf /")


def test_enforce_passe_sur_allow():
    d = enforce("type_text", text="texte inoffensif")
    assert d.level == "allow"


def test_enforce_ne_bloque_pas_un_approve():
    # 'approve' n'est pas bloquant au niveau bas (géré côté agent)
    d = enforce("close_window")
    assert d.level == "approve"


# ─── Intégration contrôleur (sans bureau réel : enforce s'exécute AVANT pyautogui) ──

def test_controller_bloque_commande_destructrice():
    from src.computer_use.controller import KeyboardController
    kb = KeyboardController()
    with pytest.raises(CUBlockedError):
        kb.type_text("rm -rf / --no-preserve-root")


def test_controller_observe_only_bloque_le_clic(monkeypatch):
    monkeypatch.setenv("LUMENA_CU_OBSERVE_ONLY", "1")
    from src.computer_use.controller import MouseController
    mouse = MouseController()
    with pytest.raises(CUBlockedError):
        mouse.click(100, 100)


def test_controller_texte_normal_passe():
    # Aucun flag, texte bénin -> pas de blocage (comportement inchangé)
    from src.computer_use.controller import KeyboardController
    kb = KeyboardController()
    kb.type_text("bonjour")  # ne doit PAS lever


# ─── CU-1b — approbation OPT-IN (par défaut OFF = autonomie totale) ──────────

def test_approbation_off_par_defaut():
    # Sans le flag, AUCUNE action ne requiert d'approbation -> autonomie totale
    assert require_approval("close_app") is False
    assert require_approval("close_window") is False


def test_approbation_opt_in_sur_haut_risque(monkeypatch):
    monkeypatch.setenv("LUMENA_CU_REQUIRE_APPROVAL", "1")
    assert require_approval("close_app") is True
    assert require_approval("close_window") is True
    # une action normale ne requiert jamais d'approbation
    assert require_approval("click") is False
    assert require_approval("type_text") is False
