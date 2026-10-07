r"""Lot MCP-1 - un MCP livre comme EXECUTABLE LOCAL peut enfin etre catalogue.

--- Ce que Charles a demande, et pourquoi ca echouait ---

« Installe le MCP Roblox Studio. » Lumena a repondu honnetement : `add_mcp` echoue avec
`mcp_action_failed`, deux fois, et elle a refuse d'annoncer une installation sans preuve.

**Son diagnostic etait exact.** Mesure au code :

- `install_orchestrator._parse_package_spec` ne connait que `npm:`, `pypi:`, `local:`
- `local:` est mappe vers le runner **`uv`** (l.199), donc prefixe par `python.exe` :
  inutilisable pour un binaire
- `local_creation_executor` sert a CREER un MCP Python, pas a enregistrer un existant
- le formulaire du panel n'envoie que `{server_id, display_name, package_spec,
  owner_profile}` — **aucun champ `command`/`args`**
- chaque entree du catalogue porte un `integrity_hmac` : l'editer a la main est exclu

**Et pourtant le runner SAIT deja lancer un binaire arbitraire** : dans `_build_start_command`,
la branche npm fait `return list(self.spec.args)` — la commande entiere est remplacee, et le
commentaire « Fix AY » le dit explicitement. La plomberie existait ; **c'est la porte
d'entree du catalogue qui manquait.**

--- Ce que la sonde du serveur Roblox a mesure ---

Le `mcp.bat` fourni par Roblox n'est qu'un wrapper de trois lignes autour d'un binaire :

    C:\Users\charl\AppData\Local\Roblox\Versions\version-<hash>\StudioMCP.exe

Sonde directe de ce binaire, requete `initialize` JSON-RPC :

    {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05",
     "capabilities":{"tools":{"listChanged":true}},
     "serverInfo":{"name":"RobloxStudio","version":"1.0.0"},
     "instructions":"Studio MCP Proxy - bridges MCP clients with Roblox Studio"}}

Donc **pas besoin de `cmd.exe`, ni de `&&`, ni du `.bat`** : un chemin d'executable suffit.
C'est ce qui rend ce lot petit ET sur — aucune ligne de commande shell n'entre au catalogue.

--- Les gardes du catalogue, deliberes, qui restent intacts ---

`_PKG_FORBIDDEN_GLOBAL` interdit globalement l'antislash, l'espace, `;`, `&`, `|`, les
guillemets et `$` ; un garde separe refuse les lettres de lecteur Windows ; `..` est banni.
Ces verrous existent precisement pour empecher d'injecter un chemin ou une commande
arbitraire. **Ce lot n'en desserre aucun** : le transport `exe:` s'ecrit avec des SLASHES,
ce qui le fait passer sans toucher au garde global.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp.server_catalog import CatalogError, _validate_package_spec

EXE_ROBLOX = ("exe:C:/Users/charl/AppData/Local/Roblox/Versions/"
              "version-806df14cc29f4bef/StudioMCP.exe")


# -- 1. Le cas qui fonde le lot ---------------------------------------------

def test_le_chemin_roblox_mesure_est_accepte():
    """Le `package_spec` exact que Lumena devra poser pour Roblox Studio."""
    _validate_package_spec(EXE_ROBLOX)      # ne doit pas lever


@pytest.mark.parametrize("spec", [
    "exe:C:/Program Files/Truc/serveur.exe",
    "exe:D:/outils/mcp-server.exe",
    "exe:C:/a/b/c/d/e/serveur_mcp.exe",
])
def test_des_chemins_d_executable_valides_passent(spec):
    _validate_package_spec(spec)


# -- 2. Les gardes de securite, qui ne doivent PAS ceder --------------------

@pytest.mark.parametrize("spec", [
    "exe:C:\\Users\\charl\\StudioMCP.exe",          # antislash : garde global
    "exe:C:/Users/../Windows/System32/cmd.exe",     # remontee de chemin
    "exe:C:/a/b.exe; del C:/x",                     # point-virgule
    "exe:C:/a/b.exe && autre.exe",                  # chainage shell
    "exe:C:/a/b.exe | tee",                         # tube
    'exe:C:/a/"b".exe',                             # guillemets
    "exe:C:/a/$env.exe",                            # expansion
    "exe:C:/a/b.exe\t--argument",                   # tabulation
])
def test_les_formes_dangereuses_restent_refusees(spec):
    """Aucun verrou existant n'est desserre par ce lot."""
    with pytest.raises(CatalogError):
        _validate_package_spec(spec)


@pytest.mark.parametrize("spec", [
    "exe:",                              # vide
    "exe:serveur.exe",                   # relatif : pas de racine
    "exe:/usr/bin/serveur",              # pas d'extension executable
    "exe:C:/a/b.txt",                    # extension non executable
    "exe:C:/a/b.bat",                    # .bat : on veut le BINAIRE, pas un wrapper shell
    "exe:C:/a/b.cmd",
    "exe:C:/a/b.ps1",
])
def test_seul_un_executable_absolu_est_accepte(spec):
    """Le lot n'ouvre PAS la porte aux scripts shell : `.bat`, `.cmd` et `.ps1` sont
    refuses. C'est exactement ce que la sonde a rendu inutile — le `.bat` de Roblox
    n'etait qu'un wrapper, le binaire se suffit."""
    with pytest.raises(CatalogError):
        _validate_package_spec(spec)


