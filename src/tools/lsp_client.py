"""
🔌 LUMENA — LSP Client Engine

Client LSP (Language Server Protocol) pour communiquer avec des language servers
externes (pyright, typescript-language-server, etc.) via JSON-RPC sur stdin/stdout.

Donne à Lumena l'accès aux vraies diagnostics de type, go-to-definition,
find-references, hover info — sans être un IDE.

Usage:
    async with LSPClient("pyright-langserver", ["--stdio"]) as lsp:
        diags = await lsp.get_diagnostics("/path/to/project")
        symbols = await lsp.get_definitions("file.py", line=10, col=5)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote as url_quote

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# DATA TYPES
# ═══════════════════════════════════════════════════════════════

class DiagnosticSeverity(IntEnum):
    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4


@dataclass
class LSPDiagnostic:
    """Un diagnostic renvoyé par le language server."""
    file_path: str          # chemin absolu
    line: int               # 0-based
    col: int                # 0-based
    end_line: int = 0
    end_col: int = 0
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    message: str = ""
    code: str = ""          # ex: "reportMissingImports"
    source: str = ""        # ex: "Pyright"

    def __str__(self) -> str:
        sev = self.severity.name
        loc = f"{self.file_path}:{self.line + 1}:{self.col + 1}"
        return f"[{sev}] {loc} — {self.source}/{self.code}: {self.message}"


@dataclass
class LSPSymbol:
    """Un symbole renvoyé par go-to-definition ou find-references."""
    file_path: str
    line: int
    col: int
    end_line: int = 0
    end_col: int = 0
    name: str = ""
    kind: str = ""  # "function", "class", "variable", etc.


@dataclass
class LSPHoverInfo:
    """Info hover (type, doc) pour un symbole."""
    contents: str = ""  # markdown ou texte brut
    language: str = ""  # "python", "typescript", etc.


# ═══════════════════════════════════════════════════════════════
# SERVER REGISTRY — quels servers pour quels langages
# ═══════════════════════════════════════════════════════════════

@dataclass
class ServerConfig:
    """Configuration d'un language server."""
    command: List[str]        # ex: ["pyright-langserver", "--stdio"]
    languages: List[str]      # ex: ["python"]
    extensions: List[str]     # ex: [".py", ".pyi"]
    install_hint: str = ""    # ex: "pip install pyright"


# Registre des servers connus
KNOWN_SERVERS: Dict[str, ServerConfig] = {
    "pyright": ServerConfig(
        command=["pyright-langserver", "--stdio"],
        languages=["python"],
        extensions=[".py", ".pyi"],
        install_hint="pip install pyright",
    ),
    "pylsp": ServerConfig(
        command=["pylsp"],
        languages=["python"],
        extensions=[".py"],
        install_hint="pip install python-lsp-server",
    ),
    "typescript": ServerConfig(
        command=["typescript-language-server", "--stdio"],
        languages=["typescript", "javascript"],
        extensions=[".ts", ".tsx", ".js", ".jsx"],
        install_hint="npm install -g typescript-language-server typescript",
    ),
    "css": ServerConfig(
        command=["vscode-css-language-server", "--stdio"],
        languages=["css", "scss", "less"],
        extensions=[".css", ".scss", ".less"],
        install_hint="npm install -g vscode-langservers-extracted",
    ),
    "html": ServerConfig(
        command=["vscode-html-language-server", "--stdio"],
        languages=["html"],
        extensions=[".html", ".htm"],
        install_hint="npm install -g vscode-langservers-extracted",
    ),
    "json": ServerConfig(
        command=["vscode-json-language-server", "--stdio"],
        languages=["json"],
        extensions=[".json", ".jsonc"],
        install_hint="npm install -g vscode-langservers-extracted",
    ),
}


def detect_available_servers() -> Dict[str, ServerConfig]:
    """Détecte quels language servers sont installés sur le système."""
    available = {}
    for name, config in KNOWN_SERVERS.items():
        exe = config.command[0]
        # Chercher dans PATH + venv
        found = shutil.which(exe)
        if not found:
            # Chercher aussi avec npx pour les serveurs Node
            if "npm" in config.install_hint:
                npx = shutil.which("npx")
                if npx:
                    # npx résout automatiquement
                    available[name] = ServerConfig(
                        command=["npx", exe] + config.command[1:],
                        languages=config.languages,
                        extensions=config.extensions,
                        install_hint=config.install_hint,
                    )
                    continue
        else:
            available[name] = config
    return available


# ═══════════════════════════════════════════════════════════════
# JSON-RPC TRANSPORT (stdin/stdout)
# ═══════════════════════════════════════════════════════════════

class JsonRpcTransport:
    """Transport JSON-RPC 2.0 sur stdin/stdout d'un processus enfant."""

    def __init__(self, process: asyncio.subprocess.Process):
        self._process = process
        self._stdin = process.stdin
        self._stdout = process.stdout
        self._request_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._notifications: Dict[str, List[asyncio.Future]] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._diagnostics: Dict[str, List[Dict[str, Any]]] = {}
        self._diag_event = asyncio.Event()

    def start_reader(self) -> None:
        """Démarre la boucle de lecture des messages du serveur."""
        self._reader_task = asyncio.create_task(self._read_loop())

    async def send_request(self, method: str, params: Any = None, timeout: float = 30.0) -> Any:
        """Envoie une requête JSON-RPC et attend la réponse."""
        self._request_id += 1
        msg_id = self._request_id
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            msg["params"] = params

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future

        self._write_message(msg)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"LSP request {method} timed out after {timeout}s")

    def send_notification(self, method: str, params: Any = None) -> None:
        """Envoie une notification (pas de réponse attendue)."""
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write_message(msg)

    def _write_message(self, msg: Dict[str, Any]) -> None:
        """Écrit un message JSON-RPC avec header Content-Length."""
        body = json.dumps(msg, ensure_ascii=False)
        body_bytes = body.encode("utf-8")
        header = f"Content-Length: {len(body_bytes)}\r\n\r\n"
        if self._stdin and not self._stdin.is_closing():
            self._stdin.write(header.encode("ascii") + body_bytes)

    async def _read_loop(self) -> None:
        """Lit les messages JSON-RPC du serveur en continu."""
        try:
            while self._stdout and not self._stdout.at_eof():
                # Lire le header
                headers = {}
                while True:
                    line = await self._stdout.readline()
                    if not line:
                        return  # EOF
                    line_str = line.decode("ascii", errors="replace").strip()
                    if not line_str:
                        break  # Fin du header (ligne vide)
                    if ":" in line_str:
                        key, val = line_str.split(":", 1)
                        headers[key.strip().lower()] = val.strip()

                content_length = int(headers.get("content-length", "0"))
                if content_length == 0:
                    continue

                body = await self._stdout.readexactly(content_length)
                try:
                    msg = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.debug("[LSP] Malformed JSON-RPC message")
                    continue

                self._handle_message(msg)
        except (asyncio.CancelledError, ConnectionError):
            pass  # reader loop arrêté normalement
        except Exception as e:
            logger.debug("[LSP] Reader loop error: {}", e)

    def _handle_message(self, msg: Dict[str, Any]) -> None:
        """Dispatche un message reçu du serveur."""
        if "id" in msg and "method" not in msg:
            # Response
            msg_id = msg["id"]
            future = self._pending.pop(msg_id, None)
            if future and not future.done():
                if "error" in msg:
                    future.set_exception(
                        RuntimeError(f"LSP error: {msg['error'].get('message', msg['error'])}")
                    )
                else:
                    future.set_result(msg.get("result"))
        elif "method" in msg and "id" not in msg:
            # Notification du serveur
            method = msg["method"]
            params = msg.get("params", {})
            if method == "textDocument/publishDiagnostics":
                uri = params.get("uri", "")
                diags = params.get("diagnostics", [])
                self._diagnostics[uri] = diags
                self._diag_event.set()
            # Ignorer les autres notifications silencieusement
        elif "method" in msg and "id" in msg:
            # Server request (window/workDoneProgress/create, etc.) — répondre vide
            response = {"jsonrpc": "2.0", "id": msg["id"], "result": None}
            self._write_message(response)

    async def wait_diagnostics(self, timeout: float = 15.0) -> Dict[str, List[Dict]]:
        """Attend que le serveur envoie des diagnostics."""
        self._diag_event.clear()
        try:
            await asyncio.wait_for(self._diag_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass  # pas de diagnostics dans le délai, on continue
        # Attendre un peu plus pour les diagnostics batch
        await asyncio.sleep(0.5)
        return dict(self._diagnostics)

    async def close(self) -> None:
        """Ferme proprement le transport."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass  # reader task annulée normalement
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()


# ═══════════════════════════════════════════════════════════════
# LSP CLIENT
# ═══════════════════════════════════════════════════════════════

def _path_to_uri(path: str) -> str:
    """Convertit un chemin en URI file:///."""
    p = Path(path).resolve()
    # Windows: file:///C:/Users/...
    if sys.platform == "win32":
        return "file:///" + str(p).replace("\\", "/")
    return "file://" + str(p)


def _uri_to_path(uri: str) -> str:
    """Convertit un URI file:/// en chemin."""
    if uri.startswith("file:///"):
        path = uri[8:] if sys.platform == "win32" else uri[7:]
        # Décoder les %XX
        from urllib.parse import unquote
        return unquote(path)
    return uri


# Mapping extension → languageId LSP
_LANG_IDS = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascriptreact",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "typescriptreact",
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "scss", ".less": "less",
    ".json": "json", ".jsonc": "jsonc",
    ".md": "markdown",
    ".rs": "rust", ".go": "go", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
}


class LSPClient:
    """
    Client LSP haut niveau.

    Usage:
        async with LSPClient("pyright", project_dir="/path/to/project") as lsp:
            diags = await lsp.get_diagnostics(["src/main.py", "src/utils.py"])
    """

    def __init__(
        self,
        server_name: str,
        project_dir: str | Path,
        server_command: Optional[List[str]] = None,
    ):
        self.server_name = server_name
        self.project_dir = Path(project_dir).resolve()
        self._config = KNOWN_SERVERS.get(server_name)
        self._command = server_command or (self._config.command if self._config else [])
        self._process: Optional[asyncio.subprocess.Process] = None
        self._transport: Optional[JsonRpcTransport] = None
        self._initialized = False
        self._open_docs: set = set()
        self._server_capabilities: Dict[str, Any] = {}

    async def __aenter__(self) -> "LSPClient":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        """Démarre le language server et effectue le handshake initialize."""
        if not self._command:
            raise RuntimeError(
                f"Pas de commande pour le serveur '{self.server_name}'. "
                f"Installer avec: {self._config.install_hint if self._config else '?'}"
            )

        exe = self._command[0]
        if not shutil.which(exe) and not (len(self._command) > 1 and shutil.which("npx")):
            raise FileNotFoundError(
                f"Exécutable '{exe}' introuvable. "
                f"Installer avec: {self._config.install_hint if self._config else '?'}"
            )

        logger.info("[LSP] Démarrage {} : {}", self.server_name, " ".join(self._command))

        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.project_dir),
        )

        self._transport = JsonRpcTransport(self._process)
        self._transport.start_reader()

        # ── Initialize handshake ──
        init_params = {
            "processId": os.getpid(),
            "rootUri": _path_to_uri(str(self.project_dir)),
            "rootPath": str(self.project_dir),
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {
                        "relatedInformation": True,
                        "tagSupport": {"valueSet": [1, 2]},
                        "codeDescriptionSupport": True,
                    },
                    "synchronization": {
                        "didSave": True,
                        "willSave": False,
                        "willSaveWaitUntil": False,
                        "dynamicRegistration": False,
                    },
                    "completion": {
                        "snippetSupport": False,
                        "completionItem": {"documentationFormat": ["plaintext"]},
                    },
                    "hover": {
                        "contentFormat": ["markdown", "plaintext"],
                    },
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "documentSymbol": {"dynamicRegistration": False},
                },
                "workspace": {
                    "workspaceFolders": True,
                    "configuration": True,
                    "didChangeConfiguration": {"dynamicRegistration": False},
                },
            },
            "workspaceFolders": [
                {
                    "uri": _path_to_uri(str(self.project_dir)),
                    "name": self.project_dir.name,
                }
            ],
        }

        result = await self._transport.send_request("initialize", init_params, timeout=30)
        self._server_capabilities = result.get("capabilities", {}) if result else {}
        self._transport.send_notification("initialized", {})
        self._initialized = True
        logger.info("[LSP] {} initialisé (capabilities: {})", self.server_name, list(self._server_capabilities.keys()))

    async def stop(self) -> None:
        """Arrête proprement le language server."""
        if not self._initialized:
            return

        # Fermer tous les documents ouverts
        for uri in list(self._open_docs):
            try:
                self._transport.send_notification("textDocument/didClose", {
                    "textDocument": {"uri": uri},
                })
            except Exception:
                pass  # document close best-effort
        self._open_docs.clear()

        # Shutdown + exit
        try:
            if self._transport:
                await self._transport.send_request("shutdown", timeout=5)
                self._transport.send_notification("exit")
        except Exception:
            pass  # LSP shutdown best-effort

        # Tuer le processus si encore vivant
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=3)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass  # process déjà mort

        if self._transport:
            await self._transport.close()

        self._initialized = False
        logger.info("[LSP] {} arrêté", self.server_name)

    # ─────────────────────────────────────────────────────────────
    # Document Management
    # ─────────────────────────────────────────────────────────────

    def _open_document(self, file_path: str, content: Optional[str] = None) -> str:
        """Ouvre un document dans le language server. Retourne l'URI."""
        abs_path = str(Path(file_path).resolve()) if not Path(file_path).is_absolute() else file_path
        uri = _path_to_uri(abs_path)

        if uri in self._open_docs:
            return uri

        if content is None:
            try:
                content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = ""  # fichier illisible, on ouvre avec contenu vide

        ext = Path(abs_path).suffix.lower()
        lang_id = _LANG_IDS.get(ext, "plaintext")

        self._transport.send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": lang_id,
                "version": 1,
                "text": content,
            },
        })
        self._open_docs.add(uri)
        return uri

    def _close_document(self, uri: str) -> None:
        """Ferme un document dans le language server."""
        if uri not in self._open_docs:
            return
        self._transport.send_notification("textDocument/didClose", {
            "textDocument": {"uri": uri},
        })
        self._open_docs.discard(uri)

    # ─────────────────────────────────────────────────────────────
    # Diagnostics (erreurs, warnings, etc.)
    # ─────────────────────────────────────────────────────────────

    async def get_diagnostics(
        self,
        files: Optional[List[str]] = None,
        timeout: float = 15.0,
    ) -> List[LSPDiagnostic]:
        """
        Récupère les diagnostics pour les fichiers spécifiés (ou tout le projet).

        Args:
            files: Liste de chemins de fichiers (relatifs au projet ou absolus).
                   Si None, ouvre tous les fichiers supportés du projet.
            timeout: Temps max d'attente pour les diagnostics.

        Returns:
            Liste de LSPDiagnostic.
        """
        if not self._initialized:
            raise RuntimeError("LSP client not initialized. Call start() first.")

        # Résoudre les fichiers
        if files is None:
            files = self._discover_project_files()

        # Ouvrir tous les documents
        uris = []
        for f in files:
            abs_path = str((self.project_dir / f).resolve()) if not Path(f).is_absolute() else f
            uri = self._open_document(abs_path)
            uris.append(uri)

        # Attendre que le serveur envoie les diagnostics
        raw_diags = await self._transport.wait_diagnostics(timeout=timeout)

        # Convertir en LSPDiagnostic
        result: List[LSPDiagnostic] = []
        for uri, diag_list in raw_diags.items():
            file_path = _uri_to_path(uri)
            for d in diag_list:
                rng = d.get("range", {})
                start = rng.get("start", {})
                end = rng.get("end", {})
                severity = DiagnosticSeverity(d.get("severity", 1))
                code = d.get("code", "")
                if isinstance(code, dict):
                    code = code.get("value", str(code))
                result.append(LSPDiagnostic(
                    file_path=file_path,
                    line=start.get("line", 0),
                    col=start.get("character", 0),
                    end_line=end.get("line", 0),
                    end_col=end.get("character", 0),
                    severity=severity,
                    message=d.get("message", ""),
                    code=str(code),
                    source=d.get("source", self.server_name),
                ))

        return result

    # ─────────────────────────────────────────────────────────────
    # Go-to-Definition
    # ─────────────────────────────────────────────────────────────

    async def get_definitions(
        self, file_path: str, line: int, col: int,
    ) -> List[LSPSymbol]:
        """
        Go-to-definition pour un symbole à une position donnée.

        Args:
            file_path: Chemin du fichier
            line: Ligne (0-based)
            col: Colonne (0-based)
        """
        abs_path = str((self.project_dir / file_path).resolve()) if not Path(file_path).is_absolute() else file_path
        uri = self._open_document(abs_path)

        result = await self._transport.send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        })

        return self._parse_locations(result)

    # ─────────────────────────────────────────────────────────────
    # Find References
    # ─────────────────────────────────────────────────────────────

    async def get_references(
        self, file_path: str, line: int, col: int, include_declaration: bool = True,
    ) -> List[LSPSymbol]:
        """Find-references pour un symbole."""
        abs_path = str((self.project_dir / file_path).resolve()) if not Path(file_path).is_absolute() else file_path
        uri = self._open_document(abs_path)

        result = await self._transport.send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
            "context": {"includeDeclaration": include_declaration},
        })

        return self._parse_locations(result)

    # ─────────────────────────────────────────────────────────────
    # Hover (type info)
    # ─────────────────────────────────────────────────────────────

    async def get_hover(
        self, file_path: str, line: int, col: int,
    ) -> Optional[LSPHoverInfo]:
        """Récupère l'info hover (type, docstring) pour un symbole."""
        abs_path = str((self.project_dir / file_path).resolve()) if not Path(file_path).is_absolute() else file_path
        uri = self._open_document(abs_path)

        result = await self._transport.send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        })

        if not result or "contents" not in result:
            return None

        contents = result["contents"]
        if isinstance(contents, str):
            return LSPHoverInfo(contents=contents)
        elif isinstance(contents, dict):
            return LSPHoverInfo(
                contents=contents.get("value", ""),
                language=contents.get("language", contents.get("kind", "")),
            )
        elif isinstance(contents, list):
            parts = []
            for item in contents:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("value", ""))
            return LSPHoverInfo(contents="\n\n".join(parts))

        return None

    # ─────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────

    def _discover_project_files(self, max_files: int = 200) -> List[str]:
        """Découvre les fichiers du projet supportés par ce server."""
        if not self._config:
            return []

        exts = set(self._config.extensions)
        files = []
        for fp in self.project_dir.rglob("*"):
            if len(files) >= max_files:
                break
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in exts:
                continue
            # Skip les dossiers ignorés
            rel = fp.relative_to(self.project_dir)
            parts = rel.parts
            if any(p.startswith(".") or p in ("node_modules", "__pycache__", "venv", ".venv", "dist", "build") for p in parts):
                continue
            files.append(str(fp))
        return files

    def _parse_locations(self, result: Any) -> List[LSPSymbol]:
        """Parse un résultat Location | Location[] | LocationLink[]."""
        if result is None:
            return []
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list):
            return []

        symbols = []
        for item in result:
            if "targetUri" in item:
                # LocationLink
                uri = item["targetUri"]
                rng = item.get("targetRange", item.get("targetSelectionRange", {}))
            elif "uri" in item:
                # Location
                uri = item["uri"]
                rng = item.get("range", {})
            else:
                continue

            start = rng.get("start", {})
            end = rng.get("end", {})
            symbols.append(LSPSymbol(
                file_path=_uri_to_path(uri),
                line=start.get("line", 0),
                col=start.get("character", 0),
                end_line=end.get("line", 0),
                end_col=end.get("character", 0),
            ))

        return symbols


# ═══════════════════════════════════════════════════════════════
# HIGH-LEVEL API — façade simple pour Lumena
# ═══════════════════════════════════════════════════════════════

async def lsp_check_project(
    project_dir: str | Path,
    files: Optional[List[str]] = None,
    timeout: float = 20.0,
) -> List[LSPDiagnostic]:
    """
    API haut niveau : lance le(s) language server(s), récupère les
    diagnostics, et ferme tout proprement.

    Args:
        project_dir: Répertoire du projet
        files: Fichiers à vérifier (None = auto-discovery)
        timeout: Timeout par serveur

    Returns:
        Liste de tous les diagnostics combinés
    """
    project_dir = Path(project_dir)
    available = detect_available_servers()

    if not available:
        logger.debug("[LSP] Aucun language server disponible")
        return []

    # Déterminer quels serveurs sont nécessaires
    if files:
        needed_exts = {Path(f).suffix.lower() for f in files}
    else:
        needed_exts = set()
        for fp in project_dir.rglob("*"):
            if fp.is_file():
                rel = fp.relative_to(project_dir)
                if not any(p.startswith(".") or p in ("node_modules", "__pycache__", "venv", ".venv") for p in rel.parts):
                    needed_exts.add(fp.suffix.lower())

    # Sélectionner les serveurs par extension
    servers_to_use = {}
    for name, config in available.items():
        if any(ext in needed_exts for ext in config.extensions):
            # Éviter les doublons (préférer pyright à pylsp)
            lang_key = tuple(sorted(config.languages))
            if lang_key not in servers_to_use:
                servers_to_use[lang_key] = (name, config)

    all_diagnostics: List[LSPDiagnostic] = []

    for lang_key, (name, config) in servers_to_use.items():
        try:
            async with LSPClient(name, project_dir) as client:
                # Filtrer les fichiers par extension si spécifié
                if files:
                    server_files = [
                        f for f in files
                        if Path(f).suffix.lower() in config.extensions
                    ]
                else:
                    server_files = None

                if server_files is not None and not server_files:
                    continue

                diags = await client.get_diagnostics(server_files, timeout=timeout)
                all_diagnostics.extend(diags)
                logger.info("[LSP] {} : {} diagnostic(s)", name, len(diags))
        except FileNotFoundError:
            logger.debug("[LSP] Server {} non trouvé, skip", name)
        except Exception as e:
            logger.debug("[LSP] Erreur avec {}: {}", name, e)

    return all_diagnostics


def get_install_instructions() -> str:
    """Retourne les instructions pour installer les language servers manquants."""
    available = detect_available_servers()
    lines = ["### Language Servers disponibles\n"]
    for name, config in KNOWN_SERVERS.items():
        status = "✅" if name in available else "❌"
        lines.append(f"{status} **{name}** ({', '.join(config.languages)})")
        if name not in available:
            lines.append(f"   `{config.install_hint}`")
    return "\n".join(lines)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
