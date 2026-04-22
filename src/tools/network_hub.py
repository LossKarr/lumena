"""
Lumena — Network Hub
=====================
Agent réseau complet : scan, contrôle à distance, exécution de commandes,
transfert de fichiers, power control, port scan.

Utilise WinRM pour les machines Windows et SSH pour Linux/macOS.
Tous les appareils du réseau local peuvent être découverts et contrôlés.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import struct
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from ..utils.persistence import atomic_write_json, safe_read_json

# ─────────────────────────────────────────────────────────────
# REGISTRE DES MACHINES CONNUES
# Lumena mémorise automatiquement les machines découvertes.
# ─────────────────────────────────────────────────────────────
from src.utils.paths import NETWORK_REGISTRY_JSON as _REGISTRY_PATH


def _load_registry() -> dict:
    return safe_read_json(_REGISTRY_PATH, default={"hosts": {}, "last_scan": None})


def _save_registry(reg: dict) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_REGISTRY_PATH, reg)


# ─────────────────────────────────────────────────────────────
# UTILITAIRES RÉSEAU
# ─────────────────────────────────────────────────────────────

def _get_local_subnet() -> str:
    """Détecte automatiquement le sous-réseau local (ex: 192.168.1.0/24)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        parts = local_ip.rsplit(".", 1)
        return f"{parts[0]}.0/24"
    except Exception:
        return "192.168.1.0/24"  # fallback réseau par défaut


def _ping(ip: str, timeout: float = 0.5) -> bool:
    """Ping rapide une IP. Retourne True si en ligne."""
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip],
            capture_output=True, timeout=timeout + 1
        )
        return result.returncode == 0
    except Exception:
        return False  # hôte injoignable


