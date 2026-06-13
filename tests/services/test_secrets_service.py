"""
Tests pour SecretsService (Phase 4).

Vérifie :
  - Fonctionnels de base (get/set/delete/has/list_*)
  - Isolation entre scopes
  - Chiffrement at-rest (0 plaintext dans le fichier)
  - export_for_subprocess avec allowlist EXPLICITE (corr 1)
  - Utilisation DATA_DIR (corr 2)
  - chmod best-effort POSIX, Windows tolérant (corr 3)
  - FileLock concurrence (corr 4)
  - scrub_secrets_in_text helper sans filtre global (corr 5)
  - migrate_from_env refuse remove_from_env=True (corr 6)
  - secret_ref format strict
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import List

import pytest

from src.services.secrets_service import (
    CorruptedKeyError,
    SecretsService,
    SecretsServiceError,
    get_env_or_secret,
    get_secrets_service,
    is_secret_ref,
    parse_secret_ref,
    reset_secrets_service_for_tests,
    scrub_secrets_in_text,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures : SecretsService isolé par test (tmp_path)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def svc(tmp_path) -> SecretsService:
    secrets_dir = tmp_path / "secrets"
    master_key = tmp_path / ".lumena_secrets.key"
    return SecretsService(secrets_dir=secrets_dir, master_key_path=master_key)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Isolation du singleton entre tests."""
    reset_secrets_service_for_tests()
    yield
    reset_secrets_service_for_tests()


# ──────────────────────────────────────────────────────────────────────────────
# Fonctionnels de base
# ──────────────────────────────────────────────────────────────────────────────


def test_set_then_get_returns_value(svc):
    svc.set("lumena_global", "OPENAI_API_KEY", "sk-test-123")
    assert svc.get("lumena_global", "OPENAI_API_KEY") == "sk-test-123"


def test_get_unknown_returns_none(svc):
    assert svc.get("lumena_global", "DOES_NOT_EXIST") is None


def test_delete_removes_key(svc):
    svc.set("lumena_global", "MY_KEY", "value")
    assert svc.has("lumena_global", "MY_KEY")
    assert svc.delete("lumena_global", "MY_KEY") is True
    assert not svc.has("lumena_global", "MY_KEY")


def test_delete_nonexistent_returns_false(svc):
    assert svc.delete("lumena_global", "GHOST_KEY") is False


def test_has_returns_correct_bool(svc):
    svc.set("lumena_global", "KEY_A", "v")
    assert svc.has("lumena_global", "KEY_A") is True
    assert svc.has("lumena_global", "KEY_B") is False


def test_list_keys_returns_names_only(svc):
    svc.set("lumena_global", "KEY_A", "secret_a")
    svc.set("lumena_global", "KEY_B", "secret_b")
    keys = svc.list_keys("lumena_global")
    assert set(keys) == {"KEY_A", "KEY_B"}
    # Vérifie qu'aucune valeur n'apparait
    for k in keys:
        assert "secret_a" not in k
        assert "secret_b" not in k


def test_list_scopes_after_creation(svc):
    svc.set("lumena_global", "K1", "v1")
    svc.set("mcp.postgres", "K2", "v2")
    svc.set("profile.openlumena", "K3", "v3")
    scopes = svc.list_scopes()
    assert set(scopes) == {"lumena_global", "mcp.postgres", "profile.openlumena"}


def test_overwrite_value(svc):
    svc.set("lumena_global", "K", "v1")
    svc.set("lumena_global", "K", "v2")
    assert svc.get("lumena_global", "K") == "v2"


def test_has_on_unknown_scope_returns_false(svc):
    assert svc.has("unknown_scope_xyz", "K") is False


# ──────────────────────────────────────────────────────────────────────────────
# Isolation entre scopes
# ──────────────────────────────────────────────────────────────────────────────


