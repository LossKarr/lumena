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
import math
import os
import threading
import time
import uuid
from typing import Any, Dict, Optional

from .ide_paths import canonical_workspace
from .ide_pairing import PairingAuthority, PairingError, PairingSession
from .ide_pairing_store import PairingStore
from .ide_transport import IDELoopDispatcher, TransportBusy, TransportUnavailable
from .ide_command_protocol import BINDING_FIELDS, accept_result, command_frame, encode_frame
from .ide_protocol import (
    MAX_FRAME_BYTES, HEARTBEAT_INTERVAL_MS, HEARTBEAT_TIMEOUT_MS,
    NegotiatedSession, ProtocolError, negotiate, update_workspace,
)

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


class IDEConnection:
    """One authenticated IDE session: socket, negotiation and transport state.

    LOT L5-1a (16 septembre 2026) : cet etat quittait `IDEBridge` sans changer
    AUCUN comportement. Le pont en detient exactement une aujourd'hui ; L5-1b lui
    en fera tenir plusieurs (une IDE par mission, voie A).

    `__slots__` garantit l'invariant par construction : une connexion ne porte
    jamais ce qui reste global au pont (`_server`, `_pairing`, `_pairing_store`,
    `_dispatcher`). Les methodes `async` restent sur `IDEBridge` : le gel CONN-0A
    en compte exactement 41 et deplacer `unregister` le ferait tomber a 40.
    """

    __slots__ = ("_ws", "_pending", "_commands", "_sequence",
                 "_operation_counter", "_connected", "_session", "_negotiated")

    def __init__(self) -> None:
        self._ws = None  # websockets connection
        self._pending: Dict[str, asyncio.Future] = {}
        self._commands: Dict[str, dict] = {}
        self._sequence = 0
        self._operation_counter = 0
        self._connected = False
        self._session: Optional[PairingSession] = None
        self._negotiated: Optional[NegotiatedSession] = None


