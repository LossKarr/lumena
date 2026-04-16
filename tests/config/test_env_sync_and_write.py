from __future__ import annotations

import os
import threading
from multiprocessing import Process
from pathlib import Path
from unittest.mock import patch

import pytest

from web.routes import config as config_mod


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


def _process_write(project_root: str, data_dir: str):
    from unittest.mock import patch
    from web.routes import config as local_config_mod

    with patch.object(local_config_mod, "_PROJECT_ROOT", Path(project_root)), \
         patch.object(local_config_mod, "DATA_DIR", Path(data_dir)), \
         patch.object(local_config_mod, "_ENV_FILE_LOCK", Path(data_dir) / ".env.lock"), \
         patch.object(local_config_mod, "_ENV_BACKUP_DIR", Path(data_dir) / "env_backups"), \
         patch.object(local_config_mod, "_CONFIG_SCHEMA", _mini_schema()):
        local_config_mod._write_env_values({"LUMENA_PORT": "9191", "LUMENA_WEB_PORT": "3131"})


@pytest.mark.timeout(30)
def test_env_cross_process_lock(tmp_path):
    project_root, data_dir, env_path = _configure_env_paths(tmp_path)
    process_a = Process(target=_process_write, args=(str(project_root), str(data_dir)))
    process_b = Process(target=_process_write, args=(str(project_root), str(data_dir)))
    process_a.start()
    process_b.start()
    process_a.join(20)
    process_b.join(20)
    assert process_a.exitcode == 0
    assert process_b.exitcode == 0
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