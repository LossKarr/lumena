"""
IDE Bridge — WebSocket connection manager for Lumena ↔ IDE bidirectional control.

Lumena runs a dedicated WebSocket server on port 8245.
The IDE (Electron) connects to ws://127.0.0.1:8245 as client.
Lumena can push commands (open_file, write_file, terminal_run, navigate, …)
and receive results from the IDE.
"""

import asyncio
import json
import logging
import os
import threading
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger("lumena.ide_bridge")

IDE_WS_PORT = int(os.getenv("LUMENA_IDE_WS_PORT", "8245"))

# Singleton
_instance: Optional["IDEBridge"] = None
_instance_lock = threading.Lock()


def get_ide_bridge() -> "IDEBridge":
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = IDEBridge()
    return _instance


class IDEBridge:
    """Manages a single WebSocket connection to the Lumena IDE."""

    def __init__(self) -> None:
        self._ws = None  # websockets connection
        self._pending: Dict[str, asyncio.Future] = {}
        self._connected = False
        self._workspace: Optional[str] = None
        self._server = None  # websockets.Server

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def workspace(self) -> Optional[str]:
        return self._workspace

    # ── Standalone WebSocket server on port 8245 ──

    async def start_server(self) -> None:
        """Start the standalone WebSocket server on IDE_WS_PORT."""
        try:
            import websockets
        except ImportError:
            logger.warning("[IDE-Bridge] websockets not installed — IDE bridge inactive")
            return

        import logging as _logging
        _ws_quiet = _logging.getLogger("websockets.server.ide_bridge")
        _ws_quiet.setLevel(_logging.ERROR)
        self._server = await websockets.serve(
            self._connection_handler,
            "127.0.0.1",
            IDE_WS_PORT,
            logger=_ws_quiet,
        )
        logger.info(f"[IDE-Bridge] WebSocket server listening on ws://127.0.0.1:{IDE_WS_PORT}")

    async def stop_server(self) -> None:
        """Stop the standalone WebSocket server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("[IDE-Bridge] WebSocket server stopped")

    async def _connection_handler(self, websocket: Any) -> None:
        """Handle an incoming IDE WebSocket connection."""
        # Only accept one IDE at a time — disconnect previous
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass  # WS cleanup best-effort
        self._ws = websocket
        self._connected = True
        logger.info("[IDE-Bridge] IDE connected")
        try:
            async for raw in websocket:
                await self.handle_message(raw)
        except Exception as e:
            logger.debug(f"IDE WS loop ended: {e}")
        finally:
            if self._ws is websocket:
                await self.unregister()

    async def register(self, websocket: Any) -> None:
        """Register an IDE WebSocket connection (legacy FastAPI path — unused with standalone server)."""
        self._ws = websocket
        self._connected = True
        logger.info("[IDE-Bridge] IDE connected (via FastAPI)")

    async def unregister(self) -> None:
        """Unregister the IDE connection."""
        self._ws = None
        self._connected = False
        self._workspace = None
        for rid, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(ConnectionError("IDE disconnected"))
        self._pending.clear()
        logger.info("[IDE-Bridge] IDE disconnected")

    async def handle_message(self, raw: str) -> None:
        """Process an incoming message from the IDE."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type", "")

        if msg_type == "ide_connected":
            self._workspace = msg.get("workspace")
            logger.info(f"[IDE-Bridge] IDE workspace: {self._workspace}")
            return

        if msg_type == "pong":
            return

        if msg_type == "result":
            request_id = msg.get("request_id")
            if request_id and request_id in self._pending:
                fut = self._pending.pop(request_id)
                if not fut.done():
                    fut.set_result(msg)
            return

    # ── Public API for Lumena handlers ──

    async def send_command(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Send a command to the IDE and wait for the result."""
        if not self.connected:
            return {"success": False, "error": "IDE not connected"}

        request_id = str(uuid.uuid4())[:12]
        payload = json.dumps({
            "type": "command",
            "action": action,
            "request_id": request_id,
            "params": params or {},
        })

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[request_id] = fut

        try:
            # Support both websockets (send) and FastAPI WebSocket (send_text)
            send = getattr(self._ws, "send", None) or getattr(self._ws, "send_text", None)
            if send is None:
                raise RuntimeError("WebSocket has no send method")
            await send(payload)
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            return {"success": False, "error": "IDE command timed out"}
        except Exception as e:
            self._pending.pop(request_id, None)
            return {"success": False, "error": str(e)}

    # ── Convenience methods ──

    async def open_file(self, path: str) -> Dict[str, Any]:
        return await self.send_command("open_file", {"path": path})

    async def write_file(self, path: str, content: str) -> Dict[str, Any]:
        return await self.send_command("write_file", {"path": path, "content": content})

    async def read_file(self, path: str) -> Dict[str, Any]:
        return await self.send_command("read_file", {"path": path})

    async def terminal_run(self, command: str) -> Dict[str, Any]:
        return await self.send_command("terminal_run", {"command": command})

    async def navigate(self, folder: str) -> Dict[str, Any]:
        return await self.send_command("navigate", {"path": folder})

    async def list_files(self, path: Optional[str] = None) -> Dict[str, Any]:
        return await self.send_command("list_files", {"path": path or ""})

    async def get_status(self) -> Dict[str, Any]:
        if not self.connected:
            return {"success": False, "error": "IDE not connected", "connected": False}
        result = await self.send_command("get_status")
        result["connected"] = True
        return result

    async def show_diff(
        self, original: str, modified: str, filename: str, file_path: Optional[str] = None
    ) -> Dict[str, Any]:
        return await self.send_command("show_diff", {
            "original": original,
            "modified": modified,
            "filename": filename,
            "filePath": file_path,
        })

    # ── OS Control : état global ──

    async def get_state(self) -> Dict[str, Any]:
        """Retourne l'état complet de l'IDE (onglets ouverts, workspace, panels)."""
        if not self.connected:
            return {"success": False, "error": "IDE not connected", "connected": False}
        result = await self.send_command("get_state")
        result["connected"] = True
        return result

    # ── OS Control : éditeur ──

    async def editor_get_content(self) -> Dict[str, Any]:
        """Retourne le contenu de l'onglet actif dans Monaco."""
        return await self.send_command("editor_get_content")

    async def editor_switch_tab(self, path: Optional[str] = None, index: Optional[int] = None) -> Dict[str, Any]:
        """Change l'onglet actif (par chemin ou index)."""
        params: Dict[str, Any] = {}
        if path:
            params["path"] = path
        if index is not None:
            params["index"] = index
        return await self.send_command("editor_switch_tab", params)

    async def editor_close_tab(self, path: Optional[str] = None, index: Optional[int] = None) -> Dict[str, Any]:
        """Ferme un onglet (par chemin ou index)."""
        params: Dict[str, Any] = {}
        if path:
            params["path"] = path
        if index is not None:
            params["index"] = index
        return await self.send_command("editor_close_tab", params)

    async def editor_cursor_goto(self, line: int, col: int = 1) -> Dict[str, Any]:
        """Positionne le curseur Monaco à la ligne et colonne données."""
        return await self.send_command("editor_cursor_goto", {"line": line, "col": col})

    async def editor_select(self, start_line: int, end_line: int, start_col: int = 1, end_col: Optional[int] = None) -> Dict[str, Any]:
        """Sélectionne une plage de texte dans Monaco."""
        params: Dict[str, Any] = {"startLine": start_line, "startCol": start_col, "endLine": end_line}
        if end_col is not None:
            params["endCol"] = end_col
        return await self.send_command("editor_select", params)

    async def editor_insert(self, text: str, line: Optional[int] = None, col: Optional[int] = None) -> Dict[str, Any]:
        """Insère du texte à la position donnée (ou curseur actuel)."""
        params: Dict[str, Any] = {"text": text}
        if line is not None:
            params["line"] = line
        if col is not None:
            params["col"] = col
        return await self.send_command("editor_insert", params)

    async def editor_find_replace(self, find: str, replace: Optional[str] = None, all: bool = True) -> Dict[str, Any]:
        """Cherche (et remplace) du texte dans l'éditeur actif."""
        params: Dict[str, Any] = {"find": find, "all": all}
        if replace is not None:
            params["replace"] = replace
        return await self.send_command("editor_find_replace", params)

    async def editor_save(self) -> Dict[str, Any]:
        """Sauvegarde le fichier de l'onglet actif."""
        return await self.send_command("editor_save")

    # ── OS Control : terminal ──

    async def terminal_clear(self) -> Dict[str, Any]:
        """Efface l'output du terminal intégré."""
        return await self.send_command("terminal_clear")

    async def terminal_get_output(self) -> Dict[str, Any]:
        """Retourne l'output actuel du terminal intégré."""
        return await self.send_command("terminal_get_output")

    # ── OS Control : panels ──

    async def toggle_terminal(self, visible: Optional[bool] = None) -> Dict[str, Any]:
        """Affiche ou cache le panneau terminal."""
        params: Dict[str, Any] = {} if visible is None else {"visible": visible}
        return await self.send_command("toggle_terminal", params)

    async def toggle_search(self, visible: Optional[bool] = None) -> Dict[str, Any]:
        """Affiche ou cache le panneau de recherche."""
        params: Dict[str, Any] = {} if visible is None else {"visible": visible}
        return await self.send_command("toggle_search", params)

    async def toggle_sidebar(self, visible: Optional[bool] = None) -> Dict[str, Any]:
        """Affiche ou cache la sidebar (explorateur de fichiers)."""
        params: Dict[str, Any] = {} if visible is None else {"visible": visible}
        return await self.send_command("toggle_sidebar", params)

    async def toggle_chat(self, visible: Optional[bool] = None) -> Dict[str, Any]:
        """Affiche ou cache le panneau chat."""
        params: Dict[str, Any] = {} if visible is None else {"visible": visible}
        return await self.send_command("toggle_chat", params)

    # ── OS Control : sidebar / fichiers ──

    async def sidebar_create_file(self, path: str) -> Dict[str, Any]:
        """Crée un fichier vide et rafraîchit la sidebar."""
        return await self.send_command("sidebar_create_file", {"path": path})

    async def sidebar_create_folder(self, path: str) -> Dict[str, Any]:
        """Crée un dossier et rafraîchit la sidebar."""
        return await self.send_command("sidebar_create_folder", {"path": path})

    async def sidebar_delete(self, path: str) -> Dict[str, Any]:
        """Supprime un fichier/dossier et rafraîchit la sidebar."""
        return await self.send_command("sidebar_delete", {"path": path})

    async def sidebar_rename(self, old_path: str, new_path: str) -> Dict[str, Any]:
        """Renomme/déplace un fichier ou dossier."""
        return await self.send_command("sidebar_rename", {"oldPath": old_path, "newPath": new_path})

    # ── OS Control : recherche globale ──

    async def search_in_files(self, query: str, workspace: Optional[str] = None) -> Dict[str, Any]:
        """Recherche du texte dans tous les fichiers du workspace."""
        params: Dict[str, Any] = {"query": query}
        if workspace:
            params["workspace"] = workspace
        elif self._workspace:
            params["workspace"] = self._workspace
        return await self.send_command("search_in_files", params)

    # ── OS Control : fenêtre ──

    async def window_minimize(self) -> Dict[str, Any]:
        """Minimise la fenêtre de l'IDE."""
        return await self.send_command("window_minimize")

    async def window_maximize(self) -> Dict[str, Any]:
        """Maximise ou restaure la fenêtre de l'IDE."""
        return await self.send_command("window_maximize")

    async def window_close(self) -> Dict[str, Any]:
        """Ferme la fenêtre de l'IDE."""
        return await self.send_command("window_close")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
