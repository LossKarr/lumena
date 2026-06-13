"""
client_factory.py — Factory production pour MCPClient (Phase 19.5 v2).

Construit un MCPClient connecté au subprocess actif d'un MCPSandboxRunner
(Phase 5 + Phase 19.5 propriété `process` read-only).

DOCTRINE Phase 19.5 :
  - Aucun lifecycle subprocess : la factory n'invoque NI start NI stop.
    Le runner reste propriétaire exclusif du subprocess.
  - Aucun appel à client.initialize() : laissé au caller (typiquement
    MCPActivationService Phase 19), cohérent avec Phase 7 où
    is_initialized=False par défaut.
  - Aucun appel subprocess direct : la factory consomme uniquement le Popen
    exposé par MCPSandboxRunner.process (propriété read-only Phase 19.5).
  - Defensive : refuse runner None, runner sans propriété process, process
    None, process déjà terminé (poll != None), spec absente, server_name
    invalide.
  - Wrap MCPClientError → ClientFactoryError("client_create_failed") avec
    __cause__ préservé. Aucun autre Exception générique wrappé (les bugs
    upstream doivent remonter).
  - Aucun câblage runtime : la factory est utilisable par
    MCPActivationService via le Protocol ClientFactoryLike, mais aucun
    caller production ne l'invoque actuellement.

Codes d'erreur ClientFactoryError (whitelist) :
  - runner_invalid:none                    runner = None
  - runner_invalid:no_process_property     runner ne possède pas .process
  - runner_not_started                     runner.process is None
  - runner_not_alive                       process.poll() retourne un exit code
  - runner_invalid:no_spec                 runner ne possède pas .spec
  - runner_invalid:no_server_name          .spec.name manquant/non-str/vide
  - client_create_failed                   MCPClient(...) raise MCPClientError
                                           (wrap avec __cause__)
"""
from __future__ import annotations

from typing import Any

from src.mcp.client import MCPClient, MCPClientError


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_TIMEOUT_S = 30.0


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────


class ClientFactoryError(Exception):
    """Erreur de construction du client.

    Codes :
      - runner_invalid:<reason>
      - runner_not_started
      - runner_not_alive
      - client_create_failed (wrap MCPClientError, __cause__ préservé)
    """


# ──────────────────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────────────────


def create_mcp_client_from_runner(
    runner: Any,
    *,
    default_timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> MCPClient:
    """Construit un MCPClient depuis un MCPSandboxRunner déjà démarré.

    Pré-conditions :
      - runner non None
      - runner expose une propriété `.process` (classe ou instance)
      - runner.process is not None (start() a réussi)
      - runner.process.poll() is None (subprocess vivant)
      - runner expose `.spec.name` (str non vide)

    Returns : un MCPClient prêt pour initialize() / list_tools() / call_tool().

    N'invoque PAS runner.start() ni runner.stop() ni client.initialize().

    Raises ClientFactoryError pour toute pré-condition violée.
    Les MCPClientError levées par MCPClient(...) (ex : stdin/stdout/stderr
    invalides) sont wrappées en ClientFactoryError("client_create_failed")
    avec `__cause__` préservé. Aucune autre Exception générique n'est
    wrappée : les bugs upstream remontent.
    """
    # 1. Runner None
    if runner is None:
        raise ClientFactoryError("runner_invalid:none")

    # 2. Runner expose .process ?
    if not hasattr(type(runner), "process") and not hasattr(runner, "process"):
        raise ClientFactoryError("runner_invalid:no_process_property")

    # 3. Process actif ?
    process = runner.process
    if process is None:
        raise ClientFactoryError("runner_not_started")

    # 4. Defensive : process.poll() — même si runner.process filtre déjà,
    # un fake/runner custom peut retourner un Popen mort.
    poll_method = getattr(process, "poll", None)
    if callable(poll_method):
        try:
            poll_result = poll_method()
        except Exception as e:  # noqa: BLE001
            raise ClientFactoryError("runner_not_alive") from e
        if poll_result is not None:
            raise ClientFactoryError("runner_not_alive")

    # 5. Spec présente ?
    spec = getattr(runner, "spec", None)
    if spec is None:
        raise ClientFactoryError("runner_invalid:no_spec")

    # 6. server_name valide ?
    server_name = getattr(spec, "name", None)
    if not isinstance(server_name, str) or not server_name:
        raise ClientFactoryError("runner_invalid:no_server_name")

    # 7. Construire MCPClient, wrap MCPClientError uniquement
    try:
        return MCPClient(
            process=process,
            server_name=server_name,
            default_timeout_s=default_timeout_s,
        )
    except MCPClientError as e:
        raise ClientFactoryError("client_create_failed") from e