# -- 3. Ce que le lot ne doit PAS changer ----------------------------------

@pytest.mark.parametrize("spec", [
    "npm:@scope/paquet", "npm:paquet-simple",
    "pypi:windows-mcp", "pypi:Paquet_Python",
    "local:mon-serveur",
])
def test_les_transports_existants_sont_intacts(spec):
    _validate_package_spec(spec)


@pytest.mark.parametrize("spec", [
    "npm:PAQUET-MAJUSCULE",          # npm impose les minuscules
    "pypi:paquet/avec-slash",        # pypi n'accepte pas de slash
    "local:Slug-Majuscule",
    "C:/chemin/sans/transport.exe",  # garde lettre de lecteur
    "transport-inconnu:truc",
])
def test_les_refus_existants_le_restent(spec):
    with pytest.raises(CatalogError):
        _validate_package_spec(spec)


# -- 4. Le parseur et le mapping de transport ------------------------------

def test_le_parseur_reconnait_exe():
    from src.mcp.install_orchestrator import InstallTransport, _parse_package_spec

    resultat = _parse_package_spec(EXE_ROBLOX)
    assert resultat is not None, "`exe:` n'est pas parse : add_mcp echouera comme avant"
    transport, chemin = resultat
    assert transport == InstallTransport.EXE
    assert chemin.endswith("StudioMCP.exe")


def test_le_parseur_garde_npm_pypi_local():
    """Caracterisation : le lot AJOUTE un transport, il n'en modifie aucun."""
    from src.mcp.install_orchestrator import InstallTransport, _parse_package_spec

    assert _parse_package_spec("npm:truc") == (InstallTransport.NPM, "truc")
    assert _parse_package_spec("pypi:truc") == (InstallTransport.PYPI, "truc")
    assert _parse_package_spec("local:truc") == (InstallTransport.LOCAL, "truc")
    assert _parse_package_spec("inconnu:truc") is None
    assert _parse_package_spec("") is None


def test_exe_ne_passe_PAS_par_le_runner_python():
    """`local:` est mappe vers `uv`, donc prefixe par `python.exe` — c'est exactement ce
    qui rendait un binaire inutilisable. `exe:` doit avoir son propre transport runner."""
    from src.mcp.install_orchestrator import (
        InstallTransport, _transport_to_runner_transport,
    )

    runner = _transport_to_runner_transport(InstallTransport.EXE)
    assert runner not in ("uv", "npm"), (
        f"`exe` mappe vers {runner!r} : le binaire serait prefixe ou declencherait "
        "une installation reseau"
    )


# -- 5. Le runner : rien a installer, et la commande est le binaire --------

def _exe_runner(tmp_path: Path, *, exists: bool = True):
    from src.mcp.sandbox_runner import MCPInstallSpec, MCPSandboxRunner

    executable = tmp_path / "Program Files" / "Vendor" / "server.exe"
    if exists:
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"MZ")
    spec = MCPInstallSpec(
        name="local-host",
        transport="exe",
        package=str(executable),
    )
    return MCPSandboxRunner(
        spec,
        mcp_root=tmp_path / "mcp",
        logs_dir=tmp_path / "logs",
    ), executable


def test_le_runner_n_installe_rien_pour_un_exe(tmp_path):
    """Le binaire est deja sur le disque : toute tentative d'installation serait au
    mieux inutile, au pire une sortie reseau non desiree."""
    from src.mcp.sandbox_runner import ProcessState

    runner, _ = _exe_runner(tmp_path)
    runner.install()
    assert runner.state() == ProcessState.INSTALLED
    assert runner.is_installed()
    assert not (runner.server_dir / ".venv").exists()
    assert not (runner.server_dir / "node_modules").exists()


def test_le_runner_lance_le_binaire_tel_quel(tmp_path):
    runner, executable = _exe_runner(tmp_path)
    assert runner._build_start_command() == [str(executable)]


def test_le_runner_refuse_un_executable_absent(tmp_path):
    from src.mcp.sandbox_runner import MCPSandboxError

    runner, _ = _exe_runner(tmp_path, exists=False)
    with pytest.raises(MCPSandboxError, match="missing"):
        runner.install()
    assert not runner.is_installed()


def test_exe_ne_peut_pas_remplacer_la_commande_par_args(tmp_path):
    from src.mcp.sandbox_runner import MCPInstallSpec, MCPSandboxError, MCPSandboxRunner

    executable = tmp_path / "server.exe"
    executable.write_bytes(b"MZ")
    spec = MCPInstallSpec(
        name="local-host",
        transport="exe",
        package=str(executable),
        args=["cmd.exe", "/c", "whoami"],
    )
    with pytest.raises(MCPSandboxError, match="command replacement"):
        MCPSandboxRunner(spec, mcp_root=tmp_path / "mcp", logs_dir=tmp_path / "logs")