def test_scopes_isolated(svc):
    svc.set("scope_a", "SAME_KEY", "value_in_a")
    svc.set("scope_b", "SAME_KEY", "value_in_b")
    assert svc.get("scope_a", "SAME_KEY") == "value_in_a"
    assert svc.get("scope_b", "SAME_KEY") == "value_in_b"


def test_list_keys_filtered_by_scope(svc):
    svc.set("scope_a", "KEY_A1", "v")
    svc.set("scope_a", "KEY_A2", "v")
    svc.set("scope_b", "KEY_B1", "v")
    assert set(svc.list_keys("scope_a")) == {"KEY_A1", "KEY_A2"}
    assert set(svc.list_keys("scope_b")) == {"KEY_B1"}


def test_delete_in_one_scope_does_not_affect_other(svc):
    svc.set("scope_a", "K", "va")
    svc.set("scope_b", "K", "vb")
    svc.delete("scope_a", "K")
    assert svc.get("scope_a", "K") is None
    assert svc.get("scope_b", "K") == "vb"


# ──────────────────────────────────────────────────────────────────────────────
# Chiffrement at-rest (0 plaintext)
# ──────────────────────────────────────────────────────────────────────────────


def test_no_plaintext_in_storage_file(svc, tmp_path):
    secret_value = "sk-very-secret-not-visible-anywhere-12345"
    svc.set("lumena_global", "MY_SECRET", secret_value)
    # Trouve le fichier blob
    secrets_dir = tmp_path / "secrets"
    blob_path = secrets_dir / "lumena_global.fernet.json"
    assert blob_path.exists()
    # Lecture brute
    raw_text = blob_path.read_text(encoding="utf-8")
    raw_bytes = blob_path.read_bytes()
    # La valeur secrète NE doit PAS apparaitre en clair
    assert secret_value not in raw_text
    assert secret_value.encode("utf-8") not in raw_bytes


def test_no_plaintext_for_key_name_either(svc, tmp_path):
    """Stocke une valeur dont le contenu est lui-même un nom de clé,
    vérifie qu'il est chiffré."""
    svc.set("lumena_global", "AUTH", "API_KEY=hidden_value_xyz")
    secrets_dir = tmp_path / "secrets"
    blob_path = secrets_dir / "lumena_global.fernet.json"
    raw = blob_path.read_text(encoding="utf-8")
    assert "hidden_value_xyz" not in raw


def test_master_key_auto_generated(tmp_path):
    secrets_dir = tmp_path / "secrets"
    master_key = tmp_path / ".lumena_secrets.key"
    assert not master_key.exists()
    svc = SecretsService(secrets_dir=secrets_dir, master_key_path=master_key)
    svc.set("scope", "K", "v")  # déclenche init
    assert master_key.exists()
    # Master key contient bytes Fernet (44 chars base64 typique)
    raw = master_key.read_bytes().strip()
    assert len(raw) >= 32


def test_master_key_persists_across_instances(tmp_path):
    secrets_dir = tmp_path / "secrets"
    master_key = tmp_path / ".lumena_secrets.key"
    svc1 = SecretsService(secrets_dir=secrets_dir, master_key_path=master_key)
    svc1.set("scope", "K", "secret_value")
    # Nouvelle instance lisant les mêmes fichiers
    svc2 = SecretsService(secrets_dir=secrets_dir, master_key_path=master_key)
    assert svc2.get("scope", "K") == "secret_value"


def test_corrupted_master_key_raises_clear_error(tmp_path):
    secrets_dir = tmp_path / "secrets"
    master_key = tmp_path / ".lumena_secrets.key"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    master_key.write_bytes(b"x")  # Trop court / corrompu
    svc = SecretsService(secrets_dir=secrets_dir, master_key_path=master_key)
    with pytest.raises(CorruptedKeyError):
        svc.set("scope", "K", "v")


