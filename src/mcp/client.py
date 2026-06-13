"""
client.py — Client MCP stdio + JSON-RPC 2.0 (Phase 7).

Scope strict :
  - Transport stdio uniquement (SSE/WebSocket = Phase 14)
  - JSON-RPC 2.0 minimal sans pydantic (dict simple)
  - 3 méthodes : initialize / list_tools / call_tool
  - Subprocess INJECTÉ (ownership = caller)
  - Timeout par appel
  - Lock global pour sérialiser les appels (un à la fois)

Hors scope :
  - Resources / prompts MCP → Phase ultérieure
  - SSE / WebSocket → Phase 14
  - Reader thread asynchrone + dispatch multi-id → optimisation future
  - Reconnect / restart subprocess → Phase 5 runner

⚠️ COMPATIBILITÉ MCPSandboxRunner Phase 5 :
  Le runner Phase 5 crée le subprocess avec stderr=subprocess.STDOUT
  (logs mergés dans stdout). Cette configuration n'est PAS compatible
  avec le protocole MCP stdio qui exige stdout = JSON-RPC pur.

  MCPClient REFUSE un Popen avec stderr=None (sentinelle de STDOUT merge).
  Pour Phase 7, instancier MCPClient avec un Popen créé MANUELLEMENT :
    subprocess.Popen(
        cmd, env=env, cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,    # ← obligatoire, distinct de stdout
        text=True,
    )

  Une mini Phase 5.1 exposera un mode `stderr_separate=True` dans
  MCPSandboxRunner pour brancher directement Phase 5 ↔ Phase 7.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Constantes MCP
# ──────────────────────────────────────────────────────────────────────────────

# Version stable du protocole MCP au moment de l'implémentation Phase 7
MCP_PROTOCOL_VERSION = "2024-11-05"

# clientInfo annoncé par Lumena au serveur MCP
LUMENA_CLIENT_INFO = {
    "name": "Lumena",
    "version": "1.0",
}

# Capabilities annoncées (Phase 7 = consommateur de tools uniquement)
LUMENA_CLIENT_CAPABILITIES: Dict[str, Any] = {}

_DEFAULT_TIMEOUT_S = 30.0
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB par message JSON-RPC


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────


class MCPClientError(Exception):
    """Erreur générique côté client MCP."""


class MCPTimeoutError(MCPClientError):
    """Timeout sur un appel JSON-RPC."""


class MCPProtocolError(MCPClientError):
    """Réponse JSON-RPC invalide ou inattendue."""


class MCPNotInitializedError(MCPClientError):
    """Méthode appelée avant initialize()."""


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses (input/output)
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MCPTool:
    """Métadonnées d'un tool MCP exposé par le serveur."""

    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass(frozen=True)
class MCPCallResult:
    """Résultat d'un appel `tools/call` MCP.

    Champs alignés sur la spec MCP officielle (camelCase) :
      - content : ce que le serveur retourne (généralement list de TextContent)
      - is_error : valeur de `result.isError` (camelCase dans le JSON-RPC)
      - structured_content : valeur de `result.structuredContent` si présent
      - error_message : message extrait pour confort (premier text si is_error)
    """

    content: Any
    is_error: bool = False
    error_message: Optional[str] = None
    structured_content: Optional[Any] = None


# ──────────────────────────────────────────────────────────────────────────────
# Client MCP
# ──────────────────────────────────────────────────────────────────────────────


