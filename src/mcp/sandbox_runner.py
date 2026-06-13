"""
sandbox_runner.py — Lifecycle isolé d'un serveur MCP (Phase 5).

Scope strict :
  - Install isolée (npm / uv venv) dans `~/.lumena/mcp/<name>/`
  - Env install minimal (aucun secret, allowlist système strictement nécessaire)
  - Env runtime via allowlist (`env_keys_allowlist` + valeurs de SecretsService)
  - Process lifecycle : start / stop (SIGTERM → SIGKILL) / restart / state
  - Logs rolling deque
  - Quarantine sur crashes répétés
  - Lock install (sérialisation concurrents)

Hors scope Phase 5 (cf REPO/PLAN_MCP_LUMENA.md v4.1) :
  - Communication MCP JSON-RPC (Phase 7)
  - Adapter MCPTool → HandlerDef (Phase 7)
  - Catalogue UI (Phase 13)
  - Healthcheck protocole MCP (placeholder is_alive() = process-level)
  - Network policy OS-level (Docker / Job Object) — limites assumées §9 threat model
  - Resource limits cgroups/Job Object — API placeholder seulement

Limites documentées :
  - Windows sans Docker : isolation best-effort applicative, pas OS-level
    (cf REPO/THREAT_MODEL_MCP.md §9)
  - Windows : SIGTERM mappe sur terminate() ≈ SIGKILL (pas de signal gracieux natif)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field, replace as _dc_replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Deque, Dict, List, Literal, Optional

from filelock import FileLock, Timeout
from loguru import logger

from src.utils.paths import DATA_DIR

# ──────────────────────────────────────────────────────────────────────────────
# Constantes et configuration
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_MCP_ROOT = Path(
    os.environ.get("LUMENA_MCP_ROOT", str(Path.home() / ".lumena" / "mcp"))
)
_DEFAULT_LOGS_DIR = DATA_DIR / "mcp_logs"
_INSTALL_LOCK_TIMEOUT_S = 60.0
_DEFAULT_MAX_LOG_LINES = 1000
_DEFAULT_STARTUP_TIMEOUT_S = 30.0
_DEFAULT_STOP_TIMEOUT_S = 5.0
_QUARANTINE_CRASH_WINDOW_S = 300.0  # 5 minutes
_QUARANTINE_CRASH_THRESHOLD = 3

# Phase I-8 (Fix AX) : détection réactive d'une exigence de version Python.
# Quand `uv pip install` refuse un package parce qu'il requiert un Python plus
# récent que celui du venv isolé, le stderr contient un message du type :
#   "Because <pkg> requires Python>=3.13 and the current Python version is 3.12.10"
# On extrait la version exacte (X.Y) pour recréer le venv isolé avec ce Python
# managé par uv — SANS jamais toucher le venv de Lumena (venv 100% confiné dans
# data/mcp_install/servers/<sid>/.venv, UV_CACHE_DIR redirigé sur le serveur).
_PY_REQUIRES_RE = re.compile(r"[Pp]ython\s*>=\s*(\d+\.\d+)")

# Env variables système autorisées par OS (aucun secret).
# Sur Windows, npm/uv ont besoin d'un minimum pour résoudre node.exe / py,
# trouver TEMP, etc.
_POSIX_ENV_ALLOWLIST = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LANGUAGE")
_WINDOWS_ENV_ALLOWLIST = (
    "USERPROFILE",
    "PATH",
    "TEMP",
    "TMP",
    "APPDATA",
    "LOCALAPPDATA",
    "SYSTEMROOT",
    "WINDIR",
    "PATHEXT",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "COMSPEC",
)


def _system_env_allowlist() -> tuple:
    """Retourne l'allowlist env adaptée à l'OS courant."""
    if sys.platform == "win32":
        return _WINDOWS_ENV_ALLOWLIST
    return _POSIX_ENV_ALLOWLIST


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────


class MCPSandboxError(Exception):
    """Erreur générique du runner sandbox MCP."""


# ──────────────────────────────────────────────────────────────────────────────
# Enums et dataclasses
# ──────────────────────────────────────────────────────────────────────────────


class ProcessState(Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class MCPInstallSpec:
    """Spécification d'installation d'un serveur MCP.

    Attributes:
        name: identifiant unique (slug ASCII), utilisé comme nom de dossier
        transport: "npm" ou "uv" (Python via venv isolé)
        package: nom du package (`@modelcontextprotocol/server-postgres`,
                 `mcp-server-filesystem`, ou git+url)
        args: arguments passés au binaire MCP au start
        env_keys_allowlist: noms de clés env autorisées à être injectées au
                            runtime (les valeurs viennent de SecretsService,
                            jamais en clair ici)
        package_version: pin version recommandée (latest si None)
        require_wheels_only: pour transport=uv, refuse build natif depuis source
        lock_file: pour transport=uv, si fourni → uv pip install --require-hashes
        trust_score: informatif, set par Phase 6 (0 = inconnu)
        entry_args: Phase I-8 (Fix AY) — arguments ajoutés APRÈS le binaire
                    RÉSOLU (entry point console / .bin npm). Distinct de
                    `args` qui remplace la commande entière. Cas d'usage :
                    entry point = CLI à sous-commandes (ex. windows-mcp →
                    `windows-mcp.exe serve`), découvert réactivement par
                    l'activation puis persisté au catalogue.
    """

    name: str
    transport: Literal["npm", "uv"]
    package: str
    args: List[str] = field(default_factory=list)
    env_keys_allowlist: List[str] = field(default_factory=list)
    package_version: Optional[str] = None
    require_wheels_only: bool = True
    lock_file: Optional[Path] = None
    trust_score: int = 0
    entry_args: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MCPLifecycleEvent:
    """Trace d'un événement lifecycle (jamais de valeur secrète)."""

    ts: str
    server_name: str
    event_type: Literal[
        "install", "uninstall", "start", "stop", "crash", "quarantine", "restart"
    ]
    detail: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ──────────────────────────────────────────────────────────────────────────────