class IDEBridge:
    """Manages a single WebSocket connection to the Lumena IDE."""

    def __init__(self, *, pairing_store: Optional[PairingStore] = None) -> None:
        # LOT L5-1a : la connexion EN PREMIER — les proprietes d'etat ci-dessous
        # ecrivent dedans, elles ne peuvent pas preceder sa creation.
        self._connexion = IDEConnection()
        # LOT L5-1b : toutes les IDE authentifiees, indexees par `instance_id`
        # (porte par le hello, valide en 32 hex). `_connexion` designe la
        # PROPRIETAIRE — la fenetre de l'utilisateur — parmi celles-ci.
        self._connexions: Dict[str, IDEConnection] = {}
        self._workspace: Optional[str] = None
        self._server = None  # websockets.Server
        self._pairing_store = pairing_store
        self._pairing: Optional[PairingAuthority] = None
        self._protocol_error = ""
        self._protocol_error_state = "incompatible"
        self._dispatcher = IDELoopDispatcher()

    # ── LOT L5-1a : l'etat de session vit dans `_connexion` ──────────────────
    # Proprietes de compatibilite, deliberement explicites. Mesure du 16/09 :
    # ~90 occurrences dans les tests LISENT ces attributs sur le pont et 35 les
    # REASSIGNENT (`bridge._ws = object()`, `_negotiated` 11 fois, `_connected`
    # 11, `_pending` 7) ; aucune ne mute par index cote tests, mais `src/` le
    # fait (`self._pending[request_id] = fut`). Elles rendent donc l'objet REEL,
    # jamais une copie, et chacune a un setter.

    @property
    def _ws(self):
        return self._connexion._ws

    @_ws.setter
    def _ws(self, valeur) -> None:
        self._connexion._ws = valeur

    @property
    def _pending(self) -> Dict[str, asyncio.Future]:
        return self._connexion._pending

    @_pending.setter
    def _pending(self, valeur) -> None:
        self._connexion._pending = valeur

    @property
    def _commands(self) -> Dict[str, dict]:
        return self._connexion._commands

    @_commands.setter
    def _commands(self, valeur) -> None:
        self._connexion._commands = valeur

    @property
    def _sequence(self) -> int:
        return self._connexion._sequence

    @_sequence.setter
    def _sequence(self, valeur) -> None:
        self._connexion._sequence = valeur

    @property
    def _operation_counter(self) -> int:
        return self._connexion._operation_counter

    @_operation_counter.setter
    def _operation_counter(self, valeur) -> None:
        self._connexion._operation_counter = valeur

    @property
    def _connected(self) -> bool:
        return self._connexion._connected

    @_connected.setter
    def _connected(self, valeur) -> None:
        self._connexion._connected = valeur

    @property
    def _session(self) -> Optional[PairingSession]:
        return self._connexion._session

    @_session.setter
    def _session(self, valeur) -> None:
        self._connexion._session = valeur

    @property
    def _negotiated(self) -> Optional[NegotiatedSession]:
        return self._connexion._negotiated

    @_negotiated.setter
    def _negotiated(self, valeur) -> None:
        self._connexion._negotiated = valeur

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def workspace(self) -> Optional[str]:
        return self._workspace

    @property
    def ready(self) -> bool:
        return self.authenticated and self._negotiated is not None and self._negotiated.state == "ready"

    def catalogue_snapshot(self) -> Optional[NegotiatedSession]:
        """Capture one authenticated revision, including across agent threads.

        This is discovery, not an execution grant. A caller retaining this
        object must also supply it to send_command for the owner-loop check.
        """
        # LOT L5-1c : decrit la PROPRIETAIRE — comportement public inchange.
        return self._snapshot_de(self._connexion)

    def snapshot_pour_workspace(self, workspace: Any) -> Optional[NegotiatedSession]:
        """L'instance ouverte sur CE dossier, validee, ou None.

        LOT L5-3c-1 : le maillon qui manquait a la voie A. L5-1c a prouve qu'une
        commande part sur SA connexion **quand on lui fournit le snapshot** ; rien
        ne savait dire lequel. Sans cette designation, `_probe_bridge` et
        `IDECapabilityService` ne voient que la PROPRIETAIRE, donc la fenetre de
        l'utilisateur, et une mission ne peut jamais atteindre son instance.

        Aucune E/S reseau : `workspace_path` est porte par `NegotiatedSession`
        (renseigne a la negociation, tenu a jour par `update_workspace`). Designer
        une instance ne coute donc aucune commande et ne peut pas echouer sur une
        IDE occupee.

        SYNCHRONE : le gel CONN-0A compte 41 methodes `async` sur cette classe.

        La PROPRIETAIRE est consultee AVANT le registre, comme
        `_connexion_pour_snapshot` : le gel `test_conn3c_ide_snapshot_binding`
        ecrit l'etat directement et laisse `_connexions` vide.

        Chaque candidate passe par `_snapshot_de` : une connexion tombee, non
        appairee ou dont la generation a change n'est jamais designee. C'est la
        validation qui existe deja, pas une seconde regle plus laxiste.
        """
        cible = canonical_workspace(workspace)
        if cible is None:
            return None
        for connexion in (self._connexion, *self._connexions.values()):
            session = connexion._negotiated
            if session is None or canonical_workspace(session.workspace_path) != cible:
                continue
            # `is` et non `==` : le scenario « forged » du gel conn3c fabrique un
            # objet EGAL mais distinct et exige le refus.
            if self._snapshot_de(connexion) is session:
                return session
        return None

    @property
    def protocol_status(self) -> Dict[str, Any]:
        session = self._negotiated
        if session is not None and self.authenticated:
            return {"state": session.state, "protocol": session.protocol, "ide_version": session.ide_version,
                    "instance_id": session.instance_id, "build_hash": session.build_hash,
                    "catalogue_hash": session.catalogue_hash, "workspace_id": session.workspace_id}
        return {"state": self._protocol_error_state if self._protocol_error else "disconnected",
                "reason": self._protocol_error}

    @property
    def authenticated(self) -> bool:
        if not self.connected or self._pairing is None or self._session is None:
            return False
        try:
            return self._pairing.is_current(self._session)
        except PairingError:
            return False

    # ── Standalone WebSocket server on port 8245 ──

    async def start_server(self) -> None:
        """Start the standalone WebSocket server on IDE_WS_PORT."""
        if self._server is not None:
            return
        if self._pairing_store is None:
            self._pairing_store = PairingStore()
        await asyncio.to_thread(self._pairing_store.initialize)
        self._pairing = PairingAuthority(self._pairing_store.load)
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
            max_size=MAX_FRAME_BYTES,
            ping_interval=HEARTBEAT_INTERVAL_MS / 1000,
            ping_timeout=HEARTBEAT_TIMEOUT_MS / 1000,
        )
        self._dispatcher.bind()
        logger.info(f"[IDE-Bridge] WebSocket server listening on ws://127.0.0.1:{IDE_WS_PORT}")

    async def stop_server(self) -> None:
        """Stop the standalone WebSocket server."""
        await self._dispatcher.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("[IDE-Bridge] WebSocket server stopped")

    async def _connection_handler(self, websocket: Any) -> None:
        """Handle an incoming IDE WebSocket connection."""
        if not await self.register(websocket):
            return
        try:
            async for raw in websocket:
                connexion = self._connexion_de(websocket)
                if connexion is None or not self._est_courante(connexion):
                    await websocket.close(code=4003, reason="pairing_revoked")
                    break
                await self.handle_message(raw, connexion=connexion)
        except ProtocolError as exc:
            connexion = self._connexion_de(websocket)
            if connexion is not None:
                self._protocol_error = str(exc)
                self._protocol_error_state = "degraded"
                await self.unregister(connexion=connexion)
            await websocket.close(code=4006, reason=str(exc))
        except Exception:
            logger.debug("IDE WS loop ended")
        finally:
            connexion = self._connexion_de(websocket)
            if connexion is not None:
                await self.unregister(connexion=connexion)

    # LOT L5-1b : deux aides SYNCHRONES — le gel CONN-0A compte les methodes
    # `async` de `IDEBridge` (41 exactement) et ne doit pas bouger.

    def _connexion_de(self, websocket: Any) -> Optional["IDEConnection"]:
        """La connexion qui detient ce socket, ou None s'il n'est plus enregistre."""
        if self._connexion._ws is websocket:
            return self._connexion
        for connexion in self._connexions.values():
            if connexion._ws is websocket:
                return connexion
        return None

    def _snapshot_de(self, connexion: "IDEConnection") -> Optional[NegotiatedSession]:
        """`catalogue_snapshot`, mais pour UNE connexion donnee.

        LOT L5-1c : logique deplacee telle quelle depuis `catalogue_snapshot`, y
        compris la REVERIFICATION apres l'I/O d'appairage (six scenarios du gel
        `test_conn3c_ide_snapshot_binding` en dependent).
        """
        websocket, pairing = connexion._ws, self._pairing
        session, negotiated = connexion._session, connexion._negotiated
        if (not connexion._connected or websocket is None or pairing is None or session is None
                or negotiated is None or negotiated.state != "ready"
                or negotiated.session_id != session.session_id):
            return None
        try:
            current = pairing.is_current(session)
        except PairingError:
            return None
        # Pairing-store I/O may yield to another thread changing the connection.
        if (not current or not connexion._connected or connexion._ws is not websocket
                or self._pairing is not pairing or connexion._session is not session
                or connexion._negotiated is not negotiated):
            return None
        return negotiated

    def _connexion_pour_snapshot(self, snapshot: Any) -> Optional["IDEConnection"]:
        """La connexion que DECRIT ce snapshot, validee, ou None.

        LOT L5-1c : l'instance visee est deja portee par l'appel (le rail passe
        `expected_snapshot` a chaque commande) — il n'y a donc aucun parametre a
        ajouter. La comparaison reste une IDENTITE : le scenario « forged » du gel
        fabrique un objet EGAL mais distinct et exige le refus.
        La PROPRIETAIRE est consultee AVANT le registre : `unit_bridge` (gel
        conn3c) ecrit l'etat directement et laisse `_connexions` vide.
        """
        for connexion in (self._connexion, *self._connexions.values()):
            if connexion._negotiated is snapshot and self._snapshot_de(connexion) is snapshot:
                return connexion
        return None

    def _est_courante(self, connexion: "IDEConnection") -> bool:
        """`authenticated`, mais pour UNE connexion donnee et non la proprietaire."""
        if connexion._ws is None or not connexion._connected or connexion._session is None:
            return False
        if self._pairing is None:
            return False
        try:
            return self._pairing.is_current(connexion._session)
        except PairingError:
            return False

    async def register(self, websocket: Any) -> bool:
        """Authenticate both peers before replacing an existing connection."""
        challenge = None
        try:
            if self._pairing is None:
                raise PairingError("pairing_unavailable")
            remote = getattr(websocket, "remote_address", None)
            host = remote[0] if isinstance(remote, tuple) and remote else ""
            if not host:
                host = getattr(getattr(websocket, "client", None), "host", "")
            challenge = self._pairing.begin(host)
            send = getattr(websocket, "send", None) or getattr(websocket, "send_text", None)
            receive = getattr(websocket, "recv", None) or getattr(websocket, "receive_text", None)
            await send(json.dumps(challenge))
            raw = await asyncio.wait_for(receive(), timeout=15)
            session, acknowledgement = self._pairing.finish(challenge["session_id"], json.loads(raw))
            await send(json.dumps(acknowledgement))
        except Exception:
            logger.info("[IDE-Bridge] Pairing rejected")
            await websocket.close(code=4003, reason="pairing_rejected")
            return False
        finally:
            if challenge is not None and self._pairing is not None:
                self._pairing.discard(challenge["session_id"])

        # Authenticate and negotiate fully before evicting any healthy peer.
        try:
            raw = await asyncio.wait_for(receive(), timeout=15)
            if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_FRAME_BYTES:
                raise ProtocolError("frame_too_large")
            negotiated, acknowledgement = negotiate(json.loads(raw), session.session_id)
            if not self._pairing.is_current(session):
                raise ProtocolError("pairing_revoked")
            await send(json.dumps(acknowledgement))
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ProtocolError) else "negotiation_rejected"
            if not self.connected:
                self._protocol_error = reason
                self._protocol_error_state = "incompatible"
            await websocket.close(code=4004, reason=reason)
            return False

        # LOT L5-1b : un pair VALIDE ne chasse plus le precedent. Mesure qui
        # l'autorise : `PairingAuthority.is_current` juge la CLE d'appairage, pas
        # l'unicite de la session, et `begin()` gere un dictionnaire de challenges
        # (max_pending=64) — deux sessions de la meme cle sont TOUTES DEUX
        # courantes. Les deux gels existants ne protegent que du pair *invalide*
        # ou *incompatible*, jamais d'un pair valide.
        connexion = IDEConnection()
        connexion._ws = websocket
        connexion._connected = True
        connexion._session = session
        connexion._negotiated = negotiated
        connexion._sequence = 0
        connexion._operation_counter = negotiated.operation_cursor

        ancienne = self._connexions.get(negotiated.instance_id)
        self._connexions[negotiated.instance_id] = connexion
        if self._connexion._ws is None:
            # Aucune proprietaire : cette IDE prend la main. Les suivantes non —
            # la fenetre de l'utilisateur ne bascule jamais sur une mission.
            self._connexion = connexion
            self._workspace = negotiated.workspace_path
        self._protocol_error = ""
        if ancienne is not None and ancienne._ws is not None and ancienne._ws is not websocket:
            # MEME instance qui se reconnecte : on ferme SON ancien socket,
            # jamais celui d'une autre instance.
            if ancienne is self._connexion:
                self._connexion = connexion
                self._workspace = negotiated.workspace_path
            await ancienne._ws.close()
        logger.info("[IDE-Bridge] IDE authenticated")
        return True

    async def unregister(self, *, connexion: Optional["IDEConnection"] = None) -> None:
        """Unregister the IDE connection.

        LOT L5-1b : `connexion` designe celle a retirer ; par defaut la
        proprietaire, soit exactement le comportement d'avant. Parametre NOMME et
        optionnel — un positionnel casserait les doublures (regle CONN-5B-2) — et
        aucune methode `async` n'est ajoutee (gel CONN-0A : 41).
        """
        cible = connexion if connexion is not None else self._connexion
        instance = cible._negotiated.instance_id if cible._negotiated is not None else None
        if instance is not None and self._connexions.get(instance) is cible:
            self._connexions.pop(instance, None)
        cible._ws = None
        cible._connected = False
        cible._session = None
        cible._negotiated = None
        for rid, fut in list(cible._pending.items()):
            if not fut.done():
                fut.set_exception(ConnectionError("IDE disconnected"))
        cible._pending.clear()
        cible._commands.clear()
        if cible is self._connexion:
            self._workspace = None
        logger.info("[IDE-Bridge] IDE disconnected")

    async def revoke_pairing(self) -> None:
        if self._pairing_store is None:
            raise PairingError("pairing_unavailable")
        await asyncio.to_thread(self._pairing_store.revoke)
        websocket = self._ws
        await self.unregister()
        if websocket is not None:
            await websocket.close(code=4003, reason="pairing_revoked")

    async def rotate_pairing(self) -> None:
        if self._pairing_store is None:
            raise PairingError("pairing_unavailable")
        await asyncio.to_thread(self._pairing_store.rotate)
        websocket = self._ws
        await self.unregister()
        if websocket is not None:
            await websocket.close(code=4003, reason="pairing_rotated")

    async def handle_message(self, raw: str, *, connexion: Optional["IDEConnection"] = None) -> None:
        """Process an incoming message from the IDE.

        LOT L5-1c : `connexion` designe l'EMETTRICE ; par defaut la proprietaire,
        soit exactement le comportement d'avant (la doublure du gel
        `test_conn3c_ide_snapshot_binding` appelle sans connexion). Parametre
        NOMME — un positionnel casserait les doublures (regle CONN-5B-2) — et
        aucune methode `async` n'est ajoutee (gel CONN-0A : 41).
        """
        cible = connexion if connexion is not None else self._connexion
        if not self._est_courante(cible):
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if not isinstance(msg, dict):
            return
        msg_type = msg.get("type", "")

        if msg_type == "ide_workspace" and cible._negotiated is not None:
            cible._negotiated = update_workspace(msg, cible._negotiated)
            if cible is self._connexion:
                # Seule la proprietaire decrit la fenetre de l'utilisateur : une
                # IDE de mission ne deplace jamais son workspace affiche.
                self._workspace = cible._negotiated.workspace_path
            return

        if msg_type in ("ide_connected", "ide_hello", "auth_response"):
            raise ProtocolError("unexpected_frame")

        if msg_type == "pong":
            return

        if msg_type == "result":
            request_id = msg.get("request_id")
            # LOT L5-1c : resolu dans le `_pending` de l'EMETTRICE. Un resultat
            # d'IDE de mission ne reveille jamais la commande de l'utilisateur.
            if isinstance(request_id, str) and request_id in cible._pending:
                result = accept_result(msg, cible._commands[request_id])
                fut = cible._pending.pop(request_id)
                if not fut.done():
                    fut.set_result(result)
            return

    # ── Public API for Lumena handlers ──

    async def send_command(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
        *, resume_transport: Optional[Dict[str, Any]] = None,
        expected_snapshot: Optional[NegotiatedSession] = None,
        mission_scope: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Dispatch on the server loop, even when invoked by an agent thread."""
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout):
            return {"success": False, "error": "Invalid IDE command timeout"}
        if timeout <= 0 or timeout > 300:
            return {"success": False, "error": "Invalid IDE command timeout"}
        try:
            # Freeze caller-owned parameters before crossing the thread boundary.
            encoded_params = json.dumps(params if params is not None else {}, allow_nan=False)
            if len(encoded_params.encode("utf-8")) > MAX_FRAME_BYTES:
                return {"success": False, "error": "IDE command exceeds frame limit"}
            params_snapshot = json.loads(encoded_params)
        except (TypeError, ValueError, RecursionError):
            return {"success": False, "error": "Invalid IDE command parameters"}
        if not isinstance(params_snapshot, dict):
            return {"success": False, "error": "Invalid IDE command parameters"}
        scope_snapshot = None
        if mission_scope is not None:
            # CONN-5B-2 : figer le perimetre avant de traverser la frontiere de thread.
            try:
                scope_snapshot = json.loads(json.dumps(mission_scope, allow_nan=False))
            except (TypeError, ValueError, RecursionError):
                return {"success": False, "error": "Invalid IDE mission scope", "outcome": "not_sent"}
        resume = None
        if resume_transport is not None:
            try:
                resume = json.loads(encode_frame(resume_transport))
                if type(resume) is not dict or set(resume) != set(BINDING_FIELDS):
                    raise ProtocolError("resume_invalid")
            except (ProtocolError, TypeError, ValueError, RecursionError):
                return {"success": False, "error": "Invalid IDE resume reference", "outcome": "not_sent"}
        # LOT L5-1c : le `session_id` doit decrire l'instance VISEE. Sans
        # snapshot, c'est la proprietaire — comportement d'avant, inchange ; et
        # un snapshot qui decrit la proprietaire donne le meme `session_id`.
        session = expected_snapshot if expected_snapshot is not None else self._negotiated
        session_id = session.session_id if session is not None else None
        expires_at = int(time.time() * 1000) + max(1, int(timeout * 1000))
        attempt: dict = {}
        try:
            return await self._dispatcher.dispatch(
                lambda: self._send_command_on_owner(
                    action, params_snapshot, session_id, expires_at, resume, attempt, expected_snapshot,
                    # CONN-5B-2 : nomme et seulement si present, pour qu'une commande ordinaire
                    # garde exactement la forme d'appel d'avant (doublures de test comprises).
                    **({"mission_scope": scope_snapshot} if scope_snapshot is not None else {})),
                timeout,
            )
        except TimeoutError:
            return {"success": False, "error": "IDE command timed out", "outcome": "unknown", **attempt}
        except (TransportUnavailable, TransportBusy) as exc:
            return {"success": False, "error": str(exc), "outcome": getattr(exc, "outcome", "not_sent"), **attempt}

    async def _send_command_on_owner(
        self, action: str, params: Dict[str, Any], session_id: Optional[str], expires_at: int,
        resume: Optional[dict] = None, attempt: Optional[dict] = None,
        expected_snapshot: Optional[NegotiatedSession] = None,
        mission_scope: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """The socket and pending futures are exclusively owned by this loop."""
        # LOT L5-1c : le snapshot DESIGNE la connexion visee. Sans lui, la
        # proprietaire — le defaut d'avant. La resolution echoue AVANT toute
        # allocation : un refus « stale » ne consomme ni sequence ni compteur
        # d'operation (gel `test_owner_rejects_stale_snapshot`).
        if expected_snapshot is not None:
            if type(expected_snapshot) is not NegotiatedSession:
                return {"success": False, "error": "IDE catalogue snapshot stale", "outcome": "not_sent"}
            connexion = self._connexion_pour_snapshot(expected_snapshot)
            if connexion is None:
                return {"success": False, "error": "IDE catalogue snapshot stale", "outcome": "not_sent"}
        else:
            connexion = self._connexion
            if not self.authenticated:
                return {"success": False, "error": "IDE not authenticated"}
            if not self.ready:
                return {"success": False, "error": "IDE protocol not ready"}
        if connexion._negotiated.session_id != session_id:
            return {"success": False, "error": "IDE session changed", "outcome": "not_sent"}
        if not any(command["id"] == action and command["supported"] for command in connexion._negotiated.commands):
            return {"success": False, "error": "IDE command not negotiated"}
        if resume is not None and any(resume.get(key) != expected for key, expected in (
            ("instance_id", connexion._negotiated.instance_id),
            ("catalogue_revision", connexion._negotiated.catalogue_hash),
            ("workspace_id", connexion._negotiated.workspace_id),
        )):
            return {"success": False, "error": "IDE resume scope changed", "outcome": "unknown"}

        request_id = uuid.uuid4().hex
        try:
            command = command_frame(connexion._negotiated, action, params, request_id=request_id,
                                    operation_id=resume["operation_id"] if resume is not None else (
                                        connexion._negotiated.instance_id[:16]
                                        + f"{connexion._operation_counter + 1:016x}"),
                                    sequence=connexion._sequence + 1, expires_at=expires_at,
                                    mode="resume" if resume is not None else "execute",
                                    mission_scope=mission_scope)
            if resume is None and connexion._operation_counter >= 9_007_199_254_740_991:
                raise ProtocolError("operation_exhausted")
            if resume is not None and (
                command["operation_id"][:16] != connexion._negotiated.instance_id[:16]
                or not 1 <= int(command["operation_id"][16:], 16) <= connexion._operation_counter
            ):
                return {"success": False, "error": "Unknown IDE resume reference", "outcome": "unknown"}
            payload = encode_frame(command)
        except ProtocolError as exc:
            error = ("IDE command exceeds frame limit" if str(exc) == "frame_too_large"
                     else "IDE mission scope unsupported" if str(exc) == "mission_scope_unsupported"
                     else "Invalid IDE command envelope")
            return {"success": False, "error": error, "outcome": "not_sent"}

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        # LOT L5-1c : la future attend dans le `_pending` de SA connexion, jamais
        # dans un dictionnaire commun — sinon le resultat d'une IDE de mission
        # reveillerait la commande de l'utilisateur.
        connexion._pending[request_id] = fut
        connexion._commands[request_id] = command
        connexion._sequence = command["sequence"]
        if resume is None:
            connexion._operation_counter += 1
        provenance = {"_transport": {key: command[key] for key in BINDING_FIELDS}}
        if attempt is not None:
            attempt.update(provenance)

        try:
            # Support both websockets (send) and FastAPI WebSocket (send_text)
            send = getattr(connexion._ws, "send", None) or getattr(connexion._ws, "send_text", None)
            if send is None:
                raise RuntimeError("WebSocket has no send method")
            await send(payload)
            result = await fut
            return result
        except Exception as e:
            return {"success": False, "error": str(e), "outcome": "unknown", **provenance}
        finally:
            connexion._pending.pop(request_id, None)
            connexion._commands.pop(request_id, None)
            if not fut.done():
                fut.cancel()

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
            return {"success": False, "error": "IDE not connected", "connected": False,
                    "protocol_status": self.protocol_status}
        result = await self.send_command("get_status")
        result["connected"] = self.ready
        result["protocol_status"] = self.protocol_status
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
            return {"success": False, "error": "IDE not connected", "connected": False,
                    "protocol_status": self.protocol_status}
        result = await self.send_command("get_state")
        result["connected"] = self.ready
        result["protocol_status"] = self.protocol_status
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
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
