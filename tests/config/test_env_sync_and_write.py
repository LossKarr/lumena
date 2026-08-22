from __future__ import annotations

import os
import json
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from web.routes import config as config_mod


def test_privileged_voice_role_requires_explicit_pairing():
    current = {
        "LUMENA_VOICE_SESSION_ROLE": "guest",
        "LUMENA_VOICE_SESSION_TRUSTED": "",
    }
    error = config_mod._voice_pairing_error(
        {"LUMENA_VOICE_SESSION_ROLE": "owner"}, current,
    )
    assert "appairée" in error
    assert config_mod._voice_pairing_error(
        {
            "LUMENA_VOICE_SESSION_ROLE": "owner",
            "LUMENA_VOICE_SESSION_TRUSTED": "1",
        },
        current,
    ) == ""


def test_voice_session_identity_fields_require_restart():
    fields = {
        item["key"]: item for item in config_mod._CONFIG_SCHEMA
        if item["key"].startswith("LUMENA_VOICE_SESSION_")
    }
    assert fields["LUMENA_VOICE_SESSION_TRUSTED"]["restart"] is True
    assert fields["LUMENA_VOICE_SESSION_ROLE"]["restart"] is True
    assert fields["LUMENA_VOICE_SESSION_USER_ID"]["restart"] is True


def test_paired_owner_gets_coherent_owner_user_id():
    updates = config_mod._normalize_voice_pairing_updates(
        {
            "LUMENA_VOICE_SESSION_ROLE": "owner",
            "LUMENA_VOICE_SESSION_TRUSTED": "1",
            "LUMENA_VOICE_SESSION_USER_ID": "voice:guest",
        },
        {"LUMENA_OWNER_USER_ID": "local:owner"},
    )
    assert updates["LUMENA_VOICE_SESSION_USER_ID"] == "local:owner"


def _mini_schema() -> list[dict]:
    return [
        {"key": "LUMENA_PORT", "label": "Port", "group": "Serveur", "type": "number", "default": "8080", "level": "avancé", "restart": True, "hint": "Port FastAPI."},
        {"key": "LUMENA_WEB_PORT", "label": "Port Web", "group": "Serveur", "type": "number", "default": "3000", "level": "simple", "hint": "Port du front."},
        {"key": "OPENAI_API_KEY", "label": "OpenAI", "group": "Clés API", "type": "secret", "default": "", "level": "simple", "hint": "Clé API."},
    ]