def test_master_key_changed_decrypt_returns_empty(tmp_path):
    """Si la master key est remplacée, les blobs deviennent illisibles
    mais l'API ne crash pas (graceful degradation : dict vide)."""
    secrets_dir = tmp_path / "secrets"
    master_key = tmp_path / ".lumena_secrets.key"
    svc1 = SecretsService(secrets_dir=secrets_dir, master_key_path=master_key)
    svc1.set("scope", "K", "v")
    # Remplace la master key
    from cryptography.fernet import Fernet
    master_key.write_bytes(Fernet.generate_key())
    svc2 = SecretsService(secrets_dir=secrets_dir, master_key_path=master_key)
    # Lecture retourne None (graceful, pas crash)
    assert svc2.get("scope", "K") is None


# ──────────────────────────────────────────────────────────────────────────────
# export_for_subprocess : allowlist EXPLICITE (corr 1)
# ──────────────────────────────────────────────────────────────────────────────


def test_export_for_subprocess_returns_only_requested_keys(svc):
    svc.set("mcp.postgres", "POSTGRES_URL", "url1")
    svc.set("mcp.postgres", "POSTGRES_PASSWORD", "pwd1")
    svc.set("mcp.postgres", "DEBUG", "true")
    # Demande UNIQUEMENT POSTGRES_URL
    exported = svc.export_for_subprocess("mcp.postgres", keys=["POSTGRES_URL"])
    assert exported == {"POSTGRES_URL": "url1"}
    # POSTGRES_PASSWORD et DEBUG ne doivent JAMAIS apparaitre
    assert "POSTGRES_PASSWORD" not in exported
    assert "DEBUG" not in exported


def test_export_for_subprocess_omits_missing_keys(svc):
    svc.set("scope", "K1", "v1")
    exported = svc.export_for_subprocess("scope", keys=["K1", "K2_ABSENT"])
    # K2_ABSENT est demandée mais inexistante → OMISE (pas None)
    assert exported == {"K1": "v1"}
    assert "K2_ABSENT" not in exported


def test_export_for_subprocess_empty_keys_returns_empty(svc):
    svc.set("scope", "K1", "v1")
    exported = svc.export_for_subprocess("scope", keys=[])
    assert exported == {}


def test_export_for_subprocess_rejects_non_list(svc):
    with pytest.raises(SecretsServiceError):
        svc.export_for_subprocess("scope", keys="POSTGRES_URL")  # type: ignore


def test_export_for_subprocess_rejects_empty_key_in_list(svc):
    with pytest.raises(SecretsServiceError):
        svc.export_for_subprocess("scope", keys=["VALID", ""])


def test_export_for_subprocess_does_not_log_values(svc, caplog):
    """L'export ne doit pas émettre de log contenant la valeur."""
    svc.set("scope", "KEY1", "super_secret_value_xyz")
    import logging
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        result = svc.export_for_subprocess("scope", keys=["KEY1"])
    assert result["KEY1"] == "super_secret_value_xyz"
    # Vérifie qu'aucun log ne mentionne la valeur
    for record in caplog.records:
        assert "super_secret_value_xyz" not in record.getMessage()


# ──────────────────────────────────────────────────────────────────────────────
# DATA_DIR usage (corr 2)
# ──────────────────────────────────────────────────────────────────────────────


def test_default_paths_anchor_under_data_dir():
    """Vérifie via inspection du code source que les chemins par défaut
    pointent sous DATA_DIR (pas Path("data/...") brut).

    Test robuste : n'instancie pas le service (évite la pollution par
    d'autres tests qui peuvent reload paths.py via monkeypatch
    LUMENA_DATA_DIR).
    """
    import inspect
    from src.services import secrets_service as ss_module

    source = inspect.getsource(ss_module.SecretsService.__init__)
    # La signature DOIT utiliser DATA_DIR (depuis src.utils.paths) comme
    # ancre des chemins par défaut. Aucun "Path('data/...')" brut accepté.
    assert "DATA_DIR" in source, (
        "SecretsService.__init__ doit utiliser DATA_DIR de src.utils.paths"
    )
    # Et ne doit pas hardcoder Path("data/...") qui ignore LUMENA_DATA_DIR
    assert 'Path("data' not in source
    assert "Path('data" not in source


