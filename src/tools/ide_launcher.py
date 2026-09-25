"""Idempotent lifecycle owner for a discovered Lumena IDE runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import time
from typing import Awaitable, Callable, Optional, Protocol

from .ide_discovery import IDEDiscoveryService, IDEInstallation


class _Process(Protocol):
    def poll(self) -> Optional[int]: ...


@dataclass(frozen=True)
class IDEReadiness:
    """Observed bridge state; authentication remains distinct from transport."""

    transport_connected: bool = False
    handshake_received: bool = False
    authenticated: bool = False
    workspace: Optional[Path] = None


@dataclass(frozen=True)
class IDELaunchResult:
    state: str
    process_started: bool = False
    reused: bool = False
    transport_connected: bool = False
    handshake_received: bool = False
    authenticated: bool = False
    workspace: Optional[Path] = None
    installation: Optional[IDEInstallation] = None
    exit_code: Optional[int] = None
    error: str = ""

    @property
    def available(self) -> bool:
        """The requested launch/workspace completed with an observed handshake."""
        return (
            self.state in {"transport_ready", "authenticated_ready"}
            and self.transport_connected
            and self.handshake_received
        )

    @property
    def ready(self) -> bool:
        """Final trusted readiness is reached only after authentication."""
        return self.available and self.authenticated


ReadinessProbe = Callable[[], Awaitable[IDEReadiness]]
WorkspaceRouter = Callable[[Path], Awaitable[bool]]
# LOT L5-3c-2 : sonde SEPAREE — `_Probe` (doublure de 18 appels) declare
# `async def __call__(self)`, sans parametre. Changer `ReadinessProbe` la casserait.
WorkspaceProbe = Callable[[Path], Awaitable[IDEReadiness]]
ProcessStarter = Callable[[tuple[str, ...], Path, dict[str, str]], _Process]
Sleep = Callable[[float], Awaitable[None]]


async def _probe_bridge() -> IDEReadiness:
    from .ide_bridge import get_ide_bridge

    bridge = get_ide_bridge()
    if not bridge.connected:
        return IDEReadiness()
    status = await bridge.get_status()
    raw_workspace = status.get("workspace")
    workspace = Path(raw_workspace).resolve() if isinstance(raw_workspace, str) and raw_workspace else None
    return IDEReadiness(
        transport_connected=bridge.connected,
        handshake_received=status.get("success") is True,
        authenticated=bool(getattr(bridge, "authenticated", False)),
        workspace=workspace,
    )


async def _probe_bridge_workspace(workspace: Path) -> IDEReadiness:
    """LOT L5-3c-2 : l'etat de l'instance ouverte sur CE dossier, pas la proprietaire.

    `_probe_bridge` lit `get_ide_bridge()`, donc la connexion de l'utilisateur. En
    mode dedie, la boucle d'attente comparait ainsi le workspace de l'utilisateur au
    dossier de mission : `ensure_ready(dedicated=True)` ne pouvait finir qu'en
    `timeout`, meme avec une instance de mission vivante.

    Le chemin rendu est un `Path.resolve()` et NON la forme canonique : `_matches`
    compare a `_validate_workspace`, qui ne passe pas par `normcase`. La forme
    canonique sert a CHERCHER l'instance, jamais a repondre.

    `snapshot_pour_workspace` ne designe qu'une connexion validee par `_snapshot_de`
    (appairage courant, generation stable) : `transport_connected` et
    `authenticated` sont des faits observes, pas des suppositions.
    """
    from .ide_bridge import get_ide_bridge

    session = get_ide_bridge().snapshot_pour_workspace(str(workspace))
    if session is None:
        return IDEReadiness()
    annonce = getattr(session, "workspace_path", None)
    try:
        chemin = Path(annonce).resolve() if annonce else None
    except (OSError, RuntimeError, ValueError):
        chemin = None
    return IDEReadiness(
        transport_connected=True,
        handshake_received=getattr(session, "state", None) == "ready",
        authenticated=True,
        workspace=chemin,
    )


async def _route_bridge_workspace(workspace: Path) -> bool:
    from .ide_bridge import get_ide_bridge

    result = await get_ide_bridge().navigate(str(workspace))
    return result.get("success") is True


def _start_process(command: tuple[str, ...], cwd: Path, environment: dict[str, str]) -> _Process:
    kwargs: dict = {
        "cwd": str(cwd),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": environment,
    }
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
        if flags:
            kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(list(command), **kwargs)


class IDELauncherService:
    """Discover, reuse or launch one IDE and wait for observable readiness."""

    def __init__(
        self,
        *,
        discovery: IDEDiscoveryService,
        readiness_probe: ReadinessProbe = _probe_bridge,
        workspace_router: WorkspaceRouter = _route_bridge_workspace,
        workspace_probe: Optional[WorkspaceProbe] = None,
        process_starter: ProcessStarter = _start_process,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        timeout: float = 20.0,
        poll_interval: float = 0.1,
    ) -> None:
        self.discovery = discovery
        self.readiness_probe = readiness_probe
        self.workspace_router = workspace_router
        self.workspace_probe = workspace_probe or _probe_bridge_workspace
        self.process_starter = process_starter
        self.monotonic = monotonic
        self.sleep = sleep
        self.timeout = max(0.01, timeout)
        self.poll_interval = max(0.01, poll_interval)
        self._lock = asyncio.Lock()

    async def observe(self) -> IDEReadiness:
        """Return current bridge evidence without discovering or launching."""
        return await self.readiness_probe()

    async def ensure_ready(
        self,
        workspace: Optional[Path | str] = None,
        *,
        user_data_dir: Optional[Path | str] = None,
        dedicated: bool = False,
    ) -> IDELaunchResult:
        """LOT L5-3 : `user_data_dir` isole le profil de l'instance lancee.

        PREREQUIS de la voie A : `app.requestSingleInstanceLock()` (`main.ts`
        l.679-680) fait `app.quit()` quand le verrou est deja pris, et ce verrou est
        lie au repertoire `userData`. Sans repertoire distinct, une seconde instance
        meurt au demarrage.

        Le repertoire part par l'ENVIRONNEMENT, comme `LUMENA_IDE_WORKSPACE`, et non
        par la ligne de commande : `_command` rend `installation.command` BRUT en
        mode `source`, et deux tests figent la commande par egalite stricte
        (`test_conn1b_ide_launcher.py` l.121 et l.405). Parametre **nomme** et
        optionnel : les 18 appels existants de `ensure_ready` restent valides.

        LOT L5-3b-bis : `dedicated` demande une instance A SOI, jamais celle d'un
        autre. Sans lui, une IDE deja connectee sur un AUTRE dossier est simplement
        ROUTEE (`workspace_router` -> `navigate` sur la proprietaire) et aucun
        processus n'est lance : c'est le vol de fenetre que L5-2 interdit. Avec lui,
        le routage est saute et le lancement force. La reutilisation reste permise
        quand l'IDE connectee est DEJA sur le workspace demande : c'est alors
        l'instance de la mission elle-meme, pas celle de l'utilisateur.

        Defaut par defaut INCHANGE (`False`) : le chemin normal continue de router,
        ce que figent les gels de CONN-1B (l.218, l.238, l.257).
        """
        requested = self._validate_workspace(workspace)
        if workspace is not None and requested is None:
            return IDELaunchResult(
                state="invalid_workspace",
                error=f"Workspace IDE invalide: {workspace}",
            )

        async with self._lock:
            deadline = self.monotonic() + self.timeout
            try:
                initial = await asyncio.wait_for(self.readiness_probe(), timeout=self.timeout)
            except TimeoutError:
                return IDELaunchResult(state="timeout", error="Delai de reponse du bridge IDE depasse.")
            if self._matches(initial, requested):
                return self._observed_result(initial, state=self._state(initial), reused=True)

            # L5-3b-bis : en mode dedie on ne touche JAMAIS a la connexion existante.
            if (
                not dedicated
                and initial.transport_connected
                and initial.handshake_received
                and requested is not None
            ):
                try:
                    routed = await asyncio.wait_for(
                        self.workspace_router(requested),
                        timeout=max(0.0, deadline - self.monotonic()),
                    )
                except TimeoutError:
                    return self._observed_result(
                        initial, state="timeout", reused=True,
                        error="L'IDE n'a pas confirme le changement de workspace dans le delai.",
                    )
                if not routed:
                    return self._observed_result(
                        initial,
                        state="workspace_rejected",
                        reused=True,
                        error=f"L'IDE a refuse le workspace: {requested}",
                    )

            installation: Optional[IDEInstallation] = None
            process: Optional[_Process] = None
            process_started = False
            # L5-3b-bis : `dedicated` lance meme si une IDE est deja connectee -
            # c'est tout l'objet du mode : obtenir une instance SUPPLEMENTAIRE.
            if dedicated or not initial.transport_connected:
                report = self.discovery.discover()
                installation = report.installation
                if installation is None:
                    return IDELaunchResult(
                        state="unavailable",
                        workspace=requested,
                        error="Aucun runtime Lumena IDE valide et executable n'a ete trouve.",
                    )
                command = self._command(installation, requested)
                environment = os.environ.copy()
                if requested is not None:
                    environment["LUMENA_IDE_WORKSPACE"] = str(requested)
                if user_data_dir is not None:
                    # L5-3 : profil isole — Electron l'applique par `app.setPath`
                    # AVANT de prendre le verrou d'instance unique.
                    environment["LUMENA_IDE_USER_DATA"] = str(Path(user_data_dir).resolve())
                    if dedicated and requested is not None:
                        # LOT L5-4b : l'instance de mission n'a personne devant elle
                        # pour accorder la confiance, et son magasin est vierge
                        # puisque son profil est dedie. Mesure du canari reel :
                        # « workspace restricted: explicit trust required » sur toute
                        # ecriture, alors que l'instance etait ouverte et authentifiee.
                        # Cote IDE, `missionWorkspacePreTrusted` n'honore ceci que
                        # pour le dossier EXACT annonce ci-dessus et seulement avec un
                        # profil dedie : la fenetre de l'utilisateur, qui tourne sans
                        # ces variables, reste hors d'atteinte de ce chemin.
                        environment["LUMENA_IDE_TRUST_WORKSPACE"] = "1"
                else:
                    # Sans profil dedie, aucune pre-approbation : un environnement
                    # herite ne doit jamais faire confiance a la place de l'utilisateur.
                    environment.pop("LUMENA_IDE_TRUST_WORKSPACE", None)
                try:
                    process = self.process_starter(command, installation.root, environment)
                    process_started = True
                except Exception as exc:
                    return IDELaunchResult(
                        state="start_failed",
                        workspace=requested,
                        installation=installation,
                        error=f"Echec du lancement Lumena IDE: {exc}",
                    )

            last = initial
            # LOT L5-3c-2 : en mode dedie, observer la PROPRIETAIRE revient a
            # comparer le projet de l'utilisateur au dossier de mission - jamais
            # egaux, donc `timeout` garanti. On observe l'instance du dossier.
            observer = (
                (lambda: self.workspace_probe(requested))
                if dedicated and requested is not None
                else self.readiness_probe
            )
            while True:
                try:
                    last = await asyncio.wait_for(
                        observer(),
                        timeout=max(0.0, deadline - self.monotonic()),
                    )
                except TimeoutError:
                    return self._observed_result(
                        last, state="timeout", process_started=process_started,
                        reused=not process_started, installation=installation,
                        error="Lumena IDE n'a pas confirme son handshake avant le timeout.",
                    )
                if self._matches(last, requested):
                    return self._observed_result(
                        last,
                        state=self._state(last),
                        process_started=process_started,
                        reused=not process_started,
                        installation=installation,
                    )
                exit_code = process.poll() if process is not None else None
                if exit_code is not None:
                    return self._observed_result(
                        last,
                        state="crashed",
                        process_started=process_started,
                        installation=installation,
                        exit_code=exit_code,
                        error=f"Lumena IDE s'est arrete avant le handshake (code {exit_code}).",
                    )
                if self.monotonic() >= deadline:
                    return self._observed_result(
                        last,
                        state="timeout",
                        process_started=process_started,
                        reused=not process_started,
                        installation=installation,
                        error="Lumena IDE n'a pas confirme son handshake avant le timeout.",
                    )
                await self.sleep(self.poll_interval)

    @staticmethod
    def _validate_workspace(workspace: Optional[Path | str]) -> Optional[Path]:
        if workspace is None:
            return None
        raw = str(workspace).strip().strip("\"'")
        if not raw:
            return None
        candidate = Path(raw).expanduser().resolve()
        return candidate if candidate.is_dir() else None

    @staticmethod
    def _matches(readiness: IDEReadiness, requested: Optional[Path]) -> bool:
        if not (readiness.transport_connected and readiness.handshake_received):
            return False
        if requested is None:
            return True
        return readiness.workspace == requested

    @staticmethod
    def _state(readiness: IDEReadiness) -> str:
        return "authenticated_ready" if readiness.authenticated else "transport_ready"

    @staticmethod
    def _command(installation: IDEInstallation, workspace: Optional[Path]) -> tuple[str, ...]:
        if workspace is None or installation.mode == "source":
            return installation.command
        return (*installation.command, f"--workspace={workspace}")

    @staticmethod
    def _observed_result(
        readiness: IDEReadiness,
        *,
        state: str,
        process_started: bool = False,
        reused: bool = False,
        installation: Optional[IDEInstallation] = None,
        exit_code: Optional[int] = None,
        error: str = "",
    ) -> IDELaunchResult:
        return IDELaunchResult(
            state=state,
            process_started=process_started,
            reused=reused,
            transport_connected=readiness.transport_connected,
            handshake_received=readiness.handshake_received,
            authenticated=readiness.authenticated,
            workspace=readiness.workspace,
            installation=installation,
            exit_code=exit_code,
            error=error,
        )


_instance: Optional[IDELauncherService] = None


def get_ide_launcher() -> IDELauncherService:
    global _instance
    if _instance is None:
        lumena_root = Path(__file__).resolve().parents[2]
        _instance = IDELauncherService(discovery=IDEDiscoveryService(lumena_root))
    return _instance