def _resolve_hostname(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""  # résolution hostname impossible


def _get_mac_from_arp(ip: str) -> str:
    """Récupère l'adresse MAC via la table ARP Windows."""
    try:
        result = subprocess.run(["arp", "-a", ip], capture_output=True, text=True, timeout=3)
        for line in result.stdout.splitlines():
            if ip in line:
                parts = line.split()
                for p in parts:
                    if "-" in p and len(p) == 17:
                        return p.upper()
    except Exception:
        pass  # parsing MAC impossible, on continue
    return ""


def _scan_ports(ip: str, ports: list[int], timeout: float = 0.3) -> list[int]:
    """Scan une liste de ports TCP. Retourne les ports ouverts."""
    open_ports = []
    for port in ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((ip, port)) == 0:
                    open_ports.append(port)
        except Exception:
            pass  # port fermé ou timeout
    return open_ports


def _detect_os(open_ports: list[int]) -> str:
    """Devine l'OS à partir des ports ouverts."""
    if 3389 in open_ports or 5985 in open_ports or 445 in open_ports:
        return "Windows"
    if 22 in open_ports:
        return "Linux/macOS"
    if 62078 in open_ports:
        return "iOS"
    if 5555 in open_ports:
        return "Android"
    return "Inconnu"


# ─────────────────────────────────────────────────────────────
# WAKE-ON-LAN
# ─────────────────────────────────────────────────────────────

def _send_wol(mac: str) -> bool:
    """Envoie un magic packet Wake-on-LAN."""
    try:
        mac_clean = mac.replace(":", "").replace("-", "").replace(".", "")
        if len(mac_clean) != 12:
            return False
        magic = bytes.fromhex("FF" * 6 + mac_clean * 16)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(magic, ("<broadcast>", 9))
        return True
    except Exception as e:
        logger.error(f"WoL failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# EXÉCUTION À DISTANCE — WINRM (Windows)
# ─────────────────────────────────────────────────────────────

async def _winrm_exec(ip: str, command: str, username: str, password: str) -> tuple[bool, str]:
    """Exécute une commande PowerShell sur une machine Windows via WinRM."""
    try:
        import winrm  # pip install pywinrm
        session = winrm.Session(
            f"http://{ip}:5985/wsman",
            auth=(username, password),
            transport="ntlm",
        )
        result = session.run_ps(command)
        output = (result.std_out or b"").decode("utf-8", errors="replace")
        error = (result.std_err or b"").decode("utf-8", errors="replace")
        if result.status_code == 0:
            return True, output.strip()
        else:
            return False, f"Erreur ({result.status_code}): {error.strip()}"
    except ImportError:
        # Fallback : PowerShell natif via Invoke-Command
        try:
            # Sécurisation: single-quote escaping pour éviter l'injection de commandes
            _safe_ip = ip.replace("'", "''")
            _safe_user = username.replace("'", "''")
            _safe_pass = password.replace("'", "''")
            _safe_cmd = command.replace("'", "''")
            ps_cmd = (
                f"Invoke-Command -ComputerName '{_safe_ip}' -Credential "
                f"(New-Object PSCredential('{_safe_user}',"
                f"(ConvertTo-SecureString '{_safe_pass}' -AsPlainText -Force))) "
                f"-ScriptBlock ([ScriptBlock]::Create('{_safe_cmd}'))"
            )
            result = await asyncio.create_subprocess_exec(
                "powershell", "-Command", ps_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=30)
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            if result.returncode == 0:
                return True, out
            return False, err or out
        except Exception as e:
            return False, str(e)
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# EXÉCUTION À DISTANCE — SSH (Linux/macOS)
# ─────────────────────────────────────────────────────────────

async def _ssh_exec(ip: str, command: str, username: str, password: str = "", key_path: str = "") -> tuple[bool, str]:
    """Exécute une commande via SSH."""
    try:
        import asyncssh  # pip install asyncssh
        connect_kwargs: dict = {"host": ip, "username": username, "known_hosts": None}
        if key_path:
            connect_kwargs["client_keys"] = [key_path]
        elif password:
            connect_kwargs["password"] = password
        async with asyncssh.connect(**connect_kwargs) as conn:
            result = await conn.run(command, timeout=30)
            if result.returncode == 0:
                return True, (result.stdout or "").strip()
            return False, (result.stderr or result.stdout or "").strip()
    except ImportError:
        # Fallback : ssh natif en subprocess
        try:
            ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10"]
            if key_path:
                ssh_cmd += ["-i", key_path]
            ssh_cmd += [f"{username}@{ip}", command]
            result = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=35)
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            if result.returncode == 0:
                return True, out
            return False, err or out
        except Exception as e:
            return False, str(e)
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# CREDENTIALS — chargés depuis .env ou le registre
# ─────────────────────────────────────────────────────────────

def _get_credentials(ip: str) -> tuple[str, str, str]:
    """
    Retourne (username, password, key_path) pour une IP.
    Cherche dans :
    1. Le registre réseau (machines déjà connues)
    2. Les variables d'env NETWORK_USER / NETWORK_PASS
    """
    reg = _load_registry()
    host_info = reg["hosts"].get(ip, {})
    username = host_info.get("username") or os.getenv("NETWORK_USER", os.getenv("USERNAME", ""))
    password = host_info.get("password") or os.getenv("NETWORK_PASS", "")
    key_path = host_info.get("key_path") or os.getenv("NETWORK_KEY", "")
    return username, password, key_path


# ─────────────────────────────────────────────────────────────
# HANDLERS (appelés par tool_system)
# ─────────────────────────────────────────────────────────────

async def handle_network_scan(**kwargs) -> str:
    """
    Scanne le réseau local et découvre tous les appareils actifs.
    Retourne IP, hostname, MAC, OS détecté, ports ouverts.
    """
    subnet = kwargs.get("subnet", "") or _get_local_subnet()
    fast = kwargs.get("fast", True)

    KEY_PORTS = [22, 80, 135, 139, 443, 445, 3389, 5985, 8080, 8443]

    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return f"Subnet invalide : {subnet}"

    results = []
    tasks = []

    loop = asyncio.get_running_loop()

    async def probe(ip_str: str):
        alive = await loop.run_in_executor(None, _ping, ip_str, 0.5)
        if not alive:
            return
        hostname = await loop.run_in_executor(None, _resolve_hostname, ip_str)
        mac = await loop.run_in_executor(None, _get_mac_from_arp, ip_str)
        open_ports = await loop.run_in_executor(None, _scan_ports, ip_str, KEY_PORTS, 0.3)
        os_guess = _detect_os(open_ports)
        results.append({
            "ip": ip_str,
            "hostname": hostname,
            "mac": mac,
            "os": os_guess,
            "open_ports": open_ports,
            "last_seen": datetime.now().isoformat(),
        })

    hosts = list(network.hosts())
    if fast and len(hosts) > 100:
        hosts = hosts[:100]

    batch_size = 50
    total_batches = (len(hosts) + batch_size - 1) // batch_size
    for i in range(0, len(hosts), batch_size):
        batch = hosts[i:i + batch_size]
        batch_num = i // batch_size + 1
        logger.info("[cmd_output] 📡 Scan batch {}/{} ({}-{})",
                     batch_num, total_batches,
                     str(batch[0]), str(batch[-1]))
        await asyncio.gather(*[probe(str(ip)) for ip in batch])
        logger.info("[cmd_output] ✅ Batch {}/{} — {} appareils trouvés jusqu'ici",
                     batch_num, total_batches, len(results))

    results.sort(key=lambda x: list(map(int, x["ip"].split("."))))

    # Sauvegarder dans le registre
    reg = _load_registry()
    reg["last_scan"] = datetime.now().isoformat()
    for r in results:
        ip = r["ip"]
        existing = reg["hosts"].get(ip, {})
        existing.update({k: v for k, v in r.items() if v})
        reg["hosts"][ip] = existing
    _save_registry(reg)

    if not results:
        return f"Aucun appareil trouvé sur {subnet}"

    lines = [f"🔍 Scan réseau {subnet} — {len(results)} appareils trouvés\n"]
    for r in results:
        ports_str = ", ".join(str(p) for p in r["open_ports"]) if r["open_ports"] else "aucun"
        lines.append(
            f"  📡 {r['ip']:16s}  {r['os']:12s}  "
            f"hostname={r['hostname'] or '?'}  mac={r['mac'] or '?'}  "
            f"ports=[{ports_str}]"
        )
    return "\n".join(lines)


async def handle_network_exec(**kwargs) -> str:
    """
    Exécute une commande sur une machine distante du réseau.
    Utilise WinRM pour Windows, SSH pour Linux/macOS.
    """
    ip = kwargs.get("ip", "").strip()
    command = kwargs.get("command", "").strip()
    username = kwargs.get("username", "").strip()
    password = kwargs.get("password", "").strip()
    key_path = kwargs.get("key_path", "").strip()

    if not ip or not command:
        return "ip et command sont requis"

    # Charger credentials depuis registre si non fournis
    if not username:
        username, auto_pass, auto_key = _get_credentials(ip)
        if not password:
            password = auto_pass
        if not key_path:
            key_path = auto_key

    # Détecter l'OS depuis le registre
    reg = _load_registry()
    host_info = reg["hosts"].get(ip, {})
    os_type = host_info.get("os", "")
    open_ports = host_info.get("open_ports", [])

    # Choisir le protocole
    use_ssh = (22 in open_ports) or os_type in ("Linux/macOS",)
    use_winrm = (5985 in open_ports or 3389 in open_ports) or os_type == "Windows"

    if use_winrm and not use_ssh:
        ok, output = await _winrm_exec(ip, command, username, password)
    elif use_ssh:
        ok, output = await _ssh_exec(ip, command, username, password, key_path)
    else:
        # Essayer WinRM d'abord, puis SSH
        ok, output = await _winrm_exec(ip, command, username, password)
        if not ok:
            ok, output = await _ssh_exec(ip, command, username, password, key_path)

    status = "✓" if ok else "✗"
    return f"{status} [{ip}] {command}\n{output}"


async def handle_network_info(**kwargs) -> str:
    """
    Retourne les informations détaillées sur un appareil du réseau
    (processus en cours, disques, RAM, CPU, utilisateurs connectés).
    """
    ip = kwargs.get("ip", "").strip()
    if not ip:
        return "ip requis"

    reg = _load_registry()
    host_info = reg["hosts"].get(ip, {})
    os_type = host_info.get("os", "")
    open_ports = host_info.get("open_ports", [])

    if os_type == "Linux/macOS" or 22 in open_ports:
        cmd = "uname -a; uptime; free -h; df -h /; who"
    else:
        cmd = (
            "Write-Host '=== SYSTÈME ===' ; "
            "Get-ComputerInfo | Select-Object CsName,OsName,TotalPhysicalMemory | Format-List ; "
            "Write-Host '=== CPU ===' ; "
            "Get-WmiObject Win32_Processor | Select-Object Name,LoadPercentage ; "
            "Write-Host '=== DISQUES ===' ; "
            "Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free ; "
            "Write-Host '=== UTILISATEURS ===' ; "
            "query user 2>$null"
        )

    username, password, key_path = _get_credentials(ip)
    if os_type == "Linux/macOS" or 22 in open_ports:
        ok, output = await _ssh_exec(ip, cmd, username, password, key_path)
    else:
        ok, output = await _winrm_exec(ip, cmd, username, password)

    if not ok:
        # Retourne au moins ce qu'on sait du registre
        return (
            f"Impossible de se connecter à {ip}\n"
            f"Infos registre : {json.dumps(host_info, ensure_ascii=False, indent=2)}"
        )

    return f"📊 Infos système {ip} ({os_type})\n{output}"


async def handle_network_list(**kwargs) -> str:
    """
    Liste tous les appareils connus dans le registre réseau de Lumena.
    """
    reg = _load_registry()
    hosts = reg.get("hosts", {})
    last_scan = reg.get("last_scan", "jamais")

    if not hosts:
        return "Aucun appareil connu. Lance d'abord network_scan."

    lines = [f"📋 Registre réseau — {len(hosts)} appareils connus (dernier scan: {last_scan})\n"]
    for ip, info in sorted(hosts.items(), key=lambda x: list(map(int, x[0].split(".")))):
        ports = ", ".join(str(p) for p in info.get("open_ports", [])) or "?"
        lines.append(
            f"  {ip:16s}  {info.get('os','?'):12s}  "
            f"{info.get('hostname','?')}  "
            f"ports=[{ports}]  "
            f"vu={info.get('last_seen','?')[:16]}"
        )
    return "\n".join(lines)


async def handle_network_wol(**kwargs) -> str:
    """
    Allume une machine éteinte via Wake-on-LAN (nécessite l'adresse MAC).
    """
    mac = kwargs.get("mac", "").strip()
    ip = kwargs.get("ip", "").strip()

    if not mac and ip:
        reg = _load_registry()
        mac = reg["hosts"].get(ip, {}).get("mac", "")

    if not mac:
        return "Adresse MAC requise pour Wake-on-LAN"

    ok = _send_wol(mac)
    if ok:
        return f"✓ Magic packet WoL envoyé à {mac} — attends 30-60s que la machine démarre"
    return f"✗ Échec de l'envoi WoL à {mac}"


async def handle_network_shutdown(**kwargs) -> str:
    """
    Éteint ou redémarre une machine distante.
    action: 'shutdown' | 'restart' | 'sleep'
    """
    ip = kwargs.get("ip", "").strip()
    action = kwargs.get("action", "shutdown").strip()

    if not ip:
        return "ip requis"

    reg = _load_registry()
    host_info = reg["hosts"].get(ip, {})
    os_type = host_info.get("os", "")
    open_ports = host_info.get("open_ports", [])
    username, password, key_path = _get_credentials(ip)

    if os_type == "Linux/macOS" or 22 in open_ports:
        cmds = {"shutdown": "sudo shutdown -h now", "restart": "sudo reboot", "sleep": "sudo systemctl suspend"}
        cmd = cmds.get(action, "sudo shutdown -h now")
        ok, out = await _ssh_exec(ip, cmd, username, password, key_path)
    else:
        cmds = {
            "shutdown": "Stop-Computer -Force",
            "restart": "Restart-Computer -Force",
            "sleep": "(Add-Type -Assembly System.Windows.Forms); [System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)",
        }
        cmd = cmds.get(action, "Stop-Computer -Force")
        ok, out = await _winrm_exec(ip, cmd, username, password)

    status = "✓" if ok else "✗"
    return f"{status} {action} envoyé à {ip}\n{out}"


async def handle_network_set_credentials(**kwargs) -> str:
    """
    Enregistre les credentials d'une machine dans le registre réseau.
    Ces credentials seront réutilisés automatiquement pour les prochaines connexions.
    """
    ip = kwargs.get("ip", "").strip()
    username = kwargs.get("username", "").strip()
    password = kwargs.get("password", "").strip()
    key_path = kwargs.get("key_path", "").strip()
    label = kwargs.get("label", "").strip()

    if not ip:
        return "ip requis"

    reg = _load_registry()
    host = reg["hosts"].get(ip, {})
    if username:
        host["username"] = username
    if password:
        host["password"] = password
    if key_path:
        host["key_path"] = key_path
    if label:
        host["label"] = label
    reg["hosts"][ip] = host
    _save_registry(reg)

    return f"✓ Credentials enregistrés pour {ip} ({label or username})"


async def handle_network_port_scan(**kwargs) -> str:
    """
    Scan détaillé des ports d'une machine spécifique.
    """
    ip = kwargs.get("ip", "").strip()
    port_range = kwargs.get("port_range", "1-1024").strip()

    if not ip:
        return "ip requis"

    try:
        if "-" in port_range:
            start, end = map(int, port_range.split("-"))
            ports = list(range(start, min(end + 1, 65536)))
        else:
            ports = [int(p) for p in port_range.split(",")]
    except ValueError:
        return f"Format port_range invalide : {port_range} (ex: '1-1024' ou '22,80,443')"

    if len(ports) > 2000:
        ports = ports[:2000]

    # Scan async par lots pour progression en temps réel (évite le run_in_executor silencieux)
    open_ports: list[int] = []
    sem = asyncio.Semaphore(150)

    async def _check_port(port: int) -> None:
        async with sem:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port), timeout=0.4
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception as e:
                    logger.debug("[network] wait_closed port %s: %s", port, e)
                open_ports.append(port)
            except Exception as e:
                logger.debug("[network] probe port %s: %s", port, e)

    progress_every = max(50, len(ports) // 10)
    tasks = []
    for idx, port in enumerate(ports, 1):
        tasks.append(_check_port(port))
        if idx % progress_every == 0 or idx == len(ports):
            await asyncio.gather(*tasks)
            tasks = []
            pct = idx * 100 // len(ports)
            logger.info("[cmd_output] 🔌 {} ports scannés ({}/{}) — {} ouverts",
                         pct, idx, len(ports), len(open_ports))

    open_ports.sort()

    # Services connus
    SERVICES = {
        21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
        80: "HTTP", 110: "POP3", 135: "RPC", 139: "NetBIOS", 143: "IMAP",
        443: "HTTPS", 445: "SMB", 993: "IMAPS", 995: "POP3S",
        1433: "MSSQL", 3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL",
        5985: "WinRM", 6379: "Redis", 8080: "HTTP-Alt", 8443: "HTTPS-Alt",
        27017: "MongoDB",
    }

    if not open_ports:
        return f"Aucun port ouvert trouvé sur {ip} (plage : {port_range})"

    lines = [f"🔌 Ports ouverts sur {ip} ({len(open_ports)} trouvés)\n"]
    for p in open_ports:
        svc = SERVICES.get(p, "")
        lines.append(f"  {p:6d}  {svc}")

    # Mise à jour registre
    reg = _load_registry()
    if ip in reg["hosts"]:
        reg["hosts"][ip]["open_ports"] = open_ports
        _save_registry(reg)

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# TRANSFERT DE FICHIERS — helpers internes
# ─────────────────────────────────────────────────────────────

async def _winrm_upload(ip: str, local_path: str, remote_path: str, username: str, password: str) -> tuple[bool, str]:
    """Upload un fichier local vers une machine Windows via WinRM (Base64)."""
    try:
        data = Path(local_path).read_bytes()
        import base64
        b64 = base64.b64encode(data).decode("ascii")
        # Découper en chunks de 32 Ko pour éviter les limites WinRM
        chunk_size = 32_000
        chunks = [b64[i:i + chunk_size] for i in range(0, len(b64), chunk_size)]
        # Écrire chunk par chunk
        def _ps_escape(s: str) -> str:
            """Échappe quotes/backticks/$ pour éviter injection PowerShell."""
            return s.replace("`", "``").replace('"', '`"').replace("$", "`$").replace("\\", "\\\\")
        remote_safe = _ps_escape(remote_path)
        # Vider/créer le fichier
        ok, out = await _winrm_exec(ip, f'[System.IO.File]::WriteAllBytes("{remote_safe}", [byte[]]@())', username, password)
        for chunk in chunks:
            ps = (
                f'$bytes = [System.Convert]::FromBase64String("{chunk}"); '
                f'$fs = [System.IO.File]::Open("{remote_safe}", [System.IO.FileMode]::Append); '
                f'$fs.Write($bytes, 0, $bytes.Length); $fs.Close()'
            )
            ok, out = await _winrm_exec(ip, ps, username, password)
            if not ok:
                return False, f"Échec chunk upload : {out}"
        return True, f"Fichier uploadé ({len(data)} octets)"
    except Exception as e:
        return False, str(e)


async def _ssh_upload(ip: str, local_path: str, remote_path: str, username: str, password: str, key_path: str) -> tuple[bool, str]:
    """Upload un fichier local vers une machine SSH via SFTP ou scp."""
    size = os.path.getsize(local_path)
    try:
        import asyncssh
        connect_kwargs: dict = {"host": ip, "username": username, "known_hosts": None}
        if key_path:
            connect_kwargs["client_keys"] = [key_path]
        elif password:
            connect_kwargs["password"] = password
        async with asyncssh.connect(**connect_kwargs) as conn:
            async with conn.start_sftp_client() as sftp:
                await sftp.put(local_path, remote_path)
        return True, f"Fichier uploadé ({size} octets)"
    except ImportError:
        # Fallback scp natif
        try:
            scp_cmd = ["scp", "-o", "StrictHostKeyChecking=no"]
            if key_path:
                scp_cmd += ["-i", key_path]
            scp_cmd += [local_path, f"{username}@{ip}:{remote_path}"]
            result = await asyncio.create_subprocess_exec(
                *scp_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(result.communicate(), timeout=60)
            if result.returncode == 0:
                return True, f"Fichier uploadé ({size} octets)"
            return False, stderr.decode(errors="replace").strip()
        except Exception as e:
            return False, str(e)
    except Exception as e:
        return False, str(e)


async def _winrm_download(ip: str, remote_path: str, local_path: str, username: str, password: str) -> tuple[bool, str]:
    """Download un fichier depuis une machine Windows en Base64."""
    try:
        import base64
        def _ps_escape(s: str) -> str:
            return s.replace("`", "``").replace('"', '`"').replace("$", "`$").replace("\\", "\\\\")
        remote_path_ps = _ps_escape(remote_path)
        ps = f'[System.Convert]::ToBase64String([System.IO.File]::ReadAllBytes("{remote_path_ps}"))'
        ok, b64 = await _winrm_exec(ip, ps, username, password)
        if not ok:
            return False, b64
        data = base64.b64decode(b64.strip())
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_bytes(data)
        return True, f"Fichier téléchargé ({len(data)} octets)"
    except Exception as e:
        return False, str(e)


async def _ssh_download(ip: str, remote_path: str, local_path: str, username: str, password: str, key_path: str) -> tuple[bool, str]:
    """Download un fichier depuis une machine SSH via SFTP ou scp."""
    try:
        import asyncssh
        connect_kwargs: dict = {"host": ip, "username": username, "known_hosts": None}
        if key_path:
            connect_kwargs["client_keys"] = [key_path]
        elif password:
            connect_kwargs["password"] = password
        async with asyncssh.connect(**connect_kwargs) as conn:
            async with conn.start_sftp_client() as sftp:
                await sftp.get(remote_path, local_path)
        size = os.path.getsize(local_path)
        return True, f"Fichier téléchargé ({size} octets)"
    except ImportError:
        try:
            scp_cmd = ["scp", "-o", "StrictHostKeyChecking=no"]
            if key_path:
                scp_cmd += ["-i", key_path]
            scp_cmd += [f"{username}@{ip}:{remote_path}", local_path]
            result = await asyncio.create_subprocess_exec(
                *scp_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(result.communicate(), timeout=60)
            if result.returncode == 0:
                size = os.path.getsize(local_path)
                return True, f"Fichier téléchargé ({size} octets)"
            return False, stderr.decode(errors="replace").strip()
        except Exception as e:
            return False, str(e)
    except Exception as e:
        return False, str(e)


def _resolve_os_and_creds(ip: str) -> tuple[str, list[int], str, str, str]:
    """Retourne (os_type, open_ports, username, password, key_path) depuis le registre."""
    reg = _load_registry()
    host_info = reg["hosts"].get(ip, {})
    os_type = host_info.get("os", "")
    open_ports = host_info.get("open_ports", [])
    username, password, key_path = _get_credentials(ip)
    return os_type, open_ports, username, password, key_path


def _is_linux(os_type: str, open_ports: list[int]) -> bool:
    return os_type == "Linux/macOS" or (22 in open_ports and 5985 not in open_ports)


# ─────────────────────────────────────────────────────────────
# HANDLERS PUBLICS — Transfert de fichiers
# ─────────────────────────────────────────────────────────────

async def handle_network_file_upload(**kwargs) -> str:
    """
    Envoie un fichier local vers une machine distante.
    local_path : chemin local (ex: C:/data/config.txt)
    remote_path : chemin distant (ex: C:/Users/bob/config.txt ou /home/bob/config.txt)
    ip : IP de la machine cible
    """
    ip = kwargs.get("ip", "").strip()
    local_path = kwargs.get("local_path", "").strip()
    remote_path = kwargs.get("remote_path", "").strip()

    if not ip or not local_path or not remote_path:
        return "ip, local_path et remote_path sont requis"

    if not Path(local_path).exists():
        return f"Fichier local introuvable : {local_path}"

    os_type, open_ports, username, password, key_path = _resolve_os_and_creds(ip)

    if _is_linux(os_type, open_ports):
        ok, msg = await _ssh_upload(ip, local_path, remote_path, username, password, key_path)
    else:
        ok, msg = await _winrm_upload(ip, local_path, remote_path, username, password)
        if not ok and (22 in open_ports):
            ok, msg = await _ssh_upload(ip, local_path, remote_path, username, password, key_path)

    status = "✓" if ok else "✗"
    return f"{status} Upload {local_path} → {ip}:{remote_path}\n{msg}"


async def handle_network_file_download(**kwargs) -> str:
    """
    Télécharge un fichier depuis une machine distante vers le PC local.
    remote_path : chemin sur la machine distante
    local_path : chemin de destination local
    ip : IP de la machine source
    """
    ip = kwargs.get("ip", "").strip()
    remote_path = kwargs.get("remote_path", "").strip()
    local_path = kwargs.get("local_path", "").strip()

    if not ip or not remote_path or not local_path:
        return "ip, remote_path et local_path sont requis"

    os_type, open_ports, username, password, key_path = _resolve_os_and_creds(ip)

    if _is_linux(os_type, open_ports):
        ok, msg = await _ssh_download(ip, remote_path, local_path, username, password, key_path)
    else:
        ok, msg = await _winrm_download(ip, remote_path, local_path, username, password)
        if not ok and (22 in open_ports):
            ok, msg = await _ssh_download(ip, remote_path, local_path, username, password, key_path)

    status = "✓" if ok else "✗"
    return f"{status} Download {ip}:{remote_path} → {local_path}\n{msg}"


async def handle_network_file_edit(**kwargs) -> str:
    """
    Crée ou remplace le contenu d'un fichier sur une machine distante.
    Utile pour modifier des configs, scripts, etc. à distance.
    ip : IP cible
    remote_path : chemin du fichier à créer/modifier
    content : contenu textuel à écrire dans le fichier
    """
    ip = kwargs.get("ip", "").strip()
    remote_path = kwargs.get("remote_path", "").strip()
    content = kwargs.get("content", "")

    if not ip or not remote_path:
        return "ip et remote_path sont requis"

    os_type, open_ports, username, password, key_path = _resolve_os_and_creds(ip)

    # Écrire dans un fichier local temporaire puis uploader
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp", delete=False) as tf:
        tf.write(content)
        tmp_local = tf.name

    try:
        if _is_linux(os_type, open_ports):
            ok, msg = await _ssh_upload(ip, tmp_local, remote_path, username, password, key_path)
        else:
            ok, msg = await _winrm_upload(ip, tmp_local, remote_path, username, password)
            if not ok and (22 in open_ports):
                ok, msg = await _ssh_upload(ip, tmp_local, remote_path, username, password, key_path)
    finally:
        try:
            os.unlink(tmp_local)
        except Exception:
            pass  # fichier temp cleanup best-effort

    status = "✓" if ok else "✗"
    return f"{status} Fichier écrit sur {ip}:{remote_path} ({len(content)} caractères)\n{msg}"


async def handle_network_file_list(**kwargs) -> str:
    """
    Liste les fichiers dans un répertoire d'une machine distante.
    ip : IP cible
    remote_path : répertoire à lister (ex: C:/Users/bob/ ou /home/bob/)
    """
    ip = kwargs.get("ip", "").strip()
    remote_path = kwargs.get("remote_path", "").strip()

    if not ip or not remote_path:
        return "ip et remote_path sont requis"

    os_type, open_ports, username, password, key_path = _resolve_os_and_creds(ip)

    import shlex
    if _is_linux(os_type, open_ports):
        safe_path = shlex.quote(remote_path)
        cmd = f'ls -lah {safe_path} 2>&1'
        ok, out = await _ssh_exec(ip, cmd, username, password, key_path)
    else:
        def _ps_esc(s: str) -> str:
            return s.replace("`", "``").replace('"', '`"').replace("$", "`$").replace("\\", "\\\\")
        remote_path_ps = _ps_esc(remote_path)
        cmd = f'Get-ChildItem -Path "{remote_path_ps}" | Format-Table Name,Length,LastWriteTime,Mode -AutoSize | Out-String'
        ok, out = await _winrm_exec(ip, cmd, username, password)
        if not ok and (22 in open_ports):
            safe_path = shlex.quote(remote_path)
            cmd = f'ls -lah {safe_path} 2>&1'
            ok, out = await _ssh_exec(ip, cmd, username, password, key_path)

    status = "✓" if ok else "✗"
    return f"{status} Listing {ip}:{remote_path}\n{out}"


# ─────────────────────────────────────────────────────────────
# HANDLER — Auto-déploiement de Lumena sur une machine distante
# ─────────────────────────────────────────────────────────────

async def handle_network_self_deploy(**kwargs) -> str:
    """
    Copie Lumena (elle-même) sur une machine distante et la lance.
    La machine cible doit avoir Python 3.10+ installé.
    ip : IP de la machine cible
    remote_dir : répertoire de destination (ex: C:/lumena ou /opt/lumena)
    launch : True pour démarrer Lumena après le déploiement (défaut: False)
    exclude : liste de dossiers/fichiers à exclure (défaut: .git, __pycache__, models, data/chromadb)
    """
    ip = kwargs.get("ip", "").strip()
    remote_dir = kwargs.get("remote_dir", "").strip()
    launch = kwargs.get("launch", False)

    if not ip or not remote_dir:
        return "ip et remote_dir sont requis"

    os_type, open_ports, username, password, key_path = _resolve_os_and_creds(ip)

    LUMENA_ROOT = Path(__file__).parent.parent.parent.resolve()

    # Dossiers/fichiers à exclure du zip
    EXCLUDE = {
        ".git", "__pycache__", ".venv", "venv", "node_modules",
        "models", "data/chromadb", "data/browser_profiles",
        "data/screenshots", "data/training", "data/training_retrain",
        ".mypy_cache", ".pytest_cache",
    }

    import tempfile, zipfile
    with tempfile.NamedTemporaryFile(suffix="_lumena_deploy.zip", delete=False) as _zf:
        zip_path = Path(_zf.name)

    try:
        # Créer le ZIP
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in LUMENA_ROOT.rglob("*"):
                if file.is_dir():
                    continue
                rel = file.relative_to(LUMENA_ROOT)
                parts = set(rel.parts)
                if parts & EXCLUDE:
                    continue
                # Exclure .pyc et gros binaires
                if file.suffix in (".pyc", ".pyo", ".exe", ".dll", ".so", ".glb", ".png", ".jpg", ".jpeg"):
                    continue
                zf.write(file, rel)

        zip_size_mb = zip_path.stat().st_size / 1_048_576
        lines = [f"📦 Archive Lumena créée : {zip_path.name} ({zip_size_mb:.1f} Mo)"]

        import shlex
        def _ps_esc_deploy(s: str) -> str:
            return s.replace("`", "``").replace('"', '`"').replace("$", "`$").replace("\\", "\\\\")

        if _is_linux(os_type, open_ports):
            safe_dir = shlex.quote(remote_dir)
            remote_zip = f"{remote_dir.rstrip('/')}/lumena_deploy.zip"
            # Créer répertoire cible
            await _ssh_exec(ip, f'mkdir -p {safe_dir}', username, password, key_path)
            ok, msg = await _ssh_upload(ip, str(zip_path), remote_zip, username, password, key_path)
            if not ok:
                return f"✗ Échec upload ZIP : {msg}"
            lines.append(f"✓ ZIP uploadé sur {ip}:{remote_zip}")
            # Décompresser
            ok, msg = await _ssh_exec(ip, f'cd {safe_dir} && unzip -o lumena_deploy.zip && rm lumena_deploy.zip', username, password, key_path)
            lines.append(f"{'✓' if ok else '✗'} Décompression : {msg[:120]}")
            # Installer dépendances
            ok, msg = await _ssh_exec(ip, f'cd {safe_dir} && pip install -r requirements.txt -q', username, password, key_path)
            lines.append(f"{'✓' if ok else '✗'} Installation deps : {('OK' if ok else msg[:120])}")
            if launch:
                ok, msg = await _ssh_exec(ip, f'cd {safe_dir} && nohup python run_telegram.py > lumena.log 2>&1 &', username, password, key_path)
                lines.append(f"{'✓' if ok else '✗'} Lumena Telegram lancée : {msg[:80]}")
                ok2, msg2 = await _ssh_exec(ip, f'cd {safe_dir} && nohup python run_whatsapp.py >> lumena.log 2>&1 &', username, password, key_path)
                lines.append(f"{'✓' if ok2 else '✗'} Lumena WhatsApp lancée : {msg2[:80]}")
        else:
            # Windows
            remote_zip = remote_dir.rstrip("\\") + "\\lumena_deploy.zip"
            remote_dir_ps = _ps_esc_deploy(remote_dir)
            await _winrm_exec(ip, f'New-Item -ItemType Directory -Force -Path "{remote_dir_ps}" | Out-Null', username, password)
            ok, msg = await _winrm_upload(ip, str(zip_path), remote_zip, username, password)
            if not ok:
                return f"✗ Échec upload ZIP : {msg}"
            lines.append(f"✓ ZIP uploadé sur {ip}:{remote_zip}")
            # Décompresser
            remote_zip_ps = _ps_esc_deploy(remote_zip)
            ok, msg = await _winrm_exec(
                ip,
                f'Expand-Archive -Path "{remote_zip_ps}" -DestinationPath "{remote_dir_ps}" -Force; '
                f'Remove-Item "{remote_zip_ps}"',
                username, password
            )
            lines.append(f"{'✓' if ok else '✗'} Décompression : {msg[:120]}")
            # Installer dépendances
            ok, msg = await _winrm_exec(ip, f'cd "{remote_dir_ps}"; pip install -r requirements.txt -q', username, password)
            lines.append(f"{'✓' if ok else '✗'} Installation deps : {('OK' if ok else msg[:120])}")
            if launch:
                ok, msg = await _winrm_exec(
                    ip,
                    f'Start-Process python -ArgumentList "{remote_dir_ps}\\run_telegram.py" -WorkingDirectory "{remote_dir_ps}" -WindowStyle Hidden',
                    username, password
                )
                lines.append(f"{'\u2713' if ok else '\u2717'} Lumena Telegram lanc\u00e9e en arri\u00e8re-plan")
                ok2, msg2 = await _winrm_exec(
                    ip,
                    f'Start-Process python -ArgumentList "{remote_dir_ps}\\run_whatsapp.py" -WorkingDirectory "{remote_dir_ps}" -WindowStyle Hidden',
                    username, password
                )
                lines.append(f"{'\u2713' if ok2 else '\u2717'} Lumena WhatsApp lanc\u00e9e en arri\u00e8re-plan")

    finally:
        try:
            zip_path.unlink()
        except Exception:
            pass  # zip cleanup best-effort

    return "\n".join(lines)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