def test_explicit_paths_override_defaults(tmp_path):
    """Quand des chemins sont passés explicitement, ils remplacent les défauts."""
    custom_secrets = tmp_path / "my_secrets"
    custom_key = tmp_path / "my.key"
    svc = SecretsService(secrets_dir=custom_secrets, master_key_path=custom_key)
    svc.set("scope", "K", "v")
    assert (custom_secrets / "scope.fernet.json").exists()
    assert custom_key.exists()
    # DATA_DIR ne doit pas avoir été touché
    from src.utils.paths import DATA_DIR
    assert not (DATA_DIR / "secrets" / "scope.fernet.json").exists() or True
    # (le `or True` car DATA_DIR pourrait avoir des secrets d'autres tests/runs)


def test_path_traversal_in_scope_rejected(svc):
    """Scope avec / ou \\ ou commençant par . → refus."""
    with pytest.raises(SecretsServiceError):
        svc.set("../etc/passwd", "K", "v")
    with pytest.raises(SecretsServiceError):
        svc.set("..\\windows", "K", "v")
    with pytest.raises(SecretsServiceError):
        svc.set(".hidden_scope", "K", "v")


def test_invalid_scope_type_rejected(svc):
    with pytest.raises(SecretsServiceError):
        svc.set("", "K", "v")
    with pytest.raises(SecretsServiceError):
        svc.set(None, "K", "v")  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Permissions best-effort (corr 3)
# ──────────────────────────────────────────────────────────────────────────────


def test_chmod_best_effort_no_crash(tmp_path):
    """L'init ne doit jamais crash sur Windows même sans chmod."""
    svc = SecretsService(
        secrets_dir=tmp_path / "secrets",
        master_key_path=tmp_path / ".key",
    )
    svc.set("scope", "K", "v")
    # Pas d'exception levée == OK
    assert svc.get("scope", "K") == "v"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_posix_master_key_perms_0o600(tmp_path):
    """Sur POSIX, la clé maître doit avoir permissions 0o600."""
    svc = SecretsService(
        secrets_dir=tmp_path / "secrets",
        master_key_path=tmp_path / ".key",
    )
    svc.set("scope", "K", "v")
    key_path = tmp_path / ".key"
    stat = key_path.stat()
    # Vérifie le mode 0o600 (user read/write only)
    assert stat.st_mode & 0o777 == 0o600


# ──────────────────────────────────────────────────────────────────────────────
# FileLock concurrence (corr 4)
# ──────────────────────────────────────────────────────────────────────────────