def _configure_env_paths(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    env_path = project_root / ".env"
    env_path.write_text("LUMENA_PORT=8080\nCUSTOM_KEEP=1\n", encoding="utf-8")
    return project_root, data_dir, env_path


def test_env_backup_on_write(tmp_path):
    project_root, data_dir, env_path = _configure_env_paths(tmp_path)
    with patch.object(config_mod, "_PROJECT_ROOT", project_root), \
         patch.object(config_mod, "DATA_DIR", data_dir), \
         patch.object(config_mod, "_ENV_FILE_LOCK", data_dir / ".env.lock"), \
         patch.object(config_mod, "_ENV_BACKUP_DIR", data_dir / "env_backups"), \
         patch.object(config_mod, "_CONFIG_SCHEMA", _mini_schema()):
        config_mod._write_env_values({"LUMENA_PORT": "9090"})

    backups = sorted((data_dir / "env_backups").glob(".env.*"))
    assert backups
    assert env_path.read_text(encoding="utf-8").startswith("LUMENA_PORT=9090")


def test_env_preserves_unknown_keys(tmp_path):
    project_root, data_dir, env_path = _configure_env_paths(tmp_path)
    with patch.object(config_mod, "_PROJECT_ROOT", project_root), \
         patch.object(config_mod, "DATA_DIR", data_dir), \
         patch.object(config_mod, "_ENV_FILE_LOCK", data_dir / ".env.lock"), \
         patch.object(config_mod, "_ENV_BACKUP_DIR", data_dir / "env_backups"), \
         patch.object(config_mod, "_CONFIG_SCHEMA", _mini_schema()):
        config_mod._write_env_values({"LUMENA_WEB_PORT": "3333"})

    text = env_path.read_text(encoding="utf-8")
    assert "CUSTOM_KEEP=1" in text
    assert "LUMENA_WEB_PORT=3333" in text


def test_env_write_lock(tmp_path):
    project_root, data_dir, env_path = _configure_env_paths(tmp_path)
    with patch.object(config_mod, "_PROJECT_ROOT", project_root), \
         patch.object(config_mod, "DATA_DIR", data_dir), \
         patch.object(config_mod, "_ENV_FILE_LOCK", data_dir / ".env.lock"), \
         patch.object(config_mod, "_ENV_BACKUP_DIR", data_dir / "env_backups"), \
         patch.object(config_mod, "_CONFIG_SCHEMA", _mini_schema()):
        def writer(key: str, value: str):
            config_mod._write_env_values({key: value})

        t1 = threading.Thread(target=writer, args=("LUMENA_PORT", "9090"))
        t2 = threading.Thread(target=writer, args=("LUMENA_WEB_PORT", "3333"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    text = env_path.read_text(encoding="utf-8")
    assert "LUMENA_PORT=9090" in text
    assert "LUMENA_WEB_PORT=3333" in text


_PROCESS_WRITE_SCRIPT = r"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

from web.routes import config as local_config_mod

project_root = Path(sys.argv[1])
data_dir = Path(sys.argv[2])
schema = json.loads(sys.argv[3])

with patch.object(local_config_mod, "_PROJECT_ROOT", project_root), \
     patch.object(local_config_mod, "DATA_DIR", data_dir), \
     patch.object(local_config_mod, "_ENV_FILE_LOCK", data_dir / ".env.lock"), \
     patch.object(local_config_mod, "_ENV_BACKUP_DIR", data_dir / "env_backups"), \
     patch.object(local_config_mod, "_CONFIG_SCHEMA", schema):
    local_config_mod._write_env_values({"LUMENA_PORT": "9191", "LUMENA_WEB_PORT": "3131"})
"""


@pytest.mark.timeout(30)
def test_env_cross_process_lock(tmp_path):
    project_root, data_dir, env_path = _configure_env_paths(tmp_path)
    env = os.environ.copy()
    # Windows spawn can import user-site packages before the project module tree.
    # Keep the lock test focused on Lumena instead of external cv2/numpy installs.
    env["PYTHONNOUSERSITE"] = "1"
    schema = json.dumps(_mini_schema(), ensure_ascii=False)
    args = [sys.executable, "-c", _PROCESS_WRITE_SCRIPT, str(project_root), str(data_dir), schema]
    process_a = subprocess.Popen(args, cwd=Path.cwd(), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    process_b = subprocess.Popen(args, cwd=Path.cwd(), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out_a, err_a = process_a.communicate(timeout=20)
    out_b, err_b = process_b.communicate(timeout=20)
    assert process_a.returncode == 0, err_a or out_a
    assert process_b.returncode == 0, err_b or out_b
    text = env_path.read_text(encoding="utf-8")
    assert "LUMENA_PORT=9191" in text
    assert "LUMENA_WEB_PORT=3131" in text


def test_env_backup_rotation(tmp_path):
    project_root, data_dir, _ = _configure_env_paths(tmp_path)
    with patch.object(config_mod, "_PROJECT_ROOT", project_root), \
         patch.object(config_mod, "DATA_DIR", data_dir), \
         patch.object(config_mod, "_ENV_FILE_LOCK", data_dir / ".env.lock"), \
         patch.object(config_mod, "_ENV_BACKUP_DIR", data_dir / "env_backups"), \
         patch.object(config_mod, "_CONFIG_SCHEMA", _mini_schema()):
        for idx in range(12):
            config_mod._write_env_values({"LUMENA_PORT": str(8080 + idx)})

    backups = sorted((data_dir / "env_backups").glob(".env.*"))
    assert 1 <= len(backups) <= 10
