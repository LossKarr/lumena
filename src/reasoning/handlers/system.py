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
from .contracts import HandlerResult
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

async def run_command_handler(
    ctx: HandlerContext, command: str,
    stdin_input: str = "", timeout: int = 0,
) -> HandlerResult:
    """Execute une commande shell de manière asynchrone (non-bloquante)."""
    try:
        ide_runtime = ctx.is_ide_runtime()
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
        _cd_prefix_m = _re_cd_extract.match(
            r'^cd\s+/d\s+"([^"]+)"\s*(?:&&|;)\s*(.*)',
            command, _re_cd_extract.IGNORECASE | _re_cd_extract.DOTALL,
        )
        if _cd_prefix_m:
            _extracted_cwd = _cd_prefix_m.group(1).strip()
            _rest_cmd = _cd_prefix_m.group(2).strip()
            if _rest_cmd:
                import os as _os_cd
                if _os_cd.path.isdir(_extracted_cwd):
                    _cwd = _extracted_cwd
                command = _rest_cmd

        # ── Auto-traduction commandes Linux → Windows ──
        import sys as _sys_plat
        if _sys_plat.platform == "win32":
            import re as _re_cmd
            _orig = command
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
                from ...utils.docker_sandbox import is_docker_available, run_in_sandbox, should_use_sandbox
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
                    if exit_code == -1:
                        return HandlerResult.ok(
                            f"Timeout commande sandbox (>{timeout_sec}s)", handler_name="run_command",
                        )
                    # Fallback local si l'outil n'existe pas dans le container
                    if exit_code != 0 and ("not found" in (stderr or "").lower() or "no such file" in (stderr or "").lower()):
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
            try:
                import os as _os
                _env = _os.environ.copy()
                _env["PYTHONIOENCODING"] = "utf-8"
                _env["PYTHONUTF8"] = "1"
                _env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
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
                try:
                    # Stream stdout line-by-line via loguru for SSE
                    if _stdin_for_thread and proc.stdin:
                        proc.stdin.write(_stdin_for_thread)
                        proc.stdin.close()
                    import threading as _th, time as _time
                    _stdout_lines, _stderr_lines = [], []
                    _start = _time.monotonic()

                    def _read_stderr():
                        for raw in proc.stderr:
                            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                            _stderr_lines.append(line)
                            logger.info("[cmd_output_err] {}", line[:500])

                    _t = _th.Thread(target=_read_stderr, daemon=True)
                    _t.start()

                    for raw in proc.stdout:
                        if _time.monotonic() - _start > _timeout_for_thread:
                            raise _subprocess.TimeoutExpired(_cmd_for_thread, _timeout_for_thread)
                        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                        _stdout_lines.append(line)
                        logger.info("[cmd_output] {}", line[:500])

                    proc.wait(timeout=max(5, _timeout_for_thread - (_time.monotonic() - _start)))
                    _t.join(timeout=3)
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
                        import os as _os, signal as _signal
                        try:
                            _os.killpg(_os.getpgid(proc.pid), _signal.SIGKILL)
                        except (ProcessLookupError, OSError):
                            proc.kill()
                    try:
                        proc.communicate(timeout=5)
                    except Exception as e:
                        logger.debug("[cmd] cleanup communicate: %s", e)
                    return "TIMEOUT"
            except Exception:
                return "TIMEOUT"

        _result = await asyncio.to_thread(_run_sync)

        if _result == "TIMEOUT":
            logger.info("[cmd_done] timeout")
            return HandlerResult.ok(
                f"Timeout commande (>{timeout_sec}s)", handler_name="run_command",
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

    # Execute en parallèle via la fonction fournie
    tasks = [execute_fn(tc["name"], tc["args"]) for tc in normalized]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    lines = [f"⚡ parallel_tools: {len(normalized)} appel(s) exécuté(s)"]
    for idx, result in enumerate(results, start=1):
        call = normalized[idx - 1]
        if isinstance(result, Exception):
            lines.append(f"❌ {idx}. {call['name']}: Erreur: {result}")
        else:
            success = getattr(result, "success", True)
            content = getattr(result, "content", str(result))
            status = "✅" if success else "❌"
            preview = (content or "").strip().replace("\n", " ")
            if len(preview) > 160:
                preview = preview[:160] + "..."
            lines.append(f"{status} {idx}. {call['name']}: {preview}")

    return HandlerResult.ok("\n".join(lines), handler_name="parallel_tools")

# ─── Handler: get_recent_src_changes ──────────────────────────────────────────

async def get_recent_src_changes_handler(
    ctx: HandlerContext,
    hours: int = 24,
    extensions: str = ".py",
) -> HandlerResult:
    """Liste les fichiers src/ modifiés dans les dernières N heures."""
    import time
    from pathlib import Path

    try:
        root = Path(__file__).resolve().parents[3]  # racine du projet lumena
        src_dir = root / "src"
        if not src_dir.exists():
            return HandlerResult.ok("❌ Dossier src/ introuvable.", handler_name="get_recent_src_changes")

        exts = {e.strip() for e in extensions.split(",") if e.strip()}
        cutoff = time.time() - hours * 3600
        results = []

        for f in src_dir.rglob("*"):
            if not f.is_file():
                continue
            if exts and f.suffix not in exts:
                continue
            mtime = f.stat().st_mtime
            if mtime >= cutoff:
                rel = f.relative_to(root)
                dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                results.append((mtime, dt, str(rel)))

        if not results:
            return HandlerResult.ok(
                f"✅ Aucun fichier src/ modifié dans les dernières {hours}h.",
                handler_name="get_recent_src_changes",
            )

        results.sort(reverse=True)
        lines = [f"📁 **Fichiers src/ modifiés dans les dernières {hours}h ({len(results)} fichier(s)):**\n"]
        for _, dt, path in results:
            lines.append(f"  `{dt}` — {path}")

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
                "Liste les fichiers du code source (src/) modifiés récemment. "
                "Utile pour savoir si Lumena a modifié son propre code, quels fichiers ont changé et quand. "
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
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
