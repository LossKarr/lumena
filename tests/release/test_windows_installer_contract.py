from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_installer_is_path_deterministic_and_offline() -> None:
    script = (ROOT / "scripts" / "install_runtime.ps1").read_text(encoding="utf-8")

    assert "sys.version_info[:2] == (3,12)" in script
    assert "struct.calcsize('P') == 8" in script
    assert '"--no-index"' in script
    assert "Get-AuthenticodeSignature" in script
    assert "FileShare]::ReadWrite" in script
    assert "Programs\\Python\\Python312\\python.exe" in script
    assert "Invoke-LoggedCommand -FilePath $python" in script
    assert "python -m venv" not in script


def test_runtime_installer_provisions_and_proves_native_chromadb_runtime() -> None:
    script = (ROOT / "scripts" / "install_runtime.ps1").read_text(encoding="utf-8")

    assert "BundledVCRedistInstaller" in script
    assert "Get-AuthenticodeSignature -LiteralPath $installer" in script
    assert '"/install"' in script
    assert '"/repair"' in script
    assert "Test-VCRuntime" in script
    assert "Install-VCRuntime" in script
    assert "Assert-ChromaRuntime" in script
    assert "import chromadb_rust_bindings" in script
    assert "chromadb.PersistentClient" in script
    assert "collection.add" in script
    assert 'embeddings=[[1.0, 0.0, 0.0]]' in script
    assert "collection.count() != 1" in script
    assert "[System.IO.File]::WriteAllText" in script
    assert "System.Diagnostics.ProcessStartInfo" in script
    assert "RedirectStandardOutput = $true" in script
    assert "RedirectStandardError = $true" in script
    assert "ReadToEndAsync()" in script
    assert "$PythonPath -c $probe" not in script


def test_inno_uses_runtime_installer_and_blocks_false_successes() -> None:
    path = ROOT / "installer" / "lumena_setup.iss"
    if not path.is_file():
        pytest.skip("private installer sources are intentionally absent")
    script = path.read_text(encoding="utf-8")

    assert "scripts\\install_runtime.ps1" in script
    assert "InstallSucceeded := ExitCode = 0" in script or (
        "if ExitCode = 0" in script and "InstallSucceeded := True" in script
    )
    assert "NeedsPython" not in script
    assert "python -m venv" not in script
    assert "data\\logs\\installer.log" in script
    assert "ArchitecturesAllowed=x64compatible" in script
    assert 'Source: "deps\\vc_redist.x64.exe"' in script
    assert "-BundledVCRedistInstaller" in script
    assert "WindowVisible=" not in script
    assert "RunOnceId:" in script
    assert 'Source: "..\\src\\*"' in script
    assert (ROOT / "src" / "tools" / "remotion_runtime" / "Dockerfile").is_file()


def test_installer_build_fetches_a_signed_microsoft_vc_runtime() -> None:
    path = ROOT / "installer" / "build_installer.ps1"
    if not path.is_file():
        pytest.skip("private installer sources are intentionally absent")
    script = path.read_text(encoding="utf-8")

    assert "https://aka.ms/vc14/vc_redist.x64.exe" in script
    assert "vc_redist.x64.exe" in script
    assert '-SignerPattern "Microsoft"' in script


def test_installer_version_is_supplied_by_canonical_build() -> None:
    version_source = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
    version = re.search(r'^__version__\s*=\s*"([^"]+)"', version_source, re.MULTILINE)
    assert version

    build_path = ROOT / "installer" / "build_installer.ps1"
    inno_path = ROOT / "installer" / "lumena_setup.iss"
    if not build_path.is_file() or not inno_path.is_file():
        pytest.skip("private installer sources are intentionally absent")
    build = build_path.read_text(encoding="utf-8")
    inno = inno_path.read_text(encoding="utf-8")
    assert "/DAppVersion=$Version" in build
    assert "OutputBaseFilename=lumena-setup-v{#AppVersion}" in inno


def test_start_scripts_use_the_embedded_playwright_browser() -> None:
    for filename in ("START.bat", "START_DESKTOP.bat"):
        content = (ROOT / filename).read_text(encoding="utf-8")
        assert "PLAYWRIGHT_BROWSERS_PATH" in content
        assert "playwright-browsers" in content