def _validate_name(name: str) -> None:
    """Garde-fou nom de serveur : ASCII slug, pas de path traversal."""
    if not name or not isinstance(name, str):
        raise MCPSandboxError(f"Invalid MCP server name: {name!r}")
    if len(name) > 64:
        raise MCPSandboxError(f"MCP server name too long (>64): {name!r}")
    if not all(c.isalnum() or c in "_-." for c in name):
        raise MCPSandboxError(
            f"MCP server name must be alphanumeric or _-.: {name!r}"
        )
    if name.startswith("."):
        raise MCPSandboxError(f"MCP server name cannot start with '.': {name!r}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_cli_binary(name: str) -> str:
    """Résout un binaire CLI vers son chemin absolu exécutable par subprocess.

    Phase I-7 : sur Windows, npm/yarn/pnpm sont installés comme `.cmd` ou
    `.ps1`, jamais en `.exe`. `subprocess.run(["npm", ...])` sans `shell=True`
    cherche `npm.exe` et lève `FileNotFoundError` même si `npm.cmd` est dans
    PATH (cause de `runner_install_failed` observé en prod).

    Stratégie : sur Windows on cherche explicitement `.cmd`/`.bat`/`.exe`
    (que subprocess peut lancer sans shell) AVANT le fallback `shutil.which`.
    Les `.ps1` sont volontairement ignorés (subprocess ne peut pas les
    exécuter sans `powershell.exe -File ...`).
    """
    if sys.platform == "win32":
        for ext in (".cmd", ".bat", ".exe"):
            candidate = shutil.which(name + ext)
            if candidate:
                return candidate
    resolved = shutil.which(name)
    if resolved is not None:
        return resolved
    # Fallback : laisse subprocess lever FileNotFoundError downstream
    # avec un message clair (mieux que None).
    return name


# ──────────────────────────────────────────────────────────────────────────────
# Sandbox runner
# ──────────────────────────────────────────────────────────────────────────────


class MCPSandboxRunner:
    """Gère install + lifecycle d'UN serveur MCP isolé.

    Pattern d'usage :
        spec = MCPInstallSpec(
            name="brave-search",
            transport="npm",
            package="@modelcontextprotocol/server-brave-search",
            env_keys_allowlist=["BRAVE_API_KEY"],
        )
        runner = MCPSandboxRunner(spec)
        runner.install()
        runner.start(runtime_env_secrets={"BRAVE_API_KEY": "bsa_xxx"})
        # ... usage ...
        runner.stop()

    Limites Phase 5 (cf docstring module).
    """

    def __init__(
        self,
        spec: MCPInstallSpec,
        mcp_root: Optional[Path] = None,
        logs_dir: Optional[Path] = None,
        max_log_lines: int = _DEFAULT_MAX_LOG_LINES,
        startup_timeout_s: float = _DEFAULT_STARTUP_TIMEOUT_S,
        stop_timeout_s: float = _DEFAULT_STOP_TIMEOUT_S,
        stdout_mode: str = "client",
    ):
        _validate_name(spec.name)
        if spec.transport not in ("npm", "uv"):
            raise MCPSandboxError(
                f"Unknown transport: {spec.transport!r} (expected 'npm' or 'uv')"
            )
        # Phase 5.1 v2 : stdout_mode décide qui possède stdout.
        #   "client"  (défaut) : stdout APPARTIENT au MCPClient.
        #                        Le runner NE LIT JAMAIS stdout.
        #                        Crash detection via _process_watcher_loop
        #                        (proc.wait(), aucun readline).
        #   "capture" : runner lit stdout (mode rétrocompat tests/logs legacy).
        if stdout_mode not in ("client", "capture"):
            raise MCPSandboxError(
                f"Invalid stdout_mode {stdout_mode!r}: "
                "expected 'client' (default, MCP usage) or 'capture' (legacy)."
            )
        self.spec = spec
        self._mcp_root = mcp_root or _DEFAULT_MCP_ROOT
        self._logs_dir = logs_dir or _DEFAULT_LOGS_DIR
        self._max_log_lines = max_log_lines
        self._startup_timeout_s = startup_timeout_s
        self._stop_timeout_s = stop_timeout_s
        self._stdout_mode = stdout_mode

        # State
        self._state: ProcessState = ProcessState.NOT_INSTALLED
        self._process: Optional[subprocess.Popen] = None
        # Threads :
        #   - _monitor_thread : stdout reader (mode "capture" uniquement)
        #   - _watcher_thread : process watcher (mode "client" uniquement)
        #   - _stderr_thread : stderr reader (toujours actif)
        self._monitor_thread: Optional[threading.Thread] = None
        self._watcher_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stdout_buffer: Deque[str] = deque(maxlen=max_log_lines)
        self._stderr_buffer: Deque[str] = deque(maxlen=max_log_lines)
        self._state_lock = threading.RLock()
        self._crash_timestamps: List[float] = []
        self._quarantined: bool = False
        self._scrub_values: List[str] = []  # valeurs secrètes runtime à scrubber

        self._server_dir = self._mcp_root / self.spec.name
        self._cache_dir = self._server_dir / ".cache"
        self._install_lock_path = self._mcp_root / f"{self.spec.name}.install.lock"

        # Sync state initial selon présence de l'install
        if self._is_install_complete():
            self._state = ProcessState.INSTALLED

    # ── Paths ────────────────────────────────────────────────────────────

    @property
    def server_dir(self) -> Path:
        return self._server_dir

    @property
    def venv_python(self) -> Path:
        """Chemin du Python du venv isolé (transport=uv)."""
        if sys.platform == "win32":
            return self._server_dir / ".venv" / "Scripts" / "python.exe"
        return self._server_dir / ".venv" / "bin" / "python"

    # ── Phase 19.5 : accès read-only au Popen pour client_factory ────────

    @property
    def process(self) -> Optional["subprocess.Popen"]:
        """Retourne le Popen actif si le runner est démarré ET le subprocess
        est encore vivant, sinon None.

        Propriété READ-ONLY (aucun setter). Le runner reste propriétaire
        exclusif du lifecycle (start/stop). Le caller (typiquement
        client_factory.create_mcp_client_from_runner) peut LIRE le Popen
        pour attacher un MCPClient, mais NE DOIT PAS appeler
        terminate/kill/wait ni fermer les handles stdin/stdout/stderr.

        Defensive 3-niveaux (thread-safe via _state_lock) :
          1. state not in (STARTING, RUNNING) → None
          2. _process is None → None
          3. _process.poll() is not None → None (subprocess terminé avant
             que le watcher ait mis CRASHED)

        Évite d'attacher MCPClient à un Popen déjà mort en cas de
        désynchronisation transitoire.
        """
        with self._state_lock:
            proc = self._process
            if self._state not in (ProcessState.STARTING, ProcessState.RUNNING):
                return None
            if proc is None:
                return None
            try:
                if proc.poll() is not None:
                    return None
            except Exception:  # noqa: BLE001
                return None
            return proc

    def _is_install_complete(self) -> bool:
        """Vérifie marqueur d'install : présence du dossier + sentinelle."""
        sentinel = self._server_dir / ".install_done"
        return sentinel.exists()

    def _mark_install_complete(self) -> None:
        sentinel = self._server_dir / ".install_done"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(_now_iso(), encoding="utf-8")

    # ── Env minimal (aucun secret) ───────────────────────────────────────

    def _build_install_env(self) -> Dict[str, str]:
        """Env minimal pour l'install : pas de secrets, juste système.

        Caches npm/uv redirigés dans le dossier isolé du serveur.
        """
        allowlist = _system_env_allowlist()
        env: Dict[str, str] = {}
        for key in allowlist:
            value = os.environ.get(key)
            if value:
                env[key] = value
        # Caches redirigés
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        env["NPM_CONFIG_CACHE"] = str(self._cache_dir / "npm")
        env["UV_CACHE_DIR"] = str(self._cache_dir / "uv")
        # Désactive prompts interactifs
        env["CI"] = "1"
        env["NPM_CONFIG_FUND"] = "false"
        env["NPM_CONFIG_AUDIT"] = "false"
        return env

    def _build_runtime_env(
        self, runtime_env_secrets: Optional[Dict[str, str]]
    ) -> Dict[str, str]:
        """Env runtime : système minimal + secrets allowlistés.

        runtime_env_secrets doit être un sous-ensemble strict de
        spec.env_keys_allowlist. Toute clé hors allowlist → MCPSandboxError.
        """
        env = self._build_install_env()
        if runtime_env_secrets is None:
            return env
        if not isinstance(runtime_env_secrets, dict):
            raise MCPSandboxError("runtime_env_secrets must be a dict")
        allowed = set(self.spec.env_keys_allowlist)
        for key, value in runtime_env_secrets.items():
            if not isinstance(key, str) or not key:
                raise MCPSandboxError(f"Invalid env key: {key!r}")
            if key not in allowed:
                raise MCPSandboxError(
                    f"Env key {key!r} not in env_keys_allowlist for "
                    f"MCP server {self.spec.name!r}"
                )
            if not isinstance(value, str):
                raise MCPSandboxError(
                    f"Env value for {key!r} must be a string"
                )
            env[key] = value
            # Track pour scrubbing logs
            if value and len(value) >= 4:
                self._scrub_values.append(value)
        return env

    # ── Install lock ─────────────────────────────────────────────────────

    @contextmanager
    def _install_lock(self):
        self._install_lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self._install_lock_path), timeout=_INSTALL_LOCK_TIMEOUT_S)
        try:
            with lock:
                yield
        except Timeout as e:
            raise MCPSandboxError(
                f"Could not acquire install lock for {self.spec.name!r} within "
                f"{_INSTALL_LOCK_TIMEOUT_S}s"
            ) from e

    # ── Install ──────────────────────────────────────────────────────────

    def is_installed(self) -> bool:
        return self._is_install_complete()

    def install(self) -> None:
        """Installe le package MCP de manière isolée.

        Idempotent : si déjà installé, no-op.
        """
        with self._install_lock():
            if self._is_install_complete():
                logger.debug(
                    "[mcp.install] {!r} déjà installé, skip", self.spec.name
                )
                with self._state_lock:
                    self._state = ProcessState.INSTALLED
                return

            self._server_dir.mkdir(parents=True, exist_ok=True)
            install_env = self._build_install_env()

            if self.spec.transport == "npm":
                self._install_npm(install_env)
            else:  # uv
                self._install_uv(install_env)

            self._mark_install_complete()
            with self._state_lock:
                self._state = ProcessState.INSTALLED
            logger.info(
                "[mcp.install] {!r} installé dans {}",
                self.spec.name,
                self._server_dir,
            )

    @staticmethod
    def _normalize_pin(version: Optional[str]) -> Optional[str]:
        """Phase I-8 (Fix AN) : 'latest'/'*'/'' ne sont pas des pins.

        target_resolver et proposal_planner emploient le sentinel "latest"
        quand aucune version n'est extraite de la cible. npm tolère
        `pkg@latest`, mais pip/uv rejettent `pkg==latest` (observé runtime
        2026-06-12 03:00 : "Failed to parse: bitcoin-mcp==latest").
        Un sentinel → install sans pin (le registry résout la dernière).
        """
        if not version:
            return None
        v = version.strip()
        if v.lower() in ("latest", "*", ""):
            return None
        return v

    def _install_npm(self, env: Dict[str, str]) -> None:
        """npm install --prefix <isolated> --ignore-scripts <package>."""
        package_spec = self.spec.package
        pin = self._normalize_pin(self.spec.package_version)
        if pin:
            package_spec = f"{self.spec.package}@{pin}"
        cmd = [
            _resolve_cli_binary("npm"),
            "install",
            "--prefix",
            str(self._server_dir),
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            package_spec,
        ]
        self._run_install_command(cmd, env)

    def _install_uv(self, env: Dict[str, str]) -> None:
        """uv venv <isolated>/.venv puis uv pip install --python <venv> ...

        Stratégie : install isolée dans venv dédié (pas run-only).
          - venv créé : `uv venv .venv`
          - install : `uv pip install --python <venv_python> --no-build <package>`
          - --no-build = refus build depuis source distribution (wheels only)
          - --require-hashes si lock_file fourni
        """
        venv_dir = self._server_dir / ".venv"
        # 1. Créer le venv isolé.
        # Phase I-8 (Fix AM) : --clear rend l'install IDEMPOTENT. Sans lui,
        # `uv venv` échoue (exit 2: "A virtual environment already exists")
        # sur tout .venv résiduel d'un install précédent — un install raté
        # empoisonnait définitivement tous les suivants (observé runtime
        # 2026-06-11 22:58, cause exacte révélée par le log Fix AK.2).
        self._create_uv_venv(venv_dir, env, python_version=None)

        # 2. Construire la commande d'install (réutilisée par le retry Fix AX).
        package_spec = self.spec.package
        pin = self._normalize_pin(self.spec.package_version)
        if pin:
            package_spec = f"{self.spec.package}=={pin}"

        cmd_install = [
            _resolve_cli_binary("uv"),
            "pip",
            "install",
            "--python",
            str(self.venv_python),
            package_spec,
        ]
        if self.spec.require_wheels_only:
            # --no-build : refuse de builder depuis sdist (wheels only)
            cmd_install.append("--no-build")
        if self.spec.lock_file is not None:
            lock = self.spec.lock_file
            if not lock.exists():
                raise MCPSandboxError(
                    f"lock_file not found: {lock}"
                )
            cmd_install.extend(["--require-hashes", "-r", str(lock)])

        # 3. Install — tentative nominale sans lever, pour inspecter le stderr.
        result = self._run_install_command(cmd_install, env, check=False)
        if result.returncode == 0:
            return

        # 4. Fix AX — retry réactif sur exigence de version Python.
        # Le venv par défaut hérite du Python qui exécute uv ; certains MCP
        # exigent un Python plus récent (ex. windows-mcp → Python>=3.13).
        # uv sait télécharger un CPython standalone managé : on recrée le venv
        # ISOLÉ avec `--python X.Y` et on relance l'install une seule fois.
        combined = f"{result.stderr or ''}\n{result.stdout or ''}"
        match = _PY_REQUIRES_RE.search(combined)
        if match is None:
            # Échec pour une autre raison : on relève l'erreur d'origine.
            raise MCPSandboxError(
                f"Install command failed (exit {result.returncode}): "
                f"{' '.join(cmd_install[:3])}... "
                f"stderr: {(result.stderr or '')[:500]}"
            )

        required_py = match.group(1)
        # Recrée le venv isolé avec le Python managé requis, puis ré-install.
        self._create_uv_venv(venv_dir, env, python_version=required_py)
        self._run_install_command(cmd_install, env, check=True)

    def _create_uv_venv(
        self,
        venv_dir: "Path",
        env: Dict[str, str],
        *,
        python_version: Optional[str],
    ) -> None:
        """Crée (ou recrée, `--clear`) le venv isolé du serveur.

        `python_version` (ex. "3.13") force uv à utiliser/télécharger un CPython
        managé de cette version, confiné dans le cache du serveur (UV_CACHE_DIR
        redirigé). Le venv de Lumena n'est jamais touché.
        """
        cmd_venv = [_resolve_cli_binary("uv"), "venv", "--clear"]
        if python_version:
            cmd_venv.extend(["--python", python_version])
        cmd_venv.append(str(venv_dir))
        self._run_install_command(cmd_venv, env, check=True)

    def _run_install_command(
        self,
        cmd: List[str],
        env: Dict[str, str],
        *,
        check: bool = True,
    ) -> "subprocess.CompletedProcess[str]":
        """Lance une commande d'install avec env contrôlé et timeout.

        Par défaut (`check=True`) lève `MCPSandboxError` si le code retour est
        non nul. Avec `check=False`, retourne le `CompletedProcess` sans lever —
        utilisé par le retry réactif Fix AX qui doit inspecter le stderr pour
        détecter une exigence de version Python.
        """
        try:
            result = subprocess.run(
                cmd,
                env=env,
                cwd=str(self._server_dir),
                capture_output=True,
                text=True,
                # Fix Y : npm écrit de l'UTF-8 (noms de packages, arbres
                # unicode) — cp1252 par défaut Windows crashe le decode.
                encoding="utf-8",
                errors="replace",
                timeout=600,
                check=False,
            )
        except FileNotFoundError as e:
            raise MCPSandboxError(
                f"Install binary not found: {cmd[0]}. Is it on PATH?"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise MCPSandboxError(
                f"Install command timed out after 600s: {cmd[0]}"
            ) from e
        if check and result.returncode != 0:
            # Pas de scrub ici car install env n'a aucun secret
            raise MCPSandboxError(
                f"Install command failed (exit {result.returncode}): "
                f"{' '.join(cmd[:3])}... "
                f"stderr: {result.stderr[:500]}"
            )
        return result

    def uninstall(self) -> None:
        """Stoppe le subprocess si running puis supprime le dossier serveur."""
        with self._state_lock:
            running = self._state in (ProcessState.RUNNING, ProcessState.STARTING)
        if running:
            self.stop()
        with self._install_lock():
            if self._server_dir.exists():
                shutil.rmtree(self._server_dir, ignore_errors=True)
            with self._state_lock:
                self._state = ProcessState.NOT_INSTALLED
        logger.info("[mcp.uninstall] {!r} supprimé", self.spec.name)

    # ── Lifecycle ────────────────────────────────────────────────────────

    def _resolve_local_npm_bin(self) -> Optional[Path]:
        """Cherche un binaire local installé sous <server_dir>/node_modules/.bin/.

        Heuristique : si package = "@scope/name" ou "name", cherche le binaire
        de nom "name" (souvent exposé via le champ `bin` du package.json).

        Returns None si aucun candidat trouvé.

        IMPORTANT : ne lance JAMAIS de download réseau. Si rien n'existe
        localement, c'est `start()` qui lève MCPSandboxError, pas un npx
        avec téléchargement implicite.
        """
        bin_dir = self._server_dir / "node_modules" / ".bin"
        if not bin_dir.exists():
            return None
        pkg = self.spec.package
        if "/" in pkg:
            basename = pkg.split("/")[-1]
        else:
            basename = pkg

        # Fix O (Phase I-7) : les serveurs MCP officiels Anthropic
        # `@modelcontextprotocol/server-<X>` exposent en réalité le binaire
        # sous le nom `mcp-server-<X>` (cf package.json "bin"). L'heuristique
        # naïve basename-seule rate ces cas (le plus fréquent en pratique).
        # On essaie d'abord basename direct, puis le pattern `mcp-<basename>`.
        bin_names: List[str] = [basename]
        if not basename.startswith("mcp-"):
            bin_names.append(f"mcp-{basename}")

        # Fix P (Phase I-7) : sur Windows, npm installe TROIS variantes :
        #   - mcp-server-slack       (script shell Unix, #!/usr/bin/env node)
        #   - mcp-server-slack.cmd   (wrapper batch Windows) ← LE BON
        #   - mcp-server-slack.ps1   (wrapper PowerShell)
        # Popen Windows ne peut PAS exécuter le script Unix sans extension
        # → WinError 193 (%1 n'est pas une application Win32 valide).
        # L'ordre des candidats DOIT donc prioriser .cmd avant sans-extension
        # sur Windows. Sur Unix, c'est l'inverse (.cmd n'existe pas, le
        # sans-extension est le script natif).
        candidates: List[Path] = []
        for name in bin_names:
            if sys.platform == "win32":
                # Windows : .cmd > .ps1 > sans-extension (Popen-compatible)
                candidates.append(bin_dir / f"{name}.cmd")
                candidates.append(bin_dir / f"{name}.ps1")
                candidates.append(bin_dir / name)
            else:
                # Unix : sans-extension uniquement (script shell exécutable)
                candidates.append(bin_dir / name)

        for c in candidates:
            if c.exists():
                return c

        # Fix P fallback : scan opportuniste du .bin/ pour un exécutable
        # dont le nom CONTIENT le basename (couvre les packages exotiques).
        # Priorité Windows : .cmd > .ps1 > sans-extension.
        try:
            entries = [e for e in sorted(bin_dir.iterdir()) if e.is_file()]
            if sys.platform == "win32":
                # Tri stable : .cmd d'abord, puis .ps1, puis le reste
                def _win_prio(p: Path) -> int:
                    if p.suffix == ".cmd":
                        return 0
                    if p.suffix == ".ps1":
                        return 1
                    return 2  # sans-extension (Unix script, risqué sur Win)
                entries.sort(key=_win_prio)
            for entry in entries:
                stem = entry.stem.lower()
                if basename.lower() in stem:
                    # Sur Windows, refuser explicitement le sans-extension
                    # (Unix shell script → WinError 193 garanti)
                    if sys.platform == "win32" and entry.suffix == "":
                        continue
                    return entry
        except OSError:
            pass
        return None

    def _resolve_local_venv_bin(self) -> Optional[Path]:
        """Cherche un entry point console installé sous <venv>/Scripts|bin/.

        Phase I-8 (Fix AO) : miroir pypi des Fix O/P npm. pip/uv exposent
        les console_scripts comme exécutables (.exe sur Windows) dans le
        venv — c'est LE chemin de lancement standard d'un package dont le
        nom contient un tiret (`python -m bitcoin-mcp` est impossible :
        un nom de module ne peut pas contenir de tiret — observé runtime
        2026-06-12 03:36 : « No module named bitcoin-mcp », exit 1).

        Ne lance JAMAIS de download réseau : le venv est figé.
        """
        if sys.platform == "win32":
            bin_dir = self._server_dir / ".venv" / "Scripts"
        else:
            bin_dir = self._server_dir / ".venv" / "bin"
        if not bin_dir.exists():
            return None

        basename = self.spec.package.lower()
        underscored = basename.replace("-", "_").replace(".", "_")
        bin_names: List[str] = [basename]
        if underscored != basename:
            bin_names.append(underscored)
        if not basename.startswith("mcp-"):
            bin_names.append(f"mcp-{basename}")

        candidates: List[Path] = []
        for name in bin_names:
            if sys.platform == "win32":
                # pip génère des launchers .exe pour les console_scripts
                candidates.append(bin_dir / f"{name}.exe")
                candidates.append(bin_dir / f"{name}.cmd")
            else:
                candidates.append(bin_dir / name)

        for c in candidates:
            if c.exists():
                return c

        # Scan opportuniste : exécutable dont le nom CONTIENT le basename
        # (couvre les entry points exotiques, ex. `<pkg>-server`). Les
        # outils standard du venv (python, pip, activate...) ne matchent
        # pas le basename — pas de risque de les élire.
        try:
            entries = [e for e in sorted(bin_dir.iterdir()) if e.is_file()]
            for entry in entries:
                stem = entry.stem.lower()
                if basename not in stem and underscored not in stem:
                    continue
                if sys.platform == "win32":
                    if entry.suffix == ".exe":
                        return entry
                elif entry.suffix == "":
                    return entry
        except OSError:
            pass
        return None

    def set_entry_args(self, entry_args: List[str]) -> None:
        """Phase I-8 (Fix AY) : remplace les entry_args du spec (frozen via
        dataclasses.replace — on réassigne l'ATTRIBUT du runner, jamais le
        dataclass). Utilisé par le retry réactif d'activation quand l'entry
        point console est un CLI à sous-commandes (ex. windows-mcp →
        `windows-mcp.exe serve`). N'affecte que le prochain start.
        """
        self.spec = _dc_replace(self.spec, entry_args=list(entry_args or []))

    def _build_start_command(self) -> List[str]:
        """Détermine la commande à lancer pour le serveur installé.

        Pour transport="npm" :
          - Si spec.args fourni → utiliser tel quel (commande explicite)
          - Sinon → tenter résolution d'un binaire local dans
            <server_dir>/node_modules/.bin/. Si présent, l'utiliser.
          - Sinon → MCPSandboxError. JAMAIS `npx --yes` ou équivalent
            qui résoudrait/téléchargerait au runtime (cela contournerait
            l'install `--ignore-scripts`).

        Pour transport="uv" :
          - Si spec.args fourni → exécuté dans le Python du venv isolé
          - Sinon → entry point console du venv (Fix AO), puis fallback
            `python -m <package_normalisé_underscores>` dans le venv isolé
            (pas de download réseau possible — le venv est déjà figé)
        """
        # Fix AY : entry_args = arguments ajoutés au binaire RÉSOLU (jamais
        # quand spec.args remplace la commande entière).
        entry_args = list(getattr(self.spec, "entry_args", None) or [])
        if self.spec.transport == "npm":
            if self.spec.args:
                return list(self.spec.args)
            local_bin = self._resolve_local_npm_bin()
            if local_bin is not None:
                return [str(local_bin), *entry_args]
            raise MCPSandboxError(
                f"Cannot start MCP server {self.spec.name!r} (npm transport): "
                f"no spec.args provided and no local binary found in "
                f"{self._server_dir / 'node_modules' / '.bin'}. "
                "Provide spec.args=[<local_executable_path>, ...] explicitly. "
                "Network-fetching tools like 'npx --yes' are intentionally NOT "
                "used (would bypass --ignore-scripts install controls)."
            )
        else:  # uv
            if self.spec.args:
                return [str(self.venv_python), *self.spec.args]
            # Fix AO : d'abord l'entry point console du venv (chemin
            # standard pip ; obligatoire si le nom contient un tiret).
            local_bin = self._resolve_local_venv_bin()
            if local_bin is not None:
                return [str(local_bin), *entry_args]
            # Fallback python -m : nom de module = nom du package
            # normalisé en underscores (un tiret n'est jamais importable).
            # Pas de download possible : le venv est figé.
            module = self.spec.package.replace("-", "_").replace(".", "_")
            return [str(self.venv_python), "-m", module, *entry_args]

    def start(
        self,
        runtime_env_secrets: Optional[Dict[str, str]] = None,
    ) -> None:
        """Démarre le subprocess MCP.

        Args:
            runtime_env_secrets: dict {env_key: value} provenant de SecretsService.
                Clés doivent ⊆ spec.env_keys_allowlist, sinon refus.

        Raises:
            MCPSandboxError: si non installé, quarantiné, ou env invalide.
        """
        with self._state_lock:
            if self._quarantined:
                raise MCPSandboxError(
                    f"MCP server {self.spec.name!r} is quarantined "
                    f"(too many crashes). Call clear_quarantine() first."
                )
            if self._state == ProcessState.NOT_INSTALLED:
                raise MCPSandboxError(
                    f"MCP server {self.spec.name!r} not installed yet"
                )
            if self._state in (ProcessState.STARTING, ProcessState.RUNNING):
                logger.debug(
                    "[mcp.start] {!r} déjà running/starting, no-op", self.spec.name
                )
                return
            self._state = ProcessState.STARTING

        env = self._build_runtime_env(runtime_env_secrets)
        cmd = self._build_start_command()

        try:
            # Phase 5.1 : stderr SÉPARÉ de stdout (compat MCPClient).
            # stdout = JSON-RPC pur (consommé par MCPClient)
            # stderr = logs textuels du serveur (capturés pour debug)
            # Fix Y (Phase I-7) : encoding UTF-8 EXPLICITE. Sans lui,
            # text=True utilise l'encodage console Windows (cp1252) →
            # UnicodeDecodeError au premier byte non-ASCII dans une réponse
            # JSON-RPC (noms de canaux accentués, emojis...). La spec MCP
            # impose UTF-8 sur stdio. errors="replace" : un byte invalide
            # ne doit JAMAIS casser le canal entier.
            self._process = subprocess.Popen(
                cmd,
                env=env,
                cwd=str(self._server_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as e:
            with self._state_lock:
                self._state = ProcessState.CRASHED
            raise MCPSandboxError(
                f"Start binary not found: {cmd[0]}"
            ) from e

        # Phase 5.1 v2 : MARQUE RUNNING AVANT de démarrer les threads.
        # Sinon race condition : un process qui sort très vite peut faire
        # passer le watcher en STOPPED/CRASHED, puis start() écraserait en
        # RUNNING.
        with self._state_lock:
            self._state = ProcessState.RUNNING

        # Branchement selon stdout_mode
        if self._stdout_mode == "client":
            # MCPClient possèdera stdout. Le runner NE LIT JAMAIS stdout.
            # Crash detection via proc.wait() dans un thread léger.
            self._watcher_thread = threading.Thread(
                target=self._process_watcher_loop,
                name=f"mcp-watcher-{self.spec.name}",
                daemon=True,
            )
            self._watcher_thread.start()
        else:
            # Mode "capture" (rétrocompat) : runner lit stdout
            self._monitor_thread = threading.Thread(
                target=self._stdout_reader_loop,
                name=f"mcp-monitor-{self.spec.name}",
                daemon=True,
            )
            self._monitor_thread.start()
        # Stderr toujours capturé par le runner
        self._stderr_thread = threading.Thread(
            target=self._stderr_reader_loop,
            name=f"mcp-stderr-{self.spec.name}",
            daemon=True,
        )
        self._stderr_thread.start()

        logger.info(
            "[mcp.start] {!r} démarré (PID {})",
            self.spec.name,
            self._process.pid if self._process else "?",
        )

    def _stdout_reader_loop(self) -> None:
        """Thread : capture stdout (JSON-RPC pur en usage MCPClient)
        + détection crash à EOF."""
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                clean = self._scrub_line(line.rstrip("\n"))
                self._stdout_buffer.append(clean)
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "[mcp.stdout] {!r} read error: {}", self.spec.name, e
            )
        finally:
            # Process terminé : vérifier le code de retour
            try:
                rc = proc.wait(timeout=1.0)
            except Exception:
                rc = None
            with self._state_lock:
                expected_stop = self._state == ProcessState.STOPPING
                if not expected_stop and rc is not None and rc != 0:
                    # Crash non sollicité
                    self._record_crash()
                    self._state = ProcessState.CRASHED
                elif self._state != ProcessState.STOPPED:
                    self._state = ProcessState.STOPPED

    def _process_watcher_loop(self) -> None:
        """Thread : crash detection sans toucher stdout (mode "client").

        Logique ultra simple :
          rc = proc.wait()  (bloque jusqu'à terminaison)
          si state == STOPPING → STOPPED
          sinon si rc != 0     → record_crash + CRASHED
          sinon                → STOPPED

        Aucun readline stdout, aucun polling. stdout appartient au MCPClient.
        """
        proc = self._process
        if proc is None:
            return
        try:
            rc = proc.wait()
        except Exception:  # noqa: BLE001
            rc = None
        with self._state_lock:
            expected_stop = self._state == ProcessState.STOPPING
            if expected_stop:
                self._state = ProcessState.STOPPED
            elif rc is not None and rc != 0:
                self._record_crash()
                self._state = ProcessState.CRASHED
            else:
                self._state = ProcessState.STOPPED
        # Fix W (Phase I-7) : une mort NON sollicitée doit être VISIBLE dans
        # les logs avec le code de sortie et la fin du stderr — sans ça, un
        # process MCP qui meurt en silence n'est découvert qu'au prochain
        # appel (EOF), sans aucune indication de cause.
        if not expected_stop:
            stderr_tail = list(self._stderr_buffer)[-5:]
            logger.warning(
                "[mcp.watcher] {!r} process terminé de façon inattendue "
                "(exit_code={}). stderr tail: {}",
                self.spec.name,
                rc,
                stderr_tail,
            )

    def _stderr_reader_loop(self) -> None:
        """Thread : capture stderr séparément (logs serveur, jamais stdout)."""
        proc = self._process
        if proc is None or proc.stderr is None:
            return
        try:
            for line in iter(proc.stderr.readline, ""):
                if not line:
                    break
                clean = self._scrub_line(line.rstrip("\n"))
                self._stderr_buffer.append(clean)
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "[mcp.stderr] {!r} read error: {}", self.spec.name, e
            )

    def _scrub_line(self, line: str) -> str:
        """Remplace les valeurs secrètes runtime connues par ****."""
        if not self._scrub_values:
            return line
        result = line
        for value in sorted(self._scrub_values, key=len, reverse=True):
            if value:
                result = result.replace(value, "****")
        return result

    def _record_crash(self) -> None:
        """Enregistre un crash et déclenche quarantaine si seuil atteint."""
        now = time.time()
        # Garbage collect anciens crashes
        cutoff = now - _QUARANTINE_CRASH_WINDOW_S
        self._crash_timestamps = [t for t in self._crash_timestamps if t >= cutoff]
        self._crash_timestamps.append(now)
        if len(self._crash_timestamps) >= _QUARANTINE_CRASH_THRESHOLD:
            self._quarantined = True
            logger.warning(
                "[mcp.quarantine] {!r} quarantiné après {} crashes en {}s",
                self.spec.name,
                len(self._crash_timestamps),
                _QUARANTINE_CRASH_WINDOW_S,
            )

    def stop(self) -> None:
        """Arrête le subprocess proprement (SIGTERM puis SIGKILL après timeout).

        Sur Windows, SIGTERM ≈ kill brutal (terminate()). Pas de signal
        gracieux natif Windows pour subprocess.
        """
        with self._state_lock:
            if self._state not in (ProcessState.STARTING, ProcessState.RUNNING):
                return  # no-op
            self._state = ProcessState.STOPPING

        proc = self._process
        if proc is None:
            with self._state_lock:
                self._state = ProcessState.STOPPED
            return

        try:
            proc.terminate()  # SIGTERM sur POSIX, TerminateProcess sur Windows
            try:
                proc.wait(timeout=self._stop_timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[mcp.stop] {!r} stop error: {}", self.spec.name, e
            )

        # Phase 5.1 v2 : attendre les threads selon mode
        # (monitor en mode capture, watcher en mode client, stderr toujours)
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=2.0)
        if self._watcher_thread is not None:
            self._watcher_thread.join(timeout=2.0)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2.0)

        with self._state_lock:
            self._state = ProcessState.STOPPED
            self._process = None
        logger.info("[mcp.stop] {!r} arrêté", self.spec.name)

    def restart(
        self,
        runtime_env_secrets: Optional[Dict[str, str]] = None,
    ) -> None:
        """stop() puis start()."""
        self.stop()
        # Si l'install a été détruit entre-temps, vérifier
        if not self._is_install_complete():
            raise MCPSandboxError(
                f"MCP server {self.spec.name!r} no longer installed"
            )
        with self._state_lock:
            self._state = ProcessState.INSTALLED
        self.start(runtime_env_secrets=runtime_env_secrets)

    # ── Healthcheck process-level ────────────────────────────────────────

    def is_alive(self) -> bool:
        """True si le subprocess est encore en cours.

        Process-level uniquement (PID actif + return code None).
        PAS de healthcheck protocole MCP — c'est Phase 7.
        """
        with self._state_lock:
            proc = self._process
            if proc is None:
                return False
            return proc.poll() is None

    def state(self) -> ProcessState:
        with self._state_lock:
            return self._state

    def get_pid(self) -> Optional[int]:
        with self._state_lock:
            if self._process is None:
                return None
            return self._process.pid

    # ── Logs ─────────────────────────────────────────────────────────────

    def get_logs(
        self,
        lines: int = 100,
        stream: str = "stdout",
    ) -> List[str]:
        """Retourne les dernières `lines` lignes capturées.

        Args:
            lines: nombre max de lignes
            stream: "stdout" (défaut, JSON-RPC pur si client MCP),
                    "stderr" (logs serveur), ou "all" (concat marqué)

        Important Phase 5.1 : stdout et stderr sont CAPTURÉS
        SÉPARÉMENT. Pour usage MCPClient, stdout = JSON-RPC pur.
        Les logs texte du serveur arrivent par stderr.
        """
        if lines <= 0:
            return []
        if stream == "stdout":
            # En mode "client", le runner ne lit pas stdout → buffer toujours vide.
            # On retourne [] de façon explicite (état attendu, pas une erreur).
            if self._stdout_mode == "client":
                return []
            return list(self._stdout_buffer)[-lines:]
        if stream == "stderr":
            return list(self._stderr_buffer)[-lines:]
        if stream == "all":
            # Concat avec préfixe pour distinguer
            # En mode client, [stdout] sera vide → seuls [stderr] apparaissent
            out_lines = (
                [f"[stdout] {line}" for line in self._stdout_buffer]
                if self._stdout_mode == "capture"
                else []
            )
            err_lines = [f"[stderr] {line}" for line in self._stderr_buffer]
            return (out_lines + err_lines)[-lines:]
        raise MCPSandboxError(
            f"Invalid stream {stream!r}: expected 'stdout', 'stderr', or 'all'"
        )

    # ── Quarantine ────────────────────────────────────────────────────────

    def crash_count_last_5min(self) -> int:
        """Nombre de crashes dans la fenêtre de quarantine."""
        cutoff = time.time() - _QUARANTINE_CRASH_WINDOW_S
        return sum(1 for t in self._crash_timestamps if t >= cutoff)

    def is_quarantined(self) -> bool:
        return self._quarantined

    def clear_quarantine(self) -> None:
        """Reset le compteur de crashes et l'état quarantaine.

        À appeler explicitement après diagnostic du crash.
        """
        self._crash_timestamps.clear()
        self._quarantined = False
        logger.info("[mcp.quarantine] {!r} quarantine clearée", self.spec.name)