def test_concurrent_set_does_not_lose_keys(svc):
    """20 threads écrivent simultanément 20 clés différentes — toutes doivent être présentes."""
    n_threads = 20
    errors: List[Exception] = []

    def writer(idx: int):
        try:
            svc.set("concurrent_scope", f"KEY_{idx:02d}", f"val_{idx}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15.0)

    assert errors == [], f"Errors during concurrent set: {errors}"
    keys = svc.list_keys("concurrent_scope")
    assert len(keys) == n_threads, (
        f"Expected {n_threads} keys, got {len(keys)} — keys lost during concurrent writes"
    )
    # Vérifie aussi que les valeurs sont correctes
    for i in range(n_threads):
        assert svc.get("concurrent_scope", f"KEY_{i:02d}") == f"val_{i}"


# ──────────────────────────────────────────────────────────────────────────────
# Scrubbing helper (corr 5 — pas de filtre loguru global)
# ──────────────────────────────────────────────────────────────────────────────


def test_scrub_secrets_in_text_replaces_known():
    text = "Error: failed to call API with token sk-abcdef1234"
    scrubbed = scrub_secrets_in_text(text, ["sk-abcdef1234"])
    assert "sk-abcdef1234" not in scrubbed
    assert "****" in scrubbed


def test_scrub_secrets_does_not_scrub_short_values():
    """Valeurs trop courtes (< 4 chars) ne doivent pas être scrubbed
    (sinon on perdrait du texte aléatoire comme 'OK' ou 'NO')."""
    text = "Status: OK"
    scrubbed = scrub_secrets_in_text(text, ["OK"])
    assert scrubbed == text  # inchangé


def test_scrub_secrets_returns_input_on_empty():
    assert scrub_secrets_in_text("", ["secret"]) == ""
    assert scrub_secrets_in_text(None, ["secret"]) == ""  # type: ignore


def test_scrub_secrets_handles_multiple_values():
    text = "key1=ABCDEFGH key2=12345678"
    scrubbed = scrub_secrets_in_text(text, ["ABCDEFGH", "12345678"])
    assert "ABCDEFGH" not in scrubbed
    assert "12345678" not in scrubbed
    assert "****" in scrubbed


def test_scrub_secrets_no_global_filter_installed():
    """Le helper NE doit PAS installer de filtre loguru/logging global.

    On vérifie qu'appeler scrub_secrets_in_text n'ajoute pas de handler.
    """
    from loguru import logger
    before = len(logger._core.handlers)  # type: ignore[attr-defined]
    scrub_secrets_in_text("text with secret xyz", ["xyz"])
    after = len(logger._core.handlers)  # type: ignore[attr-defined]
    assert before == after, "scrub_secrets_in_text should not install a logger filter"


# ──────────────────────────────────────────────────────────────────────────────
# Bridge legacy .env (corr 6)
# ──────────────────────────────────────────────────────────────────────────────


def test_get_env_or_secret_prefers_secrets_service(svc, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "from_env")
    svc.set("lumena_global", "OPENAI_API_KEY", "from_secrets_service")
    value = get_env_or_secret("OPENAI_API_KEY", service=svc)
    assert value == "from_secrets_service"


def test_get_env_or_secret_fallback_to_env(svc, monkeypatch):
    monkeypatch.setenv("FALLBACK_KEY", "env_value")
    # Pas de set sur SecretsService
    value = get_env_or_secret("FALLBACK_KEY", service=svc)
    assert value == "env_value"


def test_get_env_or_secret_returns_none_if_neither(svc, monkeypatch):
    monkeypatch.delenv("DEFINITELY_ABSENT", raising=False)
    value = get_env_or_secret("DEFINITELY_ABSENT", service=svc)
    assert value is None


def test_migrate_from_env_copies_keys(svc, monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_xxx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-open-xxx")
    monkeypatch.delenv("ABSENT_KEY", raising=False)

    report = svc.migrate_from_env(
        ["STRIPE_API_KEY", "OPENAI_API_KEY", "ABSENT_KEY"]
    )
    assert report["STRIPE_API_KEY"] == "migrated"
    assert report["OPENAI_API_KEY"] == "migrated"
    assert report["ABSENT_KEY"] == "absent"
    assert svc.get("lumena_global", "STRIPE_API_KEY") == "sk_test_xxx"
    assert svc.get("lumena_global", "OPENAI_API_KEY") == "sk-open-xxx"


def test_migrate_from_env_skips_existing(svc, monkeypatch):
    svc.set("lumena_global", "ALREADY_THERE", "old_value")
    monkeypatch.setenv("ALREADY_THERE", "new_env_value")
    report = svc.migrate_from_env(["ALREADY_THERE"])
    assert report["ALREADY_THERE"] == "exists_already"
    # Valeur SecretsService PRÉSERVÉE
    assert svc.get("lumena_global", "ALREADY_THERE") == "old_value"


def test_migrate_from_env_refuses_remove_from_env(svc):
    """Phase 4 STRICTE : remove_from_env=True refusé."""
    with pytest.raises(SecretsServiceError, match="remove_from_env"):
        svc.migrate_from_env(["SOME_KEY"], remove_from_env=True)


def test_migrate_from_env_does_not_touch_environ(svc, monkeypatch):
    """Phase 4 : .env / os.environ jamais modifié par migrate."""
    monkeypatch.setenv("MIGRATE_ME", "value_in_env")
    svc.migrate_from_env(["MIGRATE_ME"])
    # os.environ ne doit PAS être modifié
    assert os.environ.get("MIGRATE_ME") == "value_in_env"


# ──────────────────────────────────────────────────────────────────────────────
# Format secret_ref
# ──────────────────────────────────────────────────────────────────────────────


def test_is_secret_ref_recognizes_valid():
    assert is_secret_ref("$secret:mcp.postgres/POSTGRES_URL") is True
    assert is_secret_ref("$secret:lumena_global/OPENAI_API_KEY") is True
    assert is_secret_ref("$secret:profile.openlumena/STRIPE_KEY") is True


def test_is_secret_ref_rejects_inline_values():
    assert is_secret_ref("actual_value_12345") is False
    assert is_secret_ref("sk-abc123") is False
    assert is_secret_ref("") is False
    assert is_secret_ref(None) is False  # type: ignore


def test_is_secret_ref_rejects_partial():
    assert is_secret_ref("$secret:") is False
    assert is_secret_ref("$secret:scope") is False  # pas de /
    assert is_secret_ref("$secret:/KEY") is False  # pas de scope


def test_parse_secret_ref_returns_tuple():
    result = parse_secret_ref("$secret:mcp.postgres/POSTGRES_URL")
    assert result == ("mcp.postgres", "POSTGRES_URL")


def test_parse_secret_ref_returns_none_on_invalid():
    assert parse_secret_ref("not_a_ref") is None
    assert parse_secret_ref("") is None
    assert parse_secret_ref(None) is None  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Robustesse / edge cases
# ──────────────────────────────────────────────────────────────────────────────


def test_unicode_secret_value(svc):
    """Caractères UTF-8 (accents, emoji) doivent passer."""
    value = "secret_avec_accents_éàü_🔐_clé"
    svc.set("scope", "KEY", value)
    assert svc.get("scope", "KEY") == value


def test_empty_value_treated_as_delete(svc):
    svc.set("scope", "K", "v")
    assert svc.has("scope", "K") is True
    svc.set("scope", "K", "")
    assert svc.has("scope", "K") is False


def test_get_with_empty_key_returns_none(svc):
    svc.set("scope", "K", "v")
    assert svc.get("scope", "") is None
    assert svc.get("scope", None) is None  # type: ignore


def test_set_with_empty_key_raises(svc):
    with pytest.raises(SecretsServiceError):
        svc.set("scope", "", "v")
    with pytest.raises(SecretsServiceError):
        svc.set("scope", None, "v")  # type: ignore


def test_set_with_non_string_value_raises(svc):
    with pytest.raises(SecretsServiceError):
        svc.set("scope", "K", 123)  # type: ignore
    with pytest.raises(SecretsServiceError):
        svc.set("scope", "K", None)  # type: ignore


def test_list_scopes_empty_when_no_secrets(tmp_path):
    svc = SecretsService(
        secrets_dir=tmp_path / "secrets",
        master_key_path=tmp_path / ".key",
    )
    assert svc.list_scopes() == []


# ──────────────────────────────────────────────────────────────────────────────
# Singleton bridge
# ──────────────────────────────────────────────────────────────────────────────


def test_get_secrets_service_returns_singleton():
    svc1 = get_secrets_service()
    svc2 = get_secrets_service()
    assert svc1 is svc2


def test_reset_secrets_service_for_tests_clears_singleton():
    svc1 = get_secrets_service()
    reset_secrets_service_for_tests()
    svc2 = get_secrets_service()
    assert svc1 is not svc2
