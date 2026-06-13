"""
schema_prober.py — Phase I-3 Niveau 3 : probe runtime du MCP installé.

Stratégie (best-effort, optionnel) :
  1. Lance le binaire avec `--help` / `--print-config-schema` (timeout 3s)
  2. Parse la sortie pour extraire les noms de variables d'env
  3. Construit un MCPConfigSchema avec detected_from="probe"

Cette implémentation Niveau 3 est volontairement minimaliste : on tente le
parser de README sur la sortie texte du `--help`. Une future extension
pourra interroger via RPC stdio si le MCP supporte `config/getSchema`.

Doctrine :
  - Subprocess injecté pour testabilité.
  - Timeout court obligatoire (jamais bloquant > 3s).
  - Échec silencieux → None (le Niveau 4 prendra le relais).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional

from src.mcp.config_schema import MCPConfigSchema
from src.mcp.schema_extractor import _build_field, _extract_env_vars


def _wrap_as_bash_block(text: str) -> str:
    """Enrobe une sortie texte plate avec un fence bash pour réutiliser le
    parser de blocs. Évite la duplication de logique extraction."""
    if not isinstance(text, str):
        return ""
    return f"```bash\n{text}\n```"


# ──────────────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProbeOutput:
    """Résultat brut d'une invocation subprocess."""
    stdout: str
    stderr: str
    returncode: int


# Callable injecté pour testabilité.
ProbeRunner = Callable[[List[str], float], ProbeOutput]


# ──────────────────────────────────────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────────────────────────────────────


def probe_schema_from_binary(
    *,
    server_id: str,
    binary_path: str,
    runner: Optional[ProbeRunner] = None,
    timeout_s: float = 3.0,
) -> Optional[MCPConfigSchema]:
    """Tente de déduire un schéma en interrogeant le binaire MCP installé.

    Args:
        server_id: id catalog.
        binary_path: chemin du binaire (ex: data/mcp/<server_id>/bin/mcp-...).
        runner: callable injecté `(cmd, timeout) -> ProbeOutput`. Par défaut
            utilise subprocess.run.
        timeout_s: timeout dur (échec → None).

    Returns:
        MCPConfigSchema(detected_from="probe") si on a trouvé au moins
        un champ, sinon None.
    """
    if not isinstance(server_id, str) or not server_id:
        return None
    if not isinstance(binary_path, str) or not binary_path:
        return None
    if runner is None:
        runner = _default_runner

    found_names = set()
    for help_flag in ("--print-config-schema", "--help-config", "--help"):
        try:
            out = runner([binary_path, help_flag], timeout_s)
        except Exception:  # noqa: BLE001
            continue
        # On accepte stdout ou stderr (certains MCPs envoient le help sur stderr)
        text = "\n".join(filter(None, [out.stdout, out.stderr]))
        if not text:
            continue
        found_names |= _extract_env_vars(text)
        if found_names:
            break

    if not found_names:
        return None
    fields = tuple(_build_field(name) for name in sorted(found_names))
    return MCPConfigSchema(
        server_id=server_id,
        fields=fields,
        auth_flows=(),
        detected_from="probe",
        detected_at=datetime.now(timezone.utc).isoformat(),
    )


def _default_runner(cmd: List[str], timeout_s: float) -> ProbeOutput:
    """Runner par défaut. Best-effort, jamais raise."""
    import subprocess
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return ProbeOutput(
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            returncode=proc.returncode,
        )
    except Exception:  # noqa: BLE001
        return ProbeOutput(stdout="", stderr="", returncode=-1)


__all__ = ["probe_schema_from_binary", "ProbeOutput", "ProbeRunner"]
