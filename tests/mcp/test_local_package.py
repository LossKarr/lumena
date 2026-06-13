import json

import pytest

from src.mcp.local_package import (
    LocalMCPPackageError,
    build_local_mcp_package,
    resolve_local_mcp_package,
)


def test_build_local_package_creates_installable_shape(tmp_path):
    info = build_local_mcp_package(
        server_id="airtable_1234abcd",
        intent="Connecter Airtable pour lire les bases et ecrire des lignes",
        intent_hash="a" * 64,
        root_dir=tmp_path,
    )

    assert info.relative_package_path == "packages/airtable_1234abcd"
    assert (info.package_dir / "pyproject.toml").exists()
    assert (info.package_dir / info.module_name / "__main__.py").exists()
    assert (info.package_dir / info.module_name / "server.py").exists()
    manifest = json.loads(info.manifest_path.read_text(encoding="utf-8"))
    assert manifest["server_id"] == "airtable_1234abcd"
    assert manifest["tools"] == ["describe_local_mcp_request"]


def test_resolve_local_package_rejects_missing(tmp_path):
    with pytest.raises(LocalMCPPackageError, match="local_package_missing"):
        resolve_local_mcp_package("airtable_1234abcd", root_dir=tmp_path)


def test_resolve_local_package_after_build(tmp_path):
    build_local_mcp_package(
        server_id="airtable_1234abcd",
        intent="Connecter Airtable pour lire les bases et ecrire des lignes",
        intent_hash="a" * 64,
        root_dir=tmp_path,
    )
    info = resolve_local_mcp_package("airtable_1234abcd", root_dir=tmp_path)
    assert info.module_name == "lumena_mcp_airtable_1234abcd"


@pytest.mark.parametrize("bad", ["Upper", "../x", "con", "x/y", "x\\y", ""])
def test_invalid_server_id_rejected(tmp_path, bad):
    with pytest.raises(LocalMCPPackageError):
        build_local_mcp_package(
            server_id=bad,
            intent="Connecter Airtable pour lire les bases",
            intent_hash="a" * 64,
            root_dir=tmp_path,
        )


def test_generated_server_does_not_embed_control_chars(tmp_path):
    info = build_local_mcp_package(
        server_id="airtable_1234abcd",
        intent="Connecter\x00 Airtable\n pour lire les bases",
        intent_hash="a" * 64,
        root_dir=tmp_path,
    )
    text = (info.package_dir / info.module_name / "server.py").read_text(encoding="utf-8")
    assert "\\x00" not in text
    assert "Connecter Airtable pour lire les bases" in text