class MCPClient:
    """Client MCP stdio + JSON-RPC 2.0.

    Le subprocess est INJECTÉ par le caller (typiquement MCPSandboxRunner
    Phase 5, après mini-Phase 5.1 de mode stderr séparé).

    Thread-safety : un lock global sérialise les appels (un à la fois).
    Phase ultérieure pourra ajouter un reader thread + dispatch multi-id.
    """

    def __init__(
        self,
        process: subprocess.Popen,
        server_name: str,
        *,
        default_timeout_s: float = _DEFAULT_TIMEOUT_S,
    ):
        if not isinstance(server_name, str) or not server_name.strip():
            raise MCPClientError(f"Invalid server_name: {server_name!r}")
        if process.stdin is None or process.stdout is None:
            raise MCPClientError(
                "MCPClient requires Popen with stdin=PIPE and stdout=PIPE"
            )
        # ⚠️ Correction critique : MCP exige stdout = JSON-RPC PUR.
        # Si stderr=None, c'est typiquement un signe de stderr=subprocess.STDOUT
        # (merge dans stdout) ou pas de pipe → ambigu. Refuser.
        if process.stderr is None:
            raise MCPClientError(
                "MCPClient requires Popen with stderr=subprocess.PIPE "
                "(not STDOUT or None). MCP stdio protocol mandates that "
                "stdout carries JSON-RPC messages only. "
                "MCPSandboxRunner Phase 5 currently uses stderr=STDOUT and is "
                "NOT directly compatible; see module docstring."
            )

        self._process = process
        self._server_name = server_name.strip()
        self._default_timeout_s = default_timeout_s

        self._id_counter = 0
        self._call_lock = threading.RLock()
        self._initialized = False
        self._closed = False
        self._server_capabilities: Dict[str, Any] = {}

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def server_name(self) -> str:
        return self._server_name

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def server_capabilities(self) -> Dict[str, Any]:
        return dict(self._server_capabilities)

    # ── ID generation (thread-safe) ───────────────────────────────────────

    def _next_id(self) -> int:
        with self._call_lock:
            self._id_counter += 1
            return self._id_counter

    # ── Low-level I/O ─────────────────────────────────────────────────────

    def _send_message(self, message: Dict[str, Any]) -> None:
        """Sérialise + envoie un message JSON-RPC sur stdin du subprocess."""
        if self._closed:
            raise MCPClientError("Client is closed")
        if self._process.stdin is None:
            raise MCPClientError("Process stdin not available")
        payload = json.dumps(message, ensure_ascii=False) + "\n"
        try:
            self._process.stdin.write(payload)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPProtocolError(f"Failed to write to subprocess: {e}") from e

    def _read_response_with_id(
        self,
        expected_id: int,
        timeout_s: float,
    ) -> Dict[str, Any]:
        """Lit des lignes stdout jusqu'à trouver une réponse avec l'id attendu.

        Comportement Phase 7 :
          - Lignes non-JSON ignorées (mais loggables si debug)
          - Notifications (sans id) ignorées
          - Réponses avec id != expected_id : MCPProtocolError
            (Phase 7 sérialise les calls via call_lock, donc on attend exactement
            la réponse de notre call)
          - Timeout absolu : pas plus de timeout_s pour la séquence complète
        """
        deadline = time.monotonic() + timeout_s
        if self._process.stdout is None:
            raise MCPClientError("Process stdout not available")

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPTimeoutError(
                    f"Timed out waiting for response id={expected_id} "
                    f"after {timeout_s}s"
                )
            line = self._readline_with_timeout(remaining)
            if line is None:
                # Fix W (Phase I-7) : distinguer EOF (process mort) du vrai
                # timeout. Avant, un EOF levait "Timed out ... after 30.0s"
                # en quelques ms — message trompeur qui a coûté une session
                # de debugging. poll() non-None = process terminé.
                exit_code = None
                try:
                    exit_code = self._process.poll()
                except Exception:  # noqa: BLE001
                    pass
                # Fix X : cause précise du None (timeout/eof/exception:<T>)
                read_cause = getattr(self, "_last_read_failure", None) or "?"
                if exit_code is not None:
                    # Canal définitivement mort → marquer le client closed
                    # pour que is_running() (Fix W) le voie et que Fix S
                    # déclenche le self-healing.
                    self._closed = True
                    raise MCPProtocolError(
                        f"MCP server process died (exit_code={exit_code}, "
                        f"read_cause={read_cause}) "
                        f"while waiting for response id={expected_id}. "
                        "Le serveur doit être réactivé (run_mcp_autonomy)."
                    )
                if read_cause != "timeout":
                    # Process VIVANT mais lecture impossible (eof/exception)
                    # → canal stdio cassé. Erreur protocole, pas timeout.
                    # Client marqué closed → self-healing possible (Fix S/W).
                    self._closed = True
                    raise MCPProtocolError(
                        f"MCP stdio channel broken (read_cause={read_cause}, "
                        f"process alive) while waiting for response "
                        f"id={expected_id}. Réactivation requise "
                        "(run_mcp_autonomy)."
                    )
                raise MCPTimeoutError(
                    f"Timed out waiting for response id={expected_id} "
                    f"after {timeout_s}s"
                )
            line = line.strip()
            if not line:
                continue
            if len(line) > _MAX_RESPONSE_BYTES:
                raise MCPProtocolError("Response exceeds max size")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                # Ligne non-JSON : peut être du bruit, on ignore
                continue
            if not isinstance(msg, dict):
                continue
            msg_id = msg.get("id")
            if msg_id is None:
                # Notification ou message sans id : ignorer
                continue
            if msg_id != expected_id:
                raise MCPProtocolError(
                    f"Unexpected response id={msg_id}, expected {expected_id}"
                )
            return msg

    def _readline_with_timeout(self, timeout_s: float) -> Optional[str]:
        """Lit une ligne sur stdout avec timeout.

        Implémentation simple Phase 7 : utilise un thread pour lire et un
        Event pour signaler. Bloquant sinon.

        Fix X (Phase I-7) : self._last_read_failure expose la cause exacte
        d'un retour None — "timeout" | "eof" | "exception:<Type>: <msg>".
        Indispensable : un retour None ambigu a masqué pendant 2 sessions
        la vraie cause d'un échec (process vivant + readline None en 360ms).
        """
        self._last_read_failure: Optional[str] = None
        if self._process.stdout is None:
            self._last_read_failure = "stdout_none"
            return None
        result_holder: Dict[str, Any] = {"line": None, "exc": None}
        done = threading.Event()

        def _reader():
            try:
                result_holder["line"] = self._process.stdout.readline()
            except Exception as _read_exc:  # noqa: BLE001
                result_holder["line"] = None
                result_holder["exc"] = (
                    f"{type(_read_exc).__name__}: {str(_read_exc)[:200]}"
                )
            finally:
                done.set()

        t = threading.Thread(
            target=_reader,
            name=f"mcp-readline-{self._server_name}",
            daemon=True,
        )
        t.start()
        if not done.wait(timeout=timeout_s):
            self._last_read_failure = "timeout"
            return None
        if result_holder["exc"] is not None:
            self._last_read_failure = f"exception:{result_holder['exc']}"
            return None
        line = result_holder["line"]
        if line == "" or line is None:
            self._last_read_failure = "eof"
            return None
        return line

    # ── JSON-RPC primitives ───────────────────────────────────────────────

    def _call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Envoie une requête JSON-RPC avec id et attend la réponse.

        Returns le champ `result` du message JSON-RPC.
        Raise MCPClientError si `error` field présent.
        """
        if self._closed:
            raise MCPClientError("Client is closed")
        timeout = timeout_s if timeout_s is not None else self._default_timeout_s

        with self._call_lock:
            req_id = self._next_id()
            message = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
            }
            if params is not None:
                message["params"] = params
            self._send_message(message)
            response = self._read_response_with_id(req_id, timeout)

        if "error" in response:
            err = response["error"]
            code = err.get("code", -1) if isinstance(err, dict) else -1
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise MCPClientError(f"MCP error {code}: {msg}")
        if "result" not in response:
            raise MCPProtocolError("JSON-RPC response missing 'result' and 'error'")
        return response["result"]

    def _send_notification(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Envoie une notification JSON-RPC (pas d'id, pas de réponse attendue)."""
        if self._closed:
            raise MCPClientError("Client is closed")
        message = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            message["params"] = params
        with self._call_lock:
            self._send_message(message)

    # ── Méthodes haut-niveau ───────────────────────────────────────────────

    def initialize(
        self,
        *,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Envoie `initialize` avec params obligatoires MCP.

        Params envoyés :
          - protocolVersion : MCP_PROTOCOL_VERSION
          - capabilities : LUMENA_CLIENT_CAPABILITIES
          - clientInfo : LUMENA_CLIENT_INFO

        Après succès, envoie la notification `notifications/initialized`
        conformément au cycle de vie MCP.

        Returns le dict capabilities du serveur (`result.capabilities`).
        """
        if self._initialized:
            raise MCPClientError("MCPClient already initialized")
        params = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": dict(LUMENA_CLIENT_CAPABILITIES),
            "clientInfo": dict(LUMENA_CLIENT_INFO),
        }
        result = self._call("initialize", params, timeout_s=timeout_s)
        # Capture les capabilities serveur
        if isinstance(result, dict):
            caps = result.get("capabilities")
            if isinstance(caps, dict):
                self._server_capabilities = dict(caps)
        # Notification post-init (cycle de vie MCP)
        self._send_notification("notifications/initialized", {})
        self._initialized = True
        return result

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise MCPNotInitializedError(
                f"MCPClient for {self._server_name!r} not initialized. "
                "Call initialize() first."
            )

    def list_tools(
        self,
        *,
        timeout_s: Optional[float] = None,
    ) -> List[MCPTool]:
        """Appel `tools/list`. Parse les entrées en MCPTool."""
        self._ensure_initialized()
        result = self._call("tools/list", {}, timeout_s=timeout_s)
        if not isinstance(result, dict):
            raise MCPProtocolError("tools/list result is not a dict")
        raw_tools = result.get("tools", [])
        if not isinstance(raw_tools, list):
            raise MCPProtocolError("tools/list 'tools' field is not a list")

        out: List[MCPTool] = []
        for entry in raw_tools:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            desc = entry.get("description", "")
            schema = entry.get("inputSchema") or entry.get("input_schema") or {}
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(schema, dict):
                schema = {}
            out.append(
                MCPTool(
                    name=name,
                    description=str(desc) if desc is not None else "",
                    input_schema=schema,
                )
            )
        return out

    def call_tool(
        self,
        name: str,
        args: Dict[str, Any],
        *,
        timeout_s: Optional[float] = None,
    ) -> MCPCallResult:
        """Appel `tools/call`.

        IMPORTANT : la requête JSON-RPC envoie `params = {"name": name,
        "arguments": args}` conformément à la spec MCP (clé "arguments",
        pas "args"). L'API Python conserve "args" pour cohérence interne.
        """
        self._ensure_initialized()
        if not isinstance(name, str) or not name:
            raise MCPClientError(f"Invalid tool name: {name!r}")
        if not isinstance(args, dict):
            raise MCPClientError("args must be a dict")
        params = {
            "name": name,
            "arguments": args,  # ← clé "arguments" requise par spec MCP
        }
        raw = self._call("tools/call", params, timeout_s=timeout_s)
        return self._parse_call_result(raw)

    @staticmethod
    def _parse_call_result(raw: Any) -> MCPCallResult:
        """Parse un résultat `tools/call` selon la spec MCP officielle.

        Champs spec :
          - content : généralement list de TextContent ({type, text, ...})
          - isError : camelCase (pas snake_case)
          - structuredContent : optionnel, contenu structuré
        """
        if not isinstance(raw, dict):
            return MCPCallResult(
                content=raw,
                is_error=True,
                error_message="tools/call result is not a dict",
            )
        content = raw.get("content")
        is_error = bool(raw.get("isError", False))
        structured = raw.get("structuredContent")
        error_message: Optional[str] = None
        if is_error and isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        error_message = text
                        break
        return MCPCallResult(
            content=content,
            is_error=is_error,
            error_message=error_message,
            structured_content=structured,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Ferme le lien JSON-RPC.

        NE TUE PAS le subprocess — sa vie reste au caller (MCPSandboxRunner).
        Subsequent calls raise MCPClientError.
        """
        with self._call_lock:
            self._closed = True
