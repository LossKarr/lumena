from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

from src import __version__ as package_version
from src.version import __version__, get_build_identity


ROOT = Path(__file__).resolve().parents[2]


def test_python_package_uses_the_canonical_version() -> None:
    assert package_version == __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_pyproject_reads_src_version_dynamically() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["dynamic"] == ["version"]
    assert "version" not in config["project"]
    assert config["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "src.version.__version__"
    }


def test_build_identity_reads_packaged_attestation(tmp_path: Path) -> None:
    (tmp_path / "managed-files.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "build-info.json").write_text(
        '{"commit":"abc123","guard_smoke_profile":"update-v1",'
        '"data_schema_version":3}\n',
        encoding="utf-8",
    )

    identity = get_build_identity(tmp_path)

    assert identity.version == __version__
    assert identity.commit == "abc123"
    assert identity.guard_smoke_profile == "update-v1"
    assert identity.data_schema_version == 3
    assert re.fullmatch(r"[0-9a-f]{64}", identity.managed_manifest_sha256)


def test_development_identity_never_claims_a_release_commit(tmp_path: Path) -> None:
    identity = get_build_identity(tmp_path)
    assert identity.commit == "development"
    assert identity.managed_manifest_sha256 == ""


def test_git_checkout_identity_reports_real_head(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Updater Test"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "identity"], cwd=tmp_path, capture_output=True, check=True)
    expected = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()

    identity = get_build_identity(tmp_path)

    assert identity.commit == expected


def test_mdns_announcement_defaults_to_canonical_version(monkeypatch) -> None:
    from src.runtime import mdns_discovery

    captured: dict[str, object] = {}

    def fake_advertise(instance_id, instance_name, role, version, capabilities, port):
        captured["version"] = version
        return (object(), object())

    monkeypatch.delenv("LUMENA_VERSION", raising=False)
    monkeypatch.setattr(mdns_discovery, "_ADVERTISE_HANDLE", None)
    monkeypatch.setattr(mdns_discovery, "is_mdns_available", lambda: True)
    monkeypatch.setattr(mdns_discovery, "advertise_service", fake_advertise)

    assert mdns_discovery.start_mdns_advertise_from_env() is True
    assert captured["version"] == __version__
    monkeypatch.setattr(mdns_discovery, "_ADVERTISE_HANDLE", None)
