"""
P6 — Convention Scanner.

Scanne le workspace pour détecter les conventions du projet
(tsconfig.json, .eslintrc, pyproject.toml, package.json, .prettierrc, etc.)
et retourne un bloc compact à injecter dans le prompt système du CodeAgent.

Activé si LUMENA_CONVENTION_SCAN=1 (ON par défaut).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger


# Fichiers de convention à inspecter, par ordre de priorité
_CONVENTION_FILES = [
    "tsconfig.json",
    "tsconfig.base.json",
    ".eslintrc.json",
    ".eslintrc.js",
    ".eslintrc.yaml",
    ".eslintrc.yml",
    "eslint.config.js",
    "eslint.config.mjs",
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.yaml",
    ".prettierrc.js",
    "pyproject.toml",
    "setup.cfg",
    ".flake8",
    "mypy.ini",
    ".mypy.ini",
    "ruff.toml",
    ".ruff.toml",
    "package.json",
    "jest.config.js",
    "jest.config.ts",
    "vitest.config.ts",
    "vitest.config.js",
    ".editorconfig",
]

# Clés utiles à extraire par type de fichier
_COMPACT_KEYS = {
    "tsconfig.json": ["compilerOptions.target", "compilerOptions.strict",
                      "compilerOptions.module", "compilerOptions.moduleResolution"],
    "package.json": ["scripts.test", "scripts.build", "scripts.lint",
                     "engines.node", "type"],
    "pyproject.toml": None,  # traitement spécial
}


def scan_conventions(workspace: Path, max_chars: int = 2000) -> str:
    """
    Scanne le workspace et retourne un bloc compact de conventions.

    Retourne "" si rien n'est trouvé ou si le scan échoue.
    """
    ws = Path(workspace).resolve()
    if not ws.exists():
        return ""

    try:
        found_sections: list[str] = []
        total_chars = 0

        for filename in _CONVENTION_FILES:
            fp = ws / filename
            if not fp.exists():
                continue
            try:
                section = _read_convention_file(fp, filename)
                if not section:
                    continue
                if total_chars + len(section) > max_chars:
                    break
                found_sections.append(section)
                total_chars += len(section)
            except Exception as e:
                logger.debug("[convention_scanner] {}: {}", filename, e)

        if not found_sections:
            return ""

        return (
            "## Conventions du projet\n"
            + "\n\n".join(found_sections)
            + "\n"
        )
    except Exception as e:
        logger.debug("[convention_scanner] scan failed: {}", e)
        return ""


def _read_convention_file(fp: Path, filename: str) -> str:
    """Lit et compacte un fichier de convention."""
    raw = fp.read_text(encoding="utf-8", errors="ignore")

    if filename.endswith(".json") and not filename.startswith(".eslint"):
        return _compact_json(fp.name, raw)

    if filename == "pyproject.toml":
        return _compact_pyproject(raw)

    if filename == ".editorconfig":
        return _compact_editorconfig(raw)

    if filename.startswith(".eslint") or filename.startswith("eslint"):
        return f"**{fp.name}** : règles ESLint présentes ({len(raw.splitlines())} lignes)"

    if filename.startswith(".prettier"):
        return _compact_prettier(fp.name, raw)

    # Fallback : première ligne non vide (ou chemin seul)
    first_line = next((l.strip() for l in raw.splitlines() if l.strip()), "")
    return f"**{fp.name}** : {first_line[:120]}" if first_line else f"**{fp.name}** : présent"


def _compact_json(name: str, raw: str) -> str:
    try:
        data = json.loads(raw)
    except Exception:
        return f"**{name}** : présent (JSON invalide)"

    lines = [f"**{name}** :"]

    if name == "tsconfig.json":
        opts = data.get("compilerOptions", {})
        if opts:
            interesting = ["target", "strict", "module", "moduleResolution",
                           "jsx", "esModuleInterop", "allowJs", "baseUrl"]
            parts = [f"{k}={json.dumps(v)}" for k in interesting if k in opts]
            if parts:
                lines.append("  compilerOptions: " + ", ".join(parts))

    elif name == "package.json":
        if "type" in data:
            lines.append(f"  type={data['type']}")
        scripts = data.get("scripts", {})
        for s in ("test", "build", "lint", "dev"):
            if s in scripts:
                lines.append(f"  scripts.{s}={json.dumps(scripts[s])}")
        engines = data.get("engines", {})
        if "node" in engines:
            lines.append(f"  engines.node={engines['node']}")

    elif name.startswith(".prettier"):
        for k, v in list(data.items())[:8]:
            lines.append(f"  {k}={json.dumps(v)}")

    else:
        # Générique : afficher les clés de premier niveau
        for k, v in list(data.items())[:5]:
            if isinstance(v, (str, int, bool)):
                lines.append(f"  {k}={json.dumps(v)}")

    return "\n".join(lines) if len(lines) > 1 else f"**{name}** : présent"


def _compact_pyproject(raw: str) -> str:
    lines = ["**pyproject.toml** :"]
    in_tool = False
    tool_name = ""
    count = 0
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("[tool."):
            tool_name = stripped[6:].rstrip("]")
            in_tool = True
            lines.append(f"  [{tool_name}] section présente")
            count += 1
            if count >= 6:
                break
        elif stripped.startswith("[") and not stripped.startswith("[tool."):
            in_tool = False
    return "\n".join(lines) if len(lines) > 1 else "**pyproject.toml** : présent"


def _compact_editorconfig(raw: str) -> str:
    lines = ["**editorconfig** :"]
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            lines.append(f"  {stripped}")
        if len(lines) > 8:
            break
    return "\n".join(lines) if len(lines) > 1 else "**editorconfig** : présent"


def _compact_prettier(name: str, raw: str) -> str:
    lines = [f"**{name}** :"]
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("//", "#", "{")):
            lines.append(f"  {stripped[:80]}")
        if len(lines) > 6:
            break
    return "\n".join(lines)


__all__ = ["scan_conventions"]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# ──────────────────────────────────────────────────────────────────────────────
