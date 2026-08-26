"""
system.py - Handlers système fragmentés depuis react.py.

Handlers: run_command, get_time, parallel_tools, get_token_stats,
          screenshot_tool, dummy.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult, SubToolResult
from .registry_v2 import HandlerDef

# Import optionnel compaction
try:
    from ...tools.compaction import get_token_stats as _get_token_stats_fn, format_token_stats as _format_token_stats_fn
    COMPACTION_AVAILABLE = True
except ImportError:
    COMPACTION_AVAILABLE = False
    _get_token_stats_fn = None
    _format_token_stats_fn = None


# ─── Helpers ───────────────────────────────────────────────────────────────


def _is_broad_pytest_at_lumena_root(command: str, cwd, lumena_root) -> bool:
    """LOT 2.11.B — décision pure : ce `pytest` va-t-il collecter TOUT le dépôt ?

    True (donc à REFUSER) quand la commande est un pytest, que le cwd résolu est
    la racine Lumena elle-même, ET qu'aucune cible fichier `.py` précise n'est
    donnée. Un tel run collecte les 16 000+ tests du dépôt → timeout garanti en
    mission (run des 5 missions : une mission a lancé `pytest` nu à la racine).

    Invariant général (pas cas-par-cas) : on ne bloque QUE la collecte pleine à la
    racine ; cibler un dossier livrable (`cwd=workspace/<projet>`) ou un fichier
    de test précis passe toujours.
    """
    if not command or cwd is None or lumena_root is None:
        return False
    import re as _re
    try:
        from ...utils.docker_sandbox import is_python_test_command as _is_pytest_cmd
    except Exception:
        return False
    if not _is_pytest_cmd(command):
        return False
    try:
        cwd_p = Path(str(cwd)).resolve()
        root_p = Path(str(lumena_root)).resolve()
    except Exception:
        return False
    if cwd_p != root_p:
        return False
    # Une cible fichier `.py` explicite (ex. tests/test_app.py ou ::TestX) = OK.
    return not _re.search(r"\S+\.py(::|\b)", command)


def _resolve_cwd(raw: str, lumena_root, mission_dir: Optional[Path] = None) -> Optional[str]:
    """2.11.b : résout un répertoire de travail demandé (préfixe `cd X &&` ou
    param cwd=) vers un dossier EXISTANT.

    Ordre : absolu tel quel → mission_dir/raw (B0.2 : en mission, un relatif se
    résout D'ABORD dans le dossier mission — sinon `cwd='tests'` attrape le
    tests/ de Lumena) → lumena_root/raw → WORKSPACE_DIR/raw.
    Retourne None si aucun candidat n'existe — le caller doit alors ÉCHOUER
    clairement, jamais exécuter au mauvais endroit ni rapporter un faux timeout.
    """
    raw = (raw or "").strip().strip("\"'")
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return str(candidate) if candidate.is_dir() else None
    bases: List[Path] = []
    if mission_dir is not None:
        bases.append(Path(mission_dir))
    if lumena_root:
        bases.append(Path(lumena_root))
    try:
        from ...utils.paths import WORKSPACE_DIR as _ws_dir
        bases.append(Path(_ws_dir))
    except Exception:
        pass
    for base in bases:
        cand = base / raw
        if cand.is_dir():
            return str(cand.resolve())
    return None


async def _summarize_large_output(command: str, output: str, limit: int) -> Optional[str]:
    """Résume un output volumineux via LLM au lieu de le tronquer brutalement.

    Retourne le résumé formaté ou None si le LLM échoue (le caller
    doit alors appliquer la troncature head+tail classique).
    """
    if len(output) < 6000:
        return None
    try:
        from ...llm.multi_provider import MultiProviderLLM
        mp = MultiProviderLLM()
        messages = [
            {"role": "system", "content": (
                "Tu résumes des sorties de commandes shell. "
                "Garde : erreurs, codes de sortie, résultats clés, chemins importants, warnings. "
                "Élimine : lignes répétitives, barres de progression, output verbeux identique. "
                f"Résumé max {limit} caractères. Format brut, pas de markdown."
            )},
            {"role": "user", "content": f"Commande: {command}\n\nSortie ({len(output)} chars):\n{output[:12000]}"},
        ]
        summary = await asyncio.wait_for(
            mp.chat(messages, temperature=0.1, max_tokens=1024),
            timeout=10,
        )
        if summary and len(summary.strip()) > 20:
            return f"[résumé LLM — {len(output)} chars originaux]\n{summary.strip()}"
    except asyncio.TimeoutError:
        logger.debug("[run_command] résumé LLM échoué: timeout (10s)")
    except Exception as e:
        logger.debug(f"[run_command] résumé LLM échoué: {type(e).__name__}: {e}")
    return None


# ─── Handlers ──────────────────────────────────────────────────────────────

def _should_background_command(command: str) -> bool:
    """Detecte les commandes serveur/dev qui ne doivent pas bloquer REACT."""
    import re as _re_bg

    patterns = (
        r"\bnode\s+server\.js\b",
        r"\bnode\s+\S*app\.js\b",
        r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:dev|start|serve)\b",
        r"\bpython\s+-m\s+http\.server\b",
        r"\buvicorn\b",
        r"\bflask\s+run\b",
    )
    lowered = command.strip().lower()
    return any(_re_bg.search(pattern, lowered) for pattern in patterns)


def _mission_destructive_target_violation(ctx: HandlerContext, command: str) -> str:
    """LOT G1 — résout le contexte mission puis délègue au helper pur.

    Retourne le chemin fautif, ou `""` si rien à signaler. **Fail-open strict** :
    hors mission, sans workspace résolvable, ou sur la moindre erreur, on rend
    `""` — ce garde ne doit jamais empêcher une commande légitime de tourner.

    Le périmètre autorisé est le DOSSIER DE MISSION (partagé par tous les workers
    d'une même mission), pas `allowed_files` : un worker a des raisons légitimes
    de nettoyer un artefact commun (`__pycache__`, un `.bak`), et le périmètre
    fin des écritures reste couvert par `_assert_mission_file_allowed`.
    """
    try:
        if not getattr(ctx, "is_mission_run", False):
            return ""
        from src.utils.command_sanitizer import destructive_command_target_violation
        from src.utils.paths import ROOT_DIR, WORKSPACE_DIR

        # G1.c — TEST RÉEL du 2026-08-12 : le garde était INERTE sur une mission
        # simple. `mission_workspace` n'est attribué que par
        # `write_mission_contract` / `delegate_and_wait` ; une mission qui ne
        # délègue pas n'en a aucun. Exiger ce dossier laissait donc sans
        # protection le cas le PLUS COURANT — et la sentinelle a été supprimée.
        #
        # Le périmètre est désormais toujours défini :
        #   • mission avec dossier    -> ce dossier (périmètre étroit) ;
        #   • mission sans dossier    -> le WORKSPACE global (Lumena y travaille
        #                                légitimement ; le dépôt reste protégé).
        allowed_root = None
        _sub_fn = getattr(ctx, "mission_workspace_subdir", None)
        sub = _sub_fn() if callable(_sub_fn) else ""
        guardrails = getattr(ctx, "file_guardrails", None)
        if sub and guardrails is not None:
            try:
                allowed_root = guardrails._workspace_root() / sub
            except Exception:
                allowed_root = None
        if allowed_root is None:
            try:
                allowed_root = (
                    guardrails._workspace_root() if guardrails is not None else WORKSPACE_DIR
                )
            except Exception:
                allowed_root = WORKSPACE_DIR

        # Un périmètre autorisé qui ENGLOBE le dépôt ne garde plus rien.
        # `FileGuardrails._workspace_root()` peut légitimement retourner la racine
        # du projet (`return root` quand `_looks_like_project_root()` est faux) :
        # on retombe alors sur le workspace, seul périmètre de travail légitime.
        try:
            _allowed = Path(str(allowed_root)).resolve()
            _repo = Path(str(ROOT_DIR)).resolve()
            if _allowed == _repo or _repo.is_relative_to(_allowed):
                allowed_root = WORKSPACE_DIR
        except Exception:
            allowed_root = WORKSPACE_DIR

        return destructive_command_target_violation(
            command, mission_root=str(allowed_root), repo_root=str(ROOT_DIR),
        )
    except Exception as exc:
        logger.debug("[G1] garde de cible ignoré: {}", exc)
        return ""


# Z39 — signatures « executable introuvable », toutes coquilles confondues.
# Volontairement restreint aux formulations SANS ambiguïté : un shell qui dit
# « je ne connais pas ce programme » n'a rien exécuté, point. On ne marque PAS
# le stderr en général — git, curl et npm écrivent des avertissements sur
# stderr en cas de succès parfaitement légitime.
_COMMAND_NOT_FOUND_SIGNATURES: tuple[str, ...] = (
    "n'est pas reconnu comme nom d'applet de commande",   # PowerShell FR
    "is not recognized as the name of a cmdlet",          # PowerShell EN
    "commandnotfoundexception",                           # PowerShell (.NET)
    "n'est pas reconnu en tant que commande interne",     # cmd.exe FR
    "is not recognized as an internal or external command",  # cmd.exe EN
    "command not found",                                  # bash / sh
    # PAS « no such file or directory » : ça parle d'un FICHIER, pas d'un
    # exécutable, et `find` sur un lien cassé sort 0 en l'écrivant.
)


def _command_not_found_in(stderr: str) -> bool:
    """Vrai si stderr porte une signature d'exécutable introuvable."""
    if not stderr or not stderr.strip():
        return False
    bas = stderr.lower()
    return any(signature in bas for signature in _COMMAND_NOT_FOUND_SIGNATURES)


async def run_command_handler(
    ctx: HandlerContext, command: str,
    stdin_input: str = "", timeout: int = 0,
    cwd: Optional[str] = None,
    background: bool = False,
) -> HandlerResult:
    """Execute une commande shell de manière asynchrone (non-bloquante)."""
    try:
        ide_runtime = ctx.is_ide_runtime()

        # ── LOT G1 — GARDE DE CIBLE (mission uniquement) ───────────────────────
        # Le sanitizer juge la DANGEROSITÉ d'une commande, jamais la PROPRIÉTÉ de
        # sa cible : `del <fichier>` est explicitement autorisé (un worker doit
        # pouvoir nettoyer SES fichiers). Le 2026-08-12, une mission a exécuté
        # `del …\lumena\pytest.ini` (exit 0) pour contourner un conflit de config
        # pytest — supprimant un fichier du dépôt. `Rename-Item` sur la même cible
        # avait été bloqué (verbe interdit) ; `pyproject.toml` n'a survécu que par
        # hasard de séquence.
        # `allowed_files` protège les OUTILS FICHIERS ; cette porte-ci n'avait
        # aucun garde de périmètre. Additif, mission-only, conservateur.
        _g1_violation = _mission_destructive_target_violation(ctx, command)
        if _g1_violation:
            logger.warning(
                "[G1] commande destructive hors périmètre refusée : cible={} cmd={}",
                _g1_violation, str(command)[:160],
            )
            return HandlerResult.ok(
                f"⛔ Commande refusée : elle détruirait `{_g1_violation}`, qui "
                "appartient au dépôt Lumena et **n'est pas un fichier de ta "
                "mission**.\n\n"
                "Tu ne peux supprimer, déplacer ou renommer que les fichiers de "
                "ton dossier de mission. Si un fichier du dépôt te gêne (config, "
                "test, source), **ne le neutralise pas** : adapte ton propre code, "
                "ou signale le blocage dans ton rapport pour que le lead tranche.",
                handler_name="run_command",
            )

        # Guard BDD IONOS (prioritaire) : interdire mysql/mariadb/php/node visant une base
        # IONOS (*.hosting-data.io, injoignable de l'extérieur). Rediriger vers le bridge
        # sécurisé (outils ionos_db_*) plutôt que lire config.php + lancer un client.
        import re as _re_ionos
        _cmd_low = command.lower()
        _ionos_db_tool = _re_ionos.search(r'\b(mysql|mysqldump|mariadb|php|node)\b', _cmd_low)
        _ionos_db_host = ("hosting-data.io" in _cmd_low) or bool(
            _re_ionos.search(r'\bdbs?\d{6,}\b', _cmd_low)  # bases IONOS type dbs15704993
        )
        if _ionos_db_tool and _ionos_db_host:
            return HandlerResult.ok(
                "⛔ Accès direct à une base IONOS interdit : ces BDD (*.hosting-data.io) ne "
                "sont pas joignables ainsi. Utilise le **bridge IONOS sécurisé** via les outils "
                "`ionos_db_*` (ex: `ionos_db_install_bridge`, `ionos_db_list_tables`, "
                "`ionos_db_create_sandbox_table`, `ionos_db_propose_write`). "
                "N'utilise pas mysql/php/node ni config.php en direct.",
                handler_name="run_command",
            )

        # ── Phase I-2 : guard MCP — bloquer `npm install`/`pip install`/uvx/npx
        #     ciblant un package MCP. L'install d'un MCP DOIT passer par
        #     `add_mcp(target=...)` qui utilise InstallOrchestrator + SandboxRunner.
        #     Cela garantit isolation (data/mcp/<server_id>), traçabilité Catalog,
        #     et propagation correcte du schéma config.
        from src.reasoning.handlers._mcp_shell_guard import (
            detect_mcp_shell_install,
            detect_react_tool_as_shell,
        )
        # Phase I-8 (Fix AP) : un outil ReAct MCP émis comme commande shell
        # (`run_mcp_autonomy(...)` dans run_command) → rediriger vers l'appel
        # d'outil direct AVANT le sanitizer (dont le refus générique égare le
        # LLM vers « cet outil n'existe pas »).
        _react_tool = detect_react_tool_as_shell(command)
        if _react_tool is not None:
            return HandlerResult.ok(
                f"⛔ `{_react_tool}` n'est PAS une commande shell — c'est un "
                "OUTIL.\n\n"
                "Appelle-le directement comme action :\n"
                "```\n"
                f"ACTION: {_react_tool}\n"
                "ACTION_INPUT: {…mêmes arguments en JSON…}\n"
                "```\n"
                "Il s'exécutera même s'il n'apparaît pas dans ta liste "
                "d'outils (soft-filter). N'utilise JAMAIS run_command pour "
                "les outils MCP.",
                handler_name="run_command",
            )
        _mcp_guard = detect_mcp_shell_install(command)
        if _mcp_guard is not None:
            return HandlerResult.ok(
                "⛔ Installation MCP via shell interdite — utilise **add_mcp**.\n\n"
                f"Détecté : `{_mcp_guard.detected_tool}` ciblant "
                f"`{_mcp_guard.detected_package}`.\n\n"
                "Les installs `npm install -g` / `pip install` / `uvx` / `npx` "
                "contournent le sandbox Lumena (`data/mcp/`), le Catalog "
                "(statut DECLARED→INSTALLED), et la persistance du schéma de "
                "config. Le MCP ainsi installé ne sera **PAS** activable.\n\n"
                "À la place :\n"
                "```\n"
                f"add_mcp(target=\"{_mcp_guard.suggested_target}\", live=False)\n"
                "```\n"
                "→ proposera le bon `package_spec` et les clés requises avant "
                "tout install effectif.",
                handler_name="run_command",
            )

        from ...utils.command_sanitizer import sanitize_chained_command
        extra = ctx._discovered_executables if ctx._discovered_executables else None
        allowed, reason = sanitize_chained_command(command, extra_allowed=extra)
        if not allowed:
            return HandlerResult.ok(f"⛔ {reason}", handler_name="run_command")

        # Guard: bloquer les commandes git nues et les `cd <dir> && git` visant
        # un dossier sans .git pour éviter d'opérer sur le repo lumena root.
        import re as _re_git
        _cmd_stripped = command.strip()

        # Cas 1: commande git SANS cd → opère sur lumena_root, presque toujours
        # une erreur. Rediriger vers les outils git dédiés.
        _bare_git_m = _re_git.match(r'^git\s+(\S+)', _cmd_stripped)
        if _bare_git_m and not _re_git.match(r'^git\s+(--version|help)', _cmd_stripped):
            _git_sub = _bare_git_m.group(1)
            return HandlerResult.ok(
                f"⚠️ Commande `git {_git_sub}` détectée sans répertoire cible. "
                f"Utilise les outils git dédiés (`git_status`, `git_init`, `git_add`, "
                f"`git_commit`, `git_push_pull`, `git_remote`) avec le paramètre `path` "
                f"pour cibler le bon dépôt.",
                handler_name="run_command",
            )

        # Cas 2: `cd [/d] <dir> && git <cmd>` → vérifier que <dir> a un .git propre
        _git_cd_m = _re_git.match(
            r'cd\s+(?:/d\s+)?("(?:[^"]+)"|\'(?:[^\']+)\'|[^\s&|;]+)\s*&&\s*git\s+(\S+)',
            _cmd_stripped,
        )
        if _git_cd_m:
            _cd_target = _git_cd_m.group(1).strip("\"'")
            _git_sub = _git_cd_m.group(2)
            if _git_sub not in ("init", "clone"):
                _target_dir = Path(ctx.lumena_root) / _cd_target
                if _target_dir.is_dir() and not (_target_dir / ".git").exists():
                    return HandlerResult.ok(
                        f"⚠️ Pas de dépôt git dans {_target_dir.name}/. "
                        f"Utilise d'abord `git_init` pour initialiser, "
                        f"puis les outils git dédiés (git_add, git_commit, git_push_pull).",
                        handler_name="run_command",
                    )

        # Guard: bloquer les serveurs de preview qui tentent de bind sur le port Lumena.
        # Stratégie : bloquer seulement si (a) port explicite = Lumena_port,
        # OU (b) commande defaultant à 8080 sans port spécifié (python -m http.server).
        import re as _re_port
        _lumena_port = int(os.getenv("LUMENA_PORT", "8080"))
        _explicit_port_m = _re_port.search(
            r'(?:--port|-p|:)\s*(\d{4,5})\b|(?:http\.server|npx\s+serve|npx\s+http-server)\s+(\d{4,5})\b',
            command, _re_port.IGNORECASE,
        )
        _http_server_default_m = _re_port.search(
            r'python(?:3)?\s+-m\s+http\.server\s*$|python(?:3)?\s+-m\s+http\.server\s+(?!\d)',
            command.strip(), _re_port.IGNORECASE,
        )
        _explicit_port = None
        if _explicit_port_m:
            _explicit_port = int(next(g for g in _explicit_port_m.groups() if g))
        if (_explicit_port == _lumena_port) or (_http_server_default_m and _explicit_port is None):
            _suggested = command.replace(str(_lumena_port), str(_lumena_port + 1), 1)
            return HandlerResult.ok(
                f"⛔ Port {_lumena_port} réservé à Lumena — utilise un port différent "
                f"(ex: `{_suggested}`).",
                handler_name="run_command",
            )

        # Timeout: paramètre explicite > défaut IDE/Telegram/WhatsApp
        if timeout and timeout > 0:
            timeout_sec = min(int(timeout), 1800)  # max 30min sécurité
        else:
            timeout_sec = ctx.ide_command_timeout_sec() if ide_runtime else 120

        # ── Extraire le préfixe "cd /d ..." injecté par CodeAgent ──
        # Doit être AVANT les traductions Linux→Windows (pour que ^head/^cat matchent)
        # et AVANT le sandbox Docker (pour passer le bon workdir).
        import re as _re_cd_extract
        _cwd = str(ctx.lumena_root) if ctx.lumena_root else None
        # B0.2 (run PlantCare) — en mission, le préambule A1 dit « tu es DÉJÀ dans
        # le dossier de la mission » : vrai pour les outils fichiers (résolution
        # mission-first) mais run_command démarrait à la racine Lumena → workers
        # désaxés (~15 itérations à se chercher). Sans cd/cwd explicite, le shell
        # démarre DANS le dossier mission. Un cd/cwd explicite garde la priorité.
        _mission_dir_b02: Optional[Path] = None
        try:
            _sub_fn_b02 = getattr(ctx, "mission_workspace_subdir", None)
            _mission_sub_b02 = _sub_fn_b02() if callable(_sub_fn_b02) else ""
            if _mission_sub_b02:
                _fg_b02 = getattr(ctx, "file_guardrails", None)
                if _fg_b02 is not None:
                    _mroot_b02 = _fg_b02._workspace_root()
                else:
                    from ...utils.paths import WORKSPACE_DIR as _mroot_b02
                _mdir_b02 = _mroot_b02 / _mission_sub_b02
                if _mdir_b02.is_dir():
                    _mission_dir_b02 = _mdir_b02.resolve()
                    _cwd = str(_mission_dir_b02)
        except Exception:
            pass
        _cd_prefix_m = _re_cd_extract.match(
            r'^\s*cd\s+(?:/d\s+)?(?:"([^"]+)"|\'([^\']+)\'|([^&;]+?))\s*(?:&&|;)\s*(.+)$',
            command, _re_cd_extract.IGNORECASE | _re_cd_extract.DOTALL,
        )
        if _cd_prefix_m:
            _extracted_cwd = next(
                (
                    group.strip()
                    for group in _cd_prefix_m.groups()[:3]
                    if group and group.strip()
                ),
                "",
            )
            _rest_cmd = _cd_prefix_m.group(4).strip()
            if _rest_cmd:
                # 2.11.b : plus jamais de strip silencieux — soit le dossier
                # se résout (absolu, racine Lumena ou workspace), soit échec
                # clair AVANT exécution (exécuter au mauvais endroit = pire).
                _resolved_cd = _resolve_cwd(_extracted_cwd, ctx.lumena_root, _mission_dir_b02)
                if _resolved_cd is None and not cwd:
                    return HandlerResult.ok(
                        f"❌ Répertoire de travail introuvable : {_extracted_cwd} "
                        f"(préfixe `cd`). Commande NON exécutée. "
                        f"Les chemins mission sont relatifs au workspace "
                        f"(ex. missions/<task_id>) — vérifie avec list_directory.",
                        handler_name="run_command",
                    )
                if _resolved_cd is not None:
                    _cwd = _resolved_cd
                command = _rest_cmd

        # Si le LLM passe cwd= explicitement, ça prime sur tout le reste.
        # B0.2 : `cwd='.'` = « ici » = le répertoire par défaut du tour (dossier
        # mission en mission) — pas la racine Lumena.
        if cwd and str(cwd).strip().strip("\"'") in (".", "./", ".\\"):
            logger.info("[run_command] cwd '.' → répertoire par défaut: {}", str(_cwd)[:200])
        elif cwd:
            _resolved_explicit = _resolve_cwd(cwd, ctx.lumena_root, _mission_dir_b02)
            if _resolved_explicit is None:
                return HandlerResult.ok(
                    f"❌ Répertoire de travail introuvable : {cwd} (param cwd=). "
                    f"Commande NON exécutée. Les chemins mission sont relatifs "
                    f"au workspace (ex. missions/<task_id>) — vérifie avec list_directory.",
                    handler_name="run_command",
                )
            _cwd = _resolved_explicit
            logger.info("[run_command] cwd explicite résolu: {}", _cwd[:200])

        _command_lower = command.lower()
        _is_static_server = (
            "http.server" in _command_lower
            or "npx serve" in _command_lower
            or "http-server" in _command_lower
        )
        _is_app_server = (
            "flask run" in _command_lower or "flask_run" in _command_lower
            or "-m flask" in _command_lower or "uvicorn" in _command_lower
            or "gunicorn" in _command_lower or "waitress" in _command_lower
            or "manage.py runserver" in _command_lower or "runserver" in _command_lower
            or "app.py" in _command_lower or "main.py" in _command_lower
            or "wsgi" in _command_lower or "asgi" in _command_lower
        )
        _is_server_command = _is_static_server or _is_app_server

        if background or _should_background_command(command):
            from ...tools.process_manager import get_process_manager

            process_manager = get_process_manager(Path(_cwd) if _cwd else None)
            bg_output, process_id = await process_manager.run_background(
                command=command,
                wait_ms_before_async=800,
                timeout_s=timeout_sec if timeout and timeout > 0 else 60,
            )
            if process_id:
                logger.info("[run_command] background id: {}", process_id)
            elif _is_server_command:
                detail = str(bg_output or "").strip()
                suffix = f"\nSortie du processus :\n{detail}" if detail else ""
                return HandlerResult.fail(
                    "❌ Le serveur demandé s'est terminé immédiatement : aucun "
                    "processus d'arrière-plan n'est actif et aucun port ne peut être "
                    "considéré servi. Utilise `browser_verify_local_project` sur le "
                    "dossier du projet : il détecte et démarre la bonne application, "
                    "puis fournit la preuve navigateur. Ne répète pas cette commande "
                    f"serveur inchangée.{suffix}",
                    handler_name="run_command",
                    status_code="background_server_exited",
                )
            elif not str(bg_output or "").strip():
                bg_output = (
                    "ℹ️ La commande demandée en arrière-plan s'est terminée "
                    "immédiatement sans sortie. Aucun processus n'est encore actif."
                )
            # P1 + LOT E (run CéramiShop) : un serveur lancé en background est
            # enregistré comme preview loopback contrôlée → browser_navigate pourra
            # l'atteindre (SSRF guard). P1 couvrait les serveurs STATIQUES
            # (http.server/npx serve) ; E ajoute les serveurs APPLICATIFS
            # (flask/uvicorn/gunicorn/django/python app.py) — le run CéramiShop est
            # mort ici : `flask run --port 8081` n'était pas reconnu → 127.0.0.1:8081
            # bloqué → vérif navigateur fabriquée. Loopback only ; register_preview
            # REFUSE les ports réservés de Lumena (8080/8245/…).
            try:
                _cl = _command_lower
                # Keep these local names as the established registration
                # boundary: structural regression tests and future server
                # additions inspect this explicit list.
                _is_static_srv = (
                    "http.server" in _cl or "npx serve" in _cl or "http-server" in _cl
                )
                _is_app_srv = (
                    "flask run" in _cl or "flask_run" in _cl or "-m flask" in _cl
                    or "uvicorn" in _cl or "gunicorn" in _cl or "waitress" in _cl
                    or "manage.py runserver" in _cl or "runserver" in _cl
                    or "app.py" in _cl or "main.py" in _cl
                    or "wsgi" in _cl or "asgi" in _cl
                )
                if process_id and (_is_static_srv or _is_app_srv):
                    import re as _re_prev
                    _pm = (
                        _re_prev.search(r'http\.server\s+(\d{4,5})\b', command, _re_prev.IGNORECASE)
                        or _re_prev.search(r'(?:serve|http-server)\s+(\d{4,5})\b', command, _re_prev.IGNORECASE)
                        or _re_prev.search(r'(?:--port|-p|--bind[^\d]{1,3})[ =:](\d{4,5})\b', command, _re_prev.IGNORECASE)
                        or _re_prev.search(r':(\d{4,5})\b', command)  # uvicorn host:port
                    )
                    _port_prev = None
                    if _pm:
                        _port_prev = int(_pm.group(1))
                    elif _is_app_srv:
                        # Serveur applicatif SANS --port explicite → port par défaut du
                        # framework (Flask 5000, uvicorn/django/gunicorn 8000).
                        _port_prev = 5000 if ("flask" in _cl) else 8000
                    if _port_prev is not None:
                        from ...utils.local_preview import register_preview
                        register_preview(
                            _port_prev,
                            workspace=str(_cwd or ""),
                            task_id=str(getattr(ctx, "runtime_task_id", "") or ""),
                        )
            except Exception:
                pass
            return HandlerResult.ok(bg_output, handler_name="run_command")

        # ── Auto-traduction commandes Linux → Windows ──
        import sys as _sys_plat
        if _sys_plat.platform == "win32":
            import re as _re_cmd
            _orig = command
            # && n'est pas valide dans PowerShell 5.1 (Windows PowerShell) → remplacer par ;
            # PowerShell 7+ supporte &&, mais le shell cible est powershell.exe (5.1).
            if ' && ' in command and not command.strip().lower().startswith('cmd'):
                command = command.replace(' && ', '; ')
            # mkdir -p → mkdir (Windows mkdir est récursif nativement)
            command = _re_cmd.sub(r'\bmkdir\s+-p\s+', 'mkdir ', command)
            # tail -N file → powershell Get-Content -Tail N file
            _tail_m = _re_cmd.match(r'^tail\s+-(\d+)\s+(.+)$', command.strip())
            if _tail_m:
                command = f'powershell -NoProfile -Command "Get-Content -Tail {_tail_m.group(1)} -Encoding UTF8 \"{_tail_m.group(2)}\""'
            # head -N file → powershell Get-Content -Head N file
            _head_m = _re_cmd.match(r'^head\s+-(\d+)\s+(.+)$', command.strip())
            if _head_m:
                command = f'powershell -NoProfile -Command "Get-Content -Head {_head_m.group(1)} -Encoding UTF8 \"{_head_m.group(2)}\""'
            # cat file → type file
            if _re_cmd.match(r'^cat\s+', command.strip()):
                command = _re_cmd.sub(r'^cat\s+', 'type ', command.strip())
            # ls → dir
            if command.strip() == 'ls' or _re_cmd.match(r'^ls\s+', command.strip()):
                command = _re_cmd.sub(r'^ls(\s|$)', r'dir\1', command.strip())
            # rm -rf → rmdir /s /q
            _rmrf_m = _re_cmd.match(r'^rm\s+-rf?\s+(.+)$', command.strip())
            if _rmrf_m:
                command = f'rmdir /s /q "{_rmrf_m.group(1).strip()}"'
            # touch file → type nul > file (Windows create empty file)
            _touch_m = _re_cmd.match(r'^touch\s+(.+)$', command.strip())
            if _touch_m:
                command = f'type nul > "{_touch_m.group(1).strip()}"'
            # P4: start <path with spaces> → start "" "<path>"
            # La commande 'start' sous cmd/Windows consomme le 1er argument quoté comme
            # titre de fenêtre. Un path non-quoté avec espaces est tronqué au 1er espace.
            # Ex: "start C:\a\SITE WEB\i.html" → lance "C:\a\SITE" (plante).
            _start_m = _re_cmd.match(r'^start\s+(?!"")(.+)$', command.strip(), _re_cmd.IGNORECASE)
            if _start_m:
                _start_arg = _start_m.group(1).strip()
                # Si pas déjà quoté et contient espace → auto-quote
                if ' ' in _start_arg and not (_start_arg.startswith('"') and _start_arg.rstrip().endswith('"')):
                    command = f'start "" "{_start_arg}"'
                    logger.info("[run_command] auto-quote 'start' avec path espacé → {}", command[:120])
            # Inject -Encoding UTF8 pour les cmdlets fichier PowerShell sans -Encoding
            # (PS 5.1 : Get-Content lit en ANSI, Set-Content/Out-File écrit ANSI/UTF-16)
            # Vérification par segment (→ prochain | ou ;) pour ne pas confondre
            # un -Encoding sur un autre cmdlet dans la même commande chaînée.
            for _ps_cmdlet in ('Get-Content', 'Set-Content', 'Out-File', 'Add-Content'):
                _ps_pat = r'(?i)\b' + _ps_cmdlet + r'\b'
                if _re_cmd.search(_ps_pat, command):
                    def _add_enc(_m, _src=command):
                        seg = _src[_m.end():].split('|')[0].split(';')[0]
                        if _re_cmd.search(r'(?i)-Encoding\b', seg):
                            return _m.group(0)
                        return _m.group(0) + ' -Encoding UTF8'
                    command = _re_cmd.sub(_ps_pat, _add_enc, command)

            if command != _orig:
                logger.info("[run_command] Linux→Windows auto-traduction: {} → {}", _orig[:100], command[:100])

            # FINDSTR: convertir forward slashes en backslashes dans les arguments path
            # (FINDSTR interprète / comme switch separator, pas comme path separator)
            if _re_cmd.search(r'\bfindstr\b', command, _re_cmd.IGNORECASE):
                _findstr_parts = command.split()
                for i, part in enumerate(_findstr_parts):
                    if '/' in part and i > 0 and not part.startswith(('/', '-', '"', "'")):
                        _findstr_parts[i] = part.replace('/', '\\')
                command = ' '.join(_findstr_parts)

        logger.info("[run_command] $ {} (timeout={}s)", command[:200], timeout_sec)

        # ── Sandbox Docker : exécuter dans un container isolé si disponible ──
        if not ide_runtime:
            try:
                from ...utils.docker_sandbox import (
                    is_docker_available, run_in_sandbox, should_use_sandbox,
                    sandbox_error_needs_local_fallback,
                )
                if await is_docker_available() and should_use_sandbox(command):
                    import os as _os_sandbox
                    _sandbox_workdir = _cwd if _cwd and _os_sandbox.path.isdir(_cwd) else str(ctx.lumena_root)
                    output_limit = 4000
                    stdout, stderr, exit_code = await run_in_sandbox(
                        command, _sandbox_workdir, timeout_sec, stdin_input,
                    )
                    logger.info("[cmd_done] sandbox exit:{}", exit_code)
                    output = stdout + (f"\n[STDERR] {stderr}" if stderr.strip() else "")
                    if len(output) > output_limit:
                        summary = await _summarize_large_output(command, output, output_limit)
                        if summary:
                            output = summary
                        else:
                            output = output[:output_limit] + "\n[... tronque ...]"
                    # `-1` : le conteneur n'a meme pas rendu la main (filet externe).
                    # `124` : `timeout` a tue le processus DANS le conteneur — c'est
                    # desormais le cas normal, le sandbox appliquant la borne demandee.
                    from ...utils.docker_sandbox import SANDBOX_TIMEOUT_EXIT_CODE

                    if exit_code in (-1, SANDBOX_TIMEOUT_EXIT_CODE):
                        # La sortie deja produite accompagne le timeout : elle dit
                        # souvent OU la commande s'est arretee. La jeter, c'est
                        # perdre la seule information utile de l'echec.
                        _msg = f"Timeout commande sandbox (>{timeout_sec}s)"
                        if output.strip():
                            _msg += f"\n\n[sortie partielle]\n{output}"
                        return HandlerResult.ok(_msg, handler_name="run_command")
                    # Fallback local si l'outil n'existe pas dans le container,
                    # OU si pytest manque au Docker jetable (ciblé, cf. helper).
                    if exit_code != 0 and sandbox_error_needs_local_fallback(stderr):
                        logger.info("[run_command] sandbox tool missing, fallback local: {}", command[:80])
                    else:
                        if not output and exit_code != 0:
                            return HandlerResult.ok(
                                f"Commande echouee (exit code {exit_code}, pas de sortie)",
                                handler_name="run_command",
                            )
                        return HandlerResult.ok(
                            output if output else "Commande executee (pas de sortie)",
                            handler_name="run_command",
                        )
            except Exception as docker_err:
                logger.warning("[run_command] sandbox fallback: {}", docker_err)

        # ── Fallback : exécution locale (IDE ou Docker indisponible) ──

        # Préparer stdin si fourni (pour processus interactifs)
        stdin_bytes = stdin_input.encode("utf-8") if stdin_input else None

        # Auto-wrap PowerShell : cmdlets Verb-Noun (ex: Select-String, Get-Content, Set-Item...)
        # passés tels quels échoueraient dans cmd.exe sur Windows.
        import re as _re, sys as _sys, subprocess as _subprocess

        # _cwd déjà extrait plus haut (cd /d extraction)

        _cmd_stripped_lower = command.strip().lower()
        _ps_wrap = (
            _sys.platform == "win32"
            and not _cmd_stripped_lower.startswith(("cmd", "python", "py "))
            and bool(_re.search(r"(?:^|[|;&])\s*[A-Z][a-z]+-[A-Z][a-z]+", command))
        )
        if _ps_wrap:
            # Si la commande commence déjà par powershell(.exe) mais SANS -Command/-c,
            # le pipe sera interprété par cmd.exe → réécrire avec -Command
            if _cmd_stripped_lower.startswith("powershell"):
                _ps_body_m = _re.match(r'(?i)powershell(?:\.exe)?\s+', command)
                if _ps_body_m:
                    _rest = command[_ps_body_m.end():]
                    # Seulement réécrire si pas déjà -Command/-c/-File
                    if not _re.match(r'(?i)\s*-(?:Command|c|File)', _rest):
                        _escaped = _rest.replace('"', '\\"')
                        command = f'powershell -NoProfile -NonInteractive -Command "{_escaped}"'
            else:
                _escaped = command.replace('"', '\\"')
                command = f'powershell -NoProfile -NonInteractive -Command "{_escaped}"'

        # ── LOT 2.11.B : garde anti « pytest collecte les 16k tests de Lumena » ──
        # En mission, une pytest lancée depuis la RACINE Lumena avec une cible LARGE
        # (nue, `.`, `tests`, `tests/`) remonte le pytest.ini racine et collecte TOUTE
        # la suite Lumena (16 000+ tests) → timeout, mission cassée (cf. run RomanConv).
        # On refuse AVANT exécution avec un message guidant. Une cible PRÉCISE (un
        # fichier .py listé) reste permise — c'est justement le bon réflexe.
        try:
            if _is_broad_pytest_at_lumena_root(command, _cwd, ctx.lumena_root):
                return HandlerResult.ok(
                    "⛔ pytest large à la RACINE Lumena refusé : ça collecte les "
                    "16 000+ tests du dépôt (timeout garanti). Cible le DOSSIER du "
                    "livrable (cwd=workspace/<projet>) ou un fichier de test précis "
                    "(ex. tests/test_app.py). Commande NON exécutée.",
                    handler_name="run_command",
                )
        except Exception:
            pass

        # ── Tests pytest : forcer l'interpréteur du serveur Lumena (venv, a pytest) ──
        # Évite un `python`/`py` du PATH (System32 / WindowsApps) sans pytest ni deps.
        try:
            from ...utils.docker_sandbox import is_python_test_command
            if is_python_test_command(command):
                # Replacements en lambda (repl non interprété par re.sub → les
                # backslashes du chemin Windows passent tels quels, pas de \\ doublés).
                _exe_q = f'"{_sys.executable}"'
                # `python|py|python3 -m pytest` → `<venv-python> -m pytest`
                command = _re.sub(
                    r'(?i)\b(?:python3?|py)(?:\.exe)?\s+-m\s+pytest\b',
                    lambda _m: f'{_exe_q} -m pytest', command,
                )
                # `pytest …` nu (début ou après séparateur) → `<venv-python> -m pytest`
                command = _re.sub(
                    r'(?i)(^|&&\s*|;\s*|\|\s*)pytest\b',
                    lambda m: f'{m.group(1)}{_exe_q} -m pytest', command,
                )
        except Exception:
            pass

        logger.info("[cmd_start] {}", command[:200])
        output_limit = ctx.ide_command_output_limit() if ide_runtime else 4000

        # Utiliser asyncio.to_thread + Popen au lieu de create_subprocess_shell.
        # create_subprocess_shell exige ProactorEventLoop sur Windows ; après un reload
        # uvicorn l'event loop peut devenir SelectorEventLoop → NotImplementedError silencieuse.
        # Popen dans un thread fonctionne sur tous les event loops.
        # NB: subprocess.run() ne convient PAS pour les timeouts sur Windows car son
        # cleanup interne (kill + communicate) bloque si shell=True laisse des enfants.
        _cmd_for_thread = command
        _stdin_for_thread = stdin_bytes
        _timeout_for_thread = timeout_sec

        def _run_sync():
            import os as _os
            _env = _os.environ.copy()
            _env["PYTHONIOENCODING"] = "utf-8"
            _env["PYTHONUTF8"] = "1"
            _env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
            # 2.11.b : le spawn a son propre try — un cwd invalide ou un
            # exécutable introuvable n'est PAS un timeout et doit être dit
            # tel quel (avant : « Timeout commande (>120s) » en 1 ms).
            try:
                proc = _subprocess.Popen(
                    _cmd_for_thread,
                    shell=True,
                    stdin=_subprocess.PIPE if _stdin_for_thread else None,
                    stdout=_subprocess.PIPE,
                    stderr=_subprocess.PIPE,
                    cwd=_cwd,
                    env=_env,
                    start_new_session=(_sys.platform != "win32"),
                )
            except Exception as _spawn_err:
                return ("CMD_ERROR", f"{type(_spawn_err).__name__}: {_spawn_err}")
            try:
                # Stream stdout line-by-line via loguru for SSE
                if _stdin_for_thread and proc.stdin:
                    proc.stdin.write(_stdin_for_thread)
                    proc.stdin.close()
                import threading as _th
                _stdout_lines, _stderr_lines = [], []

                # M1bis-F1 (run MiniQuiz 2026-07-06) — le timeout était vérifié À
                # L'ARRIVÉE d'une ligne stdout : un petit-fils orphelin (Flask lancé
                # via Start-Process) qui garde le pipe ouvert SANS écrire bloquait
                # `for raw in proc.stdout` pour toujours → jamais de taskkill, jamais
                # de [cmd_done], worker de mission gelé à jamais. Les DEUX flux sont
                # lus par des threads daemon ; le timeout est porté par proc.wait().
                def _read_stream(_stream, _sink, _tag):
                    try:
                        for raw in _stream:
                            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                            _sink.append(line)
                            logger.info("[" + _tag + "] {}", line[:500])
                    except Exception:
                        pass  # pipe fermé par le kill — fin de lecture normale

                _t_err = _th.Thread(
                    target=_read_stream, args=(proc.stderr, _stderr_lines, "cmd_output_err"),
                    daemon=True,
                )
                _t_out = _th.Thread(
                    target=_read_stream, args=(proc.stdout, _stdout_lines, "cmd_output"),
                    daemon=True,
                )
                _t_err.start()
                _t_out.start()

                proc.wait(timeout=_timeout_for_thread)
                _t_out.join(timeout=3)
                _t_err.join(timeout=3)
                # Le process est SORTI mais un enfant détaché tient encore les pipes
                # (les readers vivent) : on n'attend PAS — retour honnête avec note.
                if _t_out.is_alive() or _t_err.is_alive():
                    _stderr_lines.append(
                        "[NOTE] des processus enfants tournent encore en arrière-plan "
                        "et tiennent la sortie — pour un serveur, appelle l'outil "
                        "serve_website(directory='<dossier>', port=8081)."
                    )
                stdout = "\n".join(_stdout_lines).encode("utf-8")
                stderr = "\n".join(_stderr_lines).encode("utf-8")
                return _subprocess.CompletedProcess(
                    proc.args, proc.returncode, stdout, stderr,
                )
            except _subprocess.TimeoutExpired:
                # Tuer l'arbre de processus entier (pas juste cmd.exe)
                if _sys.platform == "win32":
                    _subprocess.call(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        stdout=_subprocess.DEVNULL,
                        stderr=_subprocess.DEVNULL,
                    )
                else:
                    import signal as _signal
                    try:
                        _os.killpg(_os.getpgid(proc.pid), _signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception as e:
                    logger.debug("[cmd] cleanup communicate: %s", e)
                # M1bis-F1 : la sortie déjà collectée accompagne le timeout — le
                # worker VOIT ce qui s'est passé (ex. bannière de démarrage Flask)
                # au lieu d'un timeout muet.
                _partial = "\n".join(_stdout_lines + [f"[STDERR] {l}" for l in _stderr_lines])
                return ("TIMEOUT", _partial)
            except Exception as _run_err:
                # Erreur pendant l'exécution (≠ dépassement) : tuer le process
                # et rapporter honnêtement — plus jamais de faux « Timeout ».
                try:
                    proc.kill()
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                return ("CMD_ERROR", f"{type(_run_err).__name__}: {_run_err}")

        _result = await asyncio.to_thread(_run_sync)

        if _result == "TIMEOUT" or (
            isinstance(_result, tuple) and _result and _result[0] == "TIMEOUT"
        ):
            logger.info("[cmd_done] timeout")
            _partial_out = _result[1] if isinstance(_result, tuple) and len(_result) > 1 else ""
            _msg = (
                f"Timeout commande (>{timeout_sec}s) — arbre de processus tué. "
                "Si tu lançais un SERVEUR : appelle l'outil "
                "serve_website(directory='<dossier>', port=8081)."
            )
            if _partial_out.strip():
                _msg += "\n[SORTIE PARTIELLE]\n" + _partial_out[-1500:]
            return HandlerResult.ok(_msg, handler_name="run_command")

        # 2.11.b : échec de lancement/exécution rendu tel quel (≠ timeout).
        if isinstance(_result, tuple) and _result and _result[0] == "CMD_ERROR":
            logger.info("[cmd_done] erreur exécution: {}", str(_result[1])[:200])
            return HandlerResult.ok(
                f"❌ Échec d'exécution (pas un timeout) : {_result[1]} (cwd={_cwd})",
                handler_name="run_command",
            )

        exit_code = _result.returncode
        logger.info("[cmd_done] exit:{}", exit_code)

        stdout = _result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        stderr = _result.stderr.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        output = stdout + (f"\n[STDERR] {stderr}" if stderr.strip() else "")
        _truncated = len(output) > output_limit
        if _truncated:
            summary = await _summarize_large_output(command, output, output_limit)
            if summary:
                output = summary
            else:
                # Troncature head(60%) + tail(40%) — les dernières lignes contiennent
                # souvent le résumé, exit code, ou résultat final de la commande.
                head_size = output_limit * 3 // 5
                tail_size = output_limit - head_size - 80
                if tail_size > 0 and len(output) > output_limit:
                    _omitted = len(output) - head_size - tail_size
                    output = (
                        output[:head_size]
                        + f"\n\n[... {_omitted} chars omis ...]\n\n"
                        + output[-tail_size:]
                    )
                else:
                    output = output[:output_limit] + "\n[... tronque ...]"
        if not output and exit_code != 0:
            return HandlerResult.ok(
                f"Commande echouee (exit code {exit_code}, pas de sortie)",
                handler_name="run_command",
            )
        # A4/A5 (run FitLog) : le code de sortie n'apparaissait NULLE PART dans
        # l'observation — pytest exit 4 est revenu comme un « ✅ run_command » et
        # le worker a conclu « les tests passent ». Marqueur d'échec EN TÊTE.
        if exit_code != 0 and output:
            output = f"⚠️ ÉCHEC de la commande (exit code {exit_code}) :\n{output}"
        # Z39 (run « SaaS complet » 2026-08-25) — le marqueur A4/A5 ne se posait
        # QUE sur exit != 0. Or `powershell -Command "... | ForEach-Object { php
        # -l $_ }"` rend exit:0 meme quand les 26 iterations echouent : la boucle
        # ne propage pas le code de sortie des commandes natives. Le CodeAgent a
        # donc lu « exit:0 » sur une validation PHP qui n'avait rien valide.
        #
        # On ne devine pas : on ferme le seul cas SANS ambiguite — un executable
        # introuvable n'a jamais rien execute, quel que soit le code rendu.
        elif exit_code == 0 and _command_not_found_in(stderr):
            output = (
                "⚠️ COMMANDE INTROUVABLE malgré exit code 0 — le programme appelé "
                "n'existe pas, RIEN n'a été exécuté ni vérifié. Un code de sortie 0 "
                "rendu par une boucle (ForEach-Object, xargs, ;) ne dit rien des "
                "commandes qu'elle contient.\n"
                f"{output}"
            )
        return HandlerResult.ok(
            output if output else "Commande executee (pas de sortie)",
            handler_name="run_command",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur ({type(e).__name__}): {e}", handler_name="run_command")


async def get_time_handler(ctx: HandlerContext) -> HandlerResult:
    """Retourne la date et l'heure courantes."""
    return HandlerResult.ok(
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        handler_name="get_time",
    )


async def screenshot_tool_handler(ctx: HandlerContext) -> HandlerResult:
    """Prend un screenshot via le module computer_use (FIX #4)."""
    try:
        from ...computer_use import get_computer_use
        cu = get_computer_use()
        path = await cu.take_screenshot()
        if path:
            return HandlerResult.ok(f"✅ Screenshot sauvegardé: {path}", handler_name="screenshot")
        return HandlerResult.ok("❌ Échec du screenshot", handler_name="screenshot")
    except ImportError:
        return HandlerResult.ok("❌ Module computer_use non disponible", handler_name="screenshot")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur screenshot: {e}", handler_name="screenshot")


async def dummy_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Handler placeholder pour les outils non implémentés."""
    return HandlerResult.ok(
        f"Résultat simulé pour: {kwargs}",
        handler_name="dummy",
    )


async def get_token_stats_handler(
    ctx: HandlerContext,
    history: Optional[list] = None,
) -> HandlerResult:
    """Retourne les statistiques de tokens de la conversation.

    Args:
        history: Liste des ReActStep (passé par le wrapper legacy).
                 En standalone, on peut le passer explicitement.
    """
    if not COMPACTION_AVAILABLE:
        return HandlerResult.ok("❌ Module compaction non disponible", handler_name="get_token_stats")
    try:
        steps = history or []
        if steps:
            messages = []
            for step in steps:
                thought = getattr(step, "thought", None)
                observation = getattr(step, "observation", None)
                if thought:
                    content = getattr(thought, "content", str(thought))
                    messages.append({"role": "assistant", "content": content})
                if observation:
                    content = getattr(observation, "content", str(observation))
                    messages.append({"role": "tool", "content": content})
            stats = _get_token_stats_fn(messages)
            return HandlerResult.ok(_format_token_stats_fn(stats), handler_name="get_token_stats")
        else:
            return HandlerResult.ok(
                "📊 Pas d'historique disponible pour les statistiques",
                handler_name="get_token_stats",
            )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur stats: {e}", handler_name="get_token_stats")


async def parallel_tools_handler(
    ctx: HandlerContext,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    execute_fn: Optional[Callable] = None,
) -> HandlerResult:
    """Execute des outils en parallèle de façon contrôlée.

    Args:
        tool_calls: Liste d'appels [{name, args}].
        execute_fn: Callable async (name, args) -> Observation.
                    En mode legacy, c'est ToolRegistry.execute().
                    En mode V2, c'est HandlerRegistryV2.execute() wrappé.
    """
    calls = tool_calls or []
    if not isinstance(calls, list) or not calls:
        return HandlerResult.ok(
            "Erreur: parallel_tools parametre tool_calls invalide ou vide.",
            handler_name="parallel_tools",
        )

    if execute_fn is None:
        return HandlerResult.ok(
            "Erreur: parallel_tools execute_fn non configuré.",
            handler_name="parallel_tools",
        )

    max_calls = int(os.getenv("LUMENA_PARALLEL_TOOL_MAX_CALLS", "20"))
    max_calls = max(1, max_calls)

    # ── Blocklist : seuls les outils qui NE DOIVENT JAMAIS tourner en parallèle ──
    # Lumena est autonome et choisit elle-même quels outils paralléliser.
    # On bloque uniquement ce qui est structurellement dangereux.
    _BLOCKED = {
        "parallel_tools",   # anti-récursion
    }

    # Outils Discord d'écriture : autorisés en parallèle UNIQUEMENT si channel_id différent
    _discord_write_tools = {"discord_send", "discord_send_embed", "discord_send_dm"}

    # Clés réservées pour identifier le nom de l'outil
    _NAME_KEYS = {"name", "tool_name", "tool", "action"}
    _ARGS_KEYS = {"args", "arguments", "tool_args", "parameters", "input", "params"}

    normalized: List[Dict[str, Any]] = []
    for raw in calls[:max_calls]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return HandlerResult.ok(
                    f"Erreur: parallel_tools entree non parsable: {raw[:100]}",
                    handler_name="parallel_tools",
                )
        if not isinstance(raw, dict):
            return HandlerResult.ok(
                "Erreur: parallel_tools chaque entree doit etre un objet {name, args}.",
                handler_name="parallel_tools",
            )

        name = str(
            raw.get("name") or raw.get("tool_name") or raw.get("tool") or raw.get("action") or ""
        ).strip()

        args: Dict[str, Any] = {}
        for akey in _ARGS_KEYS:
            candidate = raw.get(akey)
            if isinstance(candidate, dict) and candidate:
                args = candidate
                break
        if not args:
            flat_args = {k: v for k, v in raw.items() if k not in _NAME_KEYS and k not in _ARGS_KEYS}
            if flat_args:
                args = flat_args
                logger.debug("parallel_tools: args plats détectés pour {}: {}", name, list(flat_args.keys()))

        if not name:
            return HandlerResult.ok("Erreur: parallel_tools nom d'outil manquant.", handler_name="parallel_tools")
        if name in _BLOCKED:
            return HandlerResult.ok(
                f"Erreur: parallel_tools — '{name}' est interdit en parallèle (anti-récursion).",
                handler_name="parallel_tools",
            )
        normalized.append({"name": name, "args": args})

    if len(calls) > max_calls:
        logger.warning("parallel_tools tronque les appels: {} -> {}", len(calls), max_calls)

    # Anti-doublon discord_send : autorisé en parallèle uniquement si channel_id différent
    _discord_channel_seen: set = set()
    for _call in normalized:
        if _call["name"] in _discord_write_tools:
            _cid = str(_call["args"].get("channel_id") or _call["args"].get("user_id") or "")
            if not _cid:
                return HandlerResult.ok(
                    f"Erreur: parallel_tools - {_call['name']} sans channel_id. "
                    "Spécifier un channel_id différent pour chaque appel parallèle.",
                    handler_name="parallel_tools",
                )
            if _cid in _discord_channel_seen:
                return HandlerResult.ok(
                    f"Erreur: parallel_tools - {_call['name']} dupliqué sur channel_id='{_cid}'. "
                    "Même channel → appeler séquentiellement.",
                    handler_name="parallel_tools",
                )
            _discord_channel_seen.add(_cid)

    # Les recherches réseau ont une borne locale: un provider bloqué ne doit
    # jamais retenir les autres sous-appels ni la boucle ReAct. Les autres
    # outils gardent leur durée historique (certains traitements sont longs).
    try:
        _search_timeout_s = float(
            os.getenv("LUMENA_PARALLEL_SEARCH_TIMEOUT_S", "25") or 25
        )
    except (TypeError, ValueError):
        _search_timeout_s = 25.0
    _search_timeout_s = max(0.05, min(120.0, _search_timeout_s))
    _bounded_search_tools = {"web_search", "web_search_brave"}

    async def _execute_one(tc: Dict[str, Any]):
        operation = execute_fn(tc["name"], tc["args"])
        if tc["name"] not in _bounded_search_tools:
            return await operation
        try:
            return await asyncio.wait_for(operation, timeout=_search_timeout_s)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"timeout recherche après {_search_timeout_s:g}s; "
                "change de stratégie sans relancer la même requête en boucle"
            ) from exc

    tasks = [_execute_one(tc) for tc in normalized]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    lines = [f"⚡ parallel_tools: {len(normalized)} appel(s) exécuté(s)"]
    sub_results: List[SubToolResult] = []
    all_success = True

    # Aperçu par sous-résultat : 160 car. coupaient les champs utiles d'un JSON
    # (ex. `current_weather` d'Open-Meteo arrive après ~450 car. d'en-tête) → le
    # modèle ne voyait pas les valeurs et REFAISAIT les appels un par un. On élargit
    # l'aperçu (configurable) avec un budget total borné pour éviter une obs énorme.
    try:
        _preview_cap = max(160, int(os.getenv("LUMENA_PARALLEL_TOOL_PREVIEW_CHARS", "800") or 800))
    except (ValueError, TypeError):
        _preview_cap = 800
    try:
        _total_budget = max(_preview_cap, int(os.getenv("LUMENA_PARALLEL_TOOL_TOTAL_CHARS", "8000") or 8000))
    except (ValueError, TypeError):
        _total_budget = 8000
    _budget_left = _total_budget

    for idx, result in enumerate(results, start=1):
        call = normalized[idx - 1]
        if isinstance(result, Exception):
            sub = SubToolResult(
                tool_name=call["name"],
                success=False,
                content=str(result)[:_preview_cap],
                status_code="exception",
                args=call["args"],
            )
            all_success = False
            lines.append(f"❌ {idx}. {call['name']}: Erreur: {result}")
        else:
            _obs_success = getattr(result, "success", True)
            _obs_content = (getattr(result, "content", str(result)) or "")
            if not _obs_success:
                all_success = False
            # Cap effectif = min(plafond par-résultat, budget restant) — plancher 160
            # pour que chaque résultat reste lisible même budget épuisé.
            _cap = min(_preview_cap, max(160, _budget_left))
            preview = _obs_content.strip().replace("\n", " ")
            if len(preview) > _cap:
                preview = preview[:_cap] + "..."
            _budget_left -= len(preview)
            sub = SubToolResult(
                tool_name=call["name"],
                success=_obs_success,
                content=_obs_content[:_preview_cap],
                status_code="success" if _obs_success else "failed",
                args=call["args"],
            )
            status = "✅" if _obs_success else "❌"
            lines.append(f"{status} {idx}. {call['name']}: {preview}")
        sub_results.append(sub)

    # Phase I-8 (Fix AV) : l'agrégat est un SUCCÈS dès lors que les appels
    # ont été exécutés — l'outil a fait son travail (lancer N appels et
    # rapporter les résultats). Les échecs INDIVIDUELS (403 web, 404...)
    # sont de l'information portée par le contenu et sub_results, pas un
    # échec de parallel_tools. L'ancien `success=all_success` faisait
    # compter chaque agrégat contenant un ❌ comme « échec de l'outil »
    # par le détecteur d'échecs consécutifs de react.py → 2 agrégats avec
    # des 403 de sites web + 1 vraie erreur de format = forçage FINAL, run
    # coupé en plein vol (observé runtime 2026-06-12 10:54, réponse 249
    # chars alors que la recherche MCP avait réussi).
    if all(not s.success for s in sub_results):
        # 0/N : signal réel — tous les sous-appels ont échoué.
        lines.append(
            "⚠️ Tous les sous-appels ont échoué — vérifie les arguments "
            "ou utilise un outil alternatif pour cette cible."
        )
    return HandlerResult(
        success=True,
        output="\n".join(lines),
        handler_name="parallel_tools",
        sub_results=tuple(sub_results),
    )

# ─── Handler: get_recent_src_changes ──────────────────────────────────────────

async def get_recent_src_changes_handler(
    ctx: HandlerContext,
    hours: int = 24,
    extensions: str = ".py",
) -> HandlerResult:
    """Liste les fichiers src/ modifiés dans les dernières N heures.

    Croise avec l'audit log des sub-agents pour distinguer les modifications
    faites PAR Lumena (via write_file/edit_file) des modifications EXTERNES
    (faites par l'utilisateur, Copilot, ou autre).
    """
    import time
    from pathlib import Path
    import json as _json

    try:
        root = Path(__file__).resolve().parents[3]  # racine du projet lumena
        src_dir = root / "src"
        if not src_dir.exists():
            return HandlerResult.ok("❌ Dossier src/ introuvable.", handler_name="get_recent_src_changes")

        exts = {e.strip() for e in extensions.split(",") if e.strip()}
        cutoff = time.time() - hours * 3600
        results = []

        # --- Charger les fichiers que Lumena a elle-même modifiés (audit log) ---
        lumena_modified: set[str] = set()
        try:
            audit_dir = root / "data" / "ops" / "subagent_audit"
            if audit_dir.exists():
                cutoff_dt = datetime.fromtimestamp(cutoff)
                for audit_file in audit_dir.glob("audit_*.jsonl"):
                    try:
                        date_str = audit_file.stem.replace("audit_", "")
                        file_date = datetime.strptime(date_str, "%Y-%m-%d")
                        if file_date.date() < cutoff_dt.date():
                            continue
                    except ValueError:
                        pass
                    try:
                        for line in audit_file.read_text(encoding="utf-8").splitlines():
                            if not line.strip():
                                continue
                            entry = _json.loads(line)
                            tool = entry.get("tool", "")
                            if tool in ("write_file", "edit_file", "edit_own_code",
                                        "str_replace", "create_file"):
                                args = entry.get("args", {})
                                target = args.get("path") or args.get("file_path") or args.get("filename", "")
                                if target:
                                    try:
                                        tp = Path(target)
                                        if tp.is_absolute():
                                            tp = tp.relative_to(root)
                                        lumena_modified.add(str(tp).replace("/", "\\"))
                                    except Exception:
                                        lumena_modified.add(str(target))
                    except Exception:
                        continue
        except Exception:
            pass

        for f in src_dir.rglob("*"):
            if not f.is_file():
                continue
            if exts and f.suffix not in exts:
                continue
            mtime = f.stat().st_mtime
            if mtime >= cutoff:
                rel = f.relative_to(root)
                dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                rel_str = str(rel)
                is_lumena = rel_str.replace("/", "\\") in lumena_modified or rel_str.replace("\\", "/") in lumena_modified
                results.append((mtime, dt, rel_str, is_lumena))

        if not results:
            return HandlerResult.ok(
                f"✅ Aucun fichier src/ modifié dans les dernières {hours}h.",
                handler_name="get_recent_src_changes",
            )

        results.sort(reverse=True)
        n_lumena = sum(1 for r in results if r[3])
        n_externe = len(results) - n_lumena
        lines = [
            f"📁 **Fichiers src/ modifiés dans les dernières {hours}h ({len(results)} fichier(s)):**",
            f"   → {n_lumena} par Lumena, {n_externe} par modification externe (utilisateur/Copilot/autre)\n",
        ]
        for _, dt, path, is_lumena in results:
            tag = "🤖 LUMENA" if is_lumena else "👤 EXTERNE"
            lines.append(f"  `{dt}` [{tag}] — {path}")

        lines.append("\n⚠️ IMPORTANT: Les fichiers marqués [👤 EXTERNE] n'ont PAS été modifiés par toi. "
                     "Ne dis pas 'j'ai modifié' pour des fichiers externes.")

        return HandlerResult.ok("\n".join(lines), handler_name="get_recent_src_changes")

    except Exception as e:
        return HandlerResult.fail(f"Erreur get_recent_src_changes: {e}", handler_name="get_recent_src_changes")

# ─── Registration ──────────────────────────────────────────────────────────

def get_system_handler_defs() -> List[HandlerDef]:
    """Retourne toutes les définitions de handlers system pour le registre V2."""
    return [
        HandlerDef(
            name="run_command",
            description=(
                "Execute une commande shell (avec sanitization). "
                "Si le processus est interactif (demande YES/NO, mot de passe, licence...), "
                "utilise stdin_input pour envoyer la reponse. "
                "Si la commande prend plus de 30s (test reseau, build...), augmente timeout."
            ),
            parameters={
                "properties": {
                    "command": {"type": "string", "description": "Commande a executer"},
                    "stdin_input": {
                        "type": "string",
                        "description": (
                            "Texte a envoyer sur stdin du processus (pour repondre a un prompt interactif). "
                            "Exemple: 'YES\\n' pour accepter une licence. Chaque ligne doit finir par \\n."
                        ),
                        "default": "",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout en secondes (defaut 120, max 600). Augmenter pour installs, speedtest, builds, downloads.",
                        "default": 0,
                    },
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Répertoire de travail pour la commande (chemin absolu). "
                            "Utilise ce paramètre PLUTÔT que 'cd ... && commande' pour éviter "
                            "les problèmes Windows avec les chemins accentués ou les espaces."
                        ),
                        "default": "",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Si true, lance la commande en arriere-plan et rend la main tout de suite. Recommande pour les serveurs locaux.",
                        "default": False,
                    },
                },
                "required": ["command"],
            },
            handler=run_command_handler,
            category="system",
            source_module="handlers.system",
        ),
        HandlerDef(
            name="get_time",
            description="Retourne la date et l'heure courantes.",
            parameters={"properties": {}, "required": []},
            handler=get_time_handler,
            category="system",
            source_module="handlers.system",
        ),
        HandlerDef(
            name="screenshot",
            description="Prend un screenshot de l'ecran.",
            parameters={"properties": {}, "required": []},
            handler=screenshot_tool_handler,
            category="system",
            source_module="handlers.system",
        ),
        HandlerDef(
            name="get_token_stats",
            description="Retourne les statistiques de tokens de la conversation.",
            parameters={"properties": {}, "required": []},
            handler=get_token_stats_handler,
            category="system",
            source_module="handlers.system",
        ),
        HandlerDef(
            name="parallel_tools",
            description="Execute plusieurs outils en parallele (max 6). Format: tool_calls=[{\"name\": \"outil\", \"args\": {...}}]. Outils autorises (lecture seule): read_file, list_directory, find_files, search_in_code, grep_search, view_outline, read_own_code, read_document, memory_search, memory_get, memory_stats, read_journal, get_time, get_agents_status, get_my_capabilities, get_token_stats, get_curiosity_status, list_skills, pip_check, list_backups, web_search, web_search_brave, web_fetch, bg_status, bg_list, process_status, list_tasks, list_scheduled_tasks, task_history, browser_dom_state, browser_get_content, browser_tabs, get_active_window, list_windows, mail_list_messages, mail_list_folders, notion_search, notion_read_page, notion_query_database, spotify_current, github_file_read, github_repo_list. Recursion interdite.",
            parameters={
                "properties": {
                    "tool_calls": {
                        "type": "array",
                        "description": "Liste d'appels: [{\"name\": \"read_file\", \"args\": {\"path\": \"x.py\"}}]. Max 6 appels. Outils lecture seule uniquement.",
                    },
                },
                "required": ["tool_calls"],
            },
            handler=parallel_tools_handler,
            category="system",
            source_module="handlers.system",
        ),
        HandlerDef(
            name="get_recent_src_changes",
            description=(
                "Liste les fichiers du code source (src/) modifiés récemment, "
                "en distinguant ceux modifiés PAR Lumena (via ses outils) de ceux "
                "modifiés par l'utilisateur ou un outil externe (Copilot, éditeur…). "
                "⚠️ Ne dis JAMAIS 'j'ai modifié X' pour un fichier marqué EXTERNE. "
                "Paramètre 'hours' = nombre d'heures à remonter (défaut 24). "
                "Paramètre 'extensions' = extensions à filtrer, séparées par virgule (défaut '.py')."
            ),
            parameters={
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "Nombre d'heures à remonter dans le temps (défaut: 24)",
                        "default": 24,
                    },
                    "extensions": {
                        "type": "string",
                        "description": "Extensions de fichiers à inclure, séparées par virgule. Ex: '.py' ou '.py,.md'",
                        "default": ".py",
                    },
                },
                "required": [],
            },
            handler=get_recent_src_changes_handler,
            category="system",
            source_module="handlers.system",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
