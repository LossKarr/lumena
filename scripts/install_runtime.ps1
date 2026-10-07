[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir,

    [string]$BundledPythonInstaller = "",

    [string]$BundledWebViewInstaller = "",

    [string]$BundledVCRedistInstaller = "",

    [string]$LogFile = "",

    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$AppDir = [System.IO.Path]::GetFullPath($AppDir)
if (-not $LogFile) {
    $LogFile = Join-Path $AppDir "data\logs\installer.log"
}
$LogFile = [System.IO.Path]::GetFullPath($LogFile)
$logDirectory = Split-Path -Parent $LogFile
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
[System.IO.File]::WriteAllText($LogFile, "", (New-Object System.Text.UTF8Encoding($false)))

function Write-InstallLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    [Console]::Out.WriteLine($line)
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($line + [Environment]::NewLine)
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        $stream = $null
        try {
            $stream = New-Object System.IO.FileStream(
                $LogFile,
                [System.IO.FileMode]::Append,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::ReadWrite
            )
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush()
            return
        } catch [System.IO.IOException] {
            if ($attempt -eq 20) { throw }
            Start-Sleep -Milliseconds 50
        } finally {
            if ($null -ne $stream) { $stream.Dispose() }
        }
    }
}

function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int[]]$AllowedExitCodes = @(0)
    )

    & $FilePath @Arguments 2>&1 | ForEach-Object {
        Write-InstallLog ([string]$_)
    }
    $exitCode = $LASTEXITCODE
    if ($AllowedExitCodes -notcontains $exitCode) {
        throw "La commande '$([System.IO.Path]::GetFileName($FilePath))' a echoue (code $exitCode)."
    }
}

function Test-CompatiblePython {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    try {
        $result = & $Path -c "import struct,sys; print('LUMENA_PY_OK' if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 'NO')" 2>$null
        return ($LASTEXITCODE -eq 0 -and ($result -contains "LUMENA_PY_OK"))
    } catch {
        return $false
    }
}

function Resolve-CompatiblePython {
    $candidates = New-Object System.Collections.Generic.List[string]
    $candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"))
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles "Python312\python.exe"))
    }

    try {
        $pyLauncher = Get-Command py.exe -ErrorAction Stop
        $resolved = & $pyLauncher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1
        if ($LASTEXITCODE -eq 0 -and $resolved) {
            $candidates.Add(([string]$resolved).Trim())
        }
    } catch { }

    try {
        $pythonCommand = Get-Command python.exe -ErrorAction Stop
        $candidates.Add($pythonCommand.Source)
    } catch { }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-CompatiblePython -Path $candidate) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }
    return $null
}

function Install-BundledPython {
    if (-not $BundledPythonInstaller -or -not (Test-Path -LiteralPath $BundledPythonInstaller -PathType Leaf)) {
        throw "Python 3.12 x64 est absent et son installeur embarque est introuvable."
    }

    $signature = Get-AuthenticodeSignature -LiteralPath $BundledPythonInstaller
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        throw "La signature de l'installeur Python embarque est invalide."
    }

    $target = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"
    Write-InstallLog "Installation de Python 3.12 x64..."
    $process = Start-Process -FilePath $BundledPythonInstaller -ArgumentList @(
        "/quiet",
        "InstallAllUsers=0",
        ('TargetDir="{0}"' -f $target),
        "PrependPath=0",
        "Include_test=0",
        "Include_doc=0",
        "Include_launcher=1",
        "Shortcuts=0"
    ) -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "L'installation de Python a echoue (code $($process.ExitCode))."
    }

    $python = Join-Path $target "python.exe"
    if (-not (Test-CompatiblePython -Path $python)) {
        throw "Python a ete installe mais Python 3.12 x64 reste introuvable."
    }
    Write-InstallLog "Python 3.12 x64 installe et verifie."
    return $python
}

function Test-WebView2Runtime {
    $clientId = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    $keys = @(
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$clientId",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$clientId",
        "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$clientId"
    )
    return [bool]($keys | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)
}

function Install-WebView2Runtime {
    if (Test-WebView2Runtime) {
        Write-InstallLog "WebView2 Runtime detecte."
        return
    }
    if (-not $BundledWebViewInstaller -or -not (Test-Path -LiteralPath $BundledWebViewInstaller -PathType Leaf)) {
        throw "WebView2 Runtime est absent et son installeur embarque est introuvable."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $BundledWebViewInstaller
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Microsoft") {
        throw "La signature de l'installeur WebView2 embarque est invalide."
    }
    Write-InstallLog "Installation de Microsoft WebView2 Runtime..."
    $process = Start-Process -FilePath $BundledWebViewInstaller -ArgumentList @("/silent", "/install") -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "L'installation de WebView2 a echoue (code $($process.ExitCode))."
    }
    if (-not (Test-WebView2Runtime)) {
        throw "WebView2 a ete installe mais son runtime reste introuvable."
    }
    Write-InstallLog "WebView2 Runtime installe et verifie."
}

function Test-VCRuntime {
    $keys = @(
        "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
    )
    foreach ($key in $keys) {
        try {
            $runtime = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
            if ([int]$runtime.Installed -eq 1 -and [int]$runtime.Major -ge 14) {
                return $true
            }
        } catch { }
    }
    return $false
}

function Resolve-VCRedistInstaller {
    if ($BundledVCRedistInstaller -and (Test-Path -LiteralPath $BundledVCRedistInstaller -PathType Leaf)) {
        return [System.IO.Path]::GetFullPath($BundledVCRedistInstaller)
    }
    $repositoryCandidate = Join-Path $AppDir "installer\deps\vc_redist.x64.exe"
    if (Test-Path -LiteralPath $repositoryCandidate -PathType Leaf) {
        return $repositoryCandidate
    }
    return ""
}

function Invoke-VCRedist {
    param([switch]$Repair)

    $installer = Resolve-VCRedistInstaller
    if (-not $installer) {
        throw "Microsoft Visual C++ Runtime x64 est absent et son installeur embarque est introuvable."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Microsoft") {
        throw "La signature du runtime Microsoft Visual C++ embarque est invalide."
    }

    $operation = if ($Repair) { "/repair" } else { "/install" }
    $vcLog = Join-Path (Split-Path -Parent $LogFile) "vc_redist.log"
    $label = if ($Repair) { "Reparation" } else { "Installation" }
    Write-InstallLog "$label du runtime Microsoft Visual C++ x64..."
    $process = Start-Process -FilePath $installer -ArgumentList @(
        $operation,
        "/quiet",
        "/norestart",
        "/log",
        ('"{0}"' -f $vcLog)
    ) -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010) -and -not (Test-VCRuntime)) {
        throw "Le runtime Microsoft Visual C++ x64 n'a pas pu etre installe (code $($process.ExitCode))."
    }
    if (-not (Test-VCRuntime)) {
        throw "Le runtime Microsoft Visual C++ x64 reste introuvable apres installation."
    }
    Write-InstallLog "Runtime Microsoft Visual C++ x64 verifie."
}

function Install-VCRuntime {
    if (Test-VCRuntime) {
        Write-InstallLog "Runtime Microsoft Visual C++ x64 detecte."
        return
    }
    Invoke-VCRedist
}

function Test-ChromaRuntime {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    $probeDirectory = Join-Path $env:TEMP ("lumena-chroma-probe-" + [guid]::NewGuid().ToString("N"))
    $probeScript = Join-Path $env:TEMP ("lumena-chroma-probe-" + [guid]::NewGuid().ToString("N") + ".py")
    $probe = @'
import pathlib
import sys

import chromadb
import chromadb_rust_bindings  # noqa: F401

root = pathlib.Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
client = chromadb.PersistentClient(path=str(root))
collection = client.get_or_create_collection("lumena_installer_probe")
# Fournir le vecteur rend la preuve strictement hors ligne. Sans `embeddings`,
# Chroma telecharge son modele ONNX par defaut sur une machine neuve, ce qui
# transformait une verification de DLL en dependance reseau implicite.
collection.add(ids=["probe"], embeddings=[[1.0, 0.0, 0.0]], documents=["Lumena"])
if collection.count() != 1:
    raise RuntimeError("ChromaDB read/write probe failed")
print("CHROMADB_RUNTIME_OK")
'@
    try {
        # Windows PowerShell 5.1 retire les guillemets internes d'un argument
        # multiligne transmis a `python -c`. Un fichier temporaire preserve le
        # programme exact et rend la sonde identique sur PowerShell 5 et 7.
        [System.IO.File]::WriteAllText(
            $probeScript,
            $probe,
            (New-Object System.Text.UTF8Encoding($false))
        )
        # Ne pas utiliser `& ... 2>&1` ici. Sous Windows PowerShell 5.1 et
        # ErrorActionPreference=Stop, une ligne ecrite par Python sur stderr
        # devient parfois une erreur PowerShell fatale. La branche de
        # diagnostic/reparation etait alors court-circuitee et le journal ne
        # conservait meme pas l'exception native.
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $PythonPath
        $startInfo.Arguments = ('"{0}" "{1}"' -f $probeScript, $probeDirectory)
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            Write-InstallLog "La sonde ChromaDB n'a pas pu demarrer Python."
            return $false
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $exitCode = $process.ExitCode
        $process.Dispose()

        foreach ($line in @($stdout, $stderr)) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                foreach ($entry in ($line -split "`r?`n")) {
                    if (-not [string]::IsNullOrWhiteSpace($entry)) {
                        Write-InstallLog $entry
                    }
                }
            }
        }
        return ($exitCode -eq 0 -and $stdout -match "(?m)^CHROMADB_RUNTIME_OK\s*$")
    } finally {
        if (Test-Path -LiteralPath $probeDirectory) {
            for ($attempt = 1; $attempt -le 10; $attempt++) {
                try {
                    Remove-Item -LiteralPath $probeDirectory -Recurse -Force -ErrorAction Stop
                    break
                } catch {
                    if ($attempt -eq 10) {
                        Write-InstallLog "Nettoyage differe de la sonde ChromaDB: $probeDirectory"
                    } else {
                        Start-Sleep -Milliseconds 100
                    }
                }
            }
        }
        if (Test-Path -LiteralPath $probeScript -PathType Leaf) {
            Remove-Item -LiteralPath $probeScript -Force -ErrorAction SilentlyContinue
        }
    }
}

function Assert-ChromaRuntime {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    Write-InstallLog "Verification du moteur de memoire ChromaDB..."
    if (Test-ChromaRuntime -PythonPath $PythonPath) {
        Write-InstallLog "Moteur de memoire ChromaDB verifie."
        return
    }

    Write-InstallLog "ChromaDB natif indisponible : tentative de reparation du runtime Visual C++."
    Invoke-VCRedist -Repair
    if (-not (Test-ChromaRuntime -PythonPath $PythonPath)) {
        throw "Le moteur ChromaDB ne peut pas charger ses DLL natives apres reparation du runtime Visual C++."
    }
    Write-InstallLog "Moteur de memoire ChromaDB verifie apres reparation."
}

try {
    Write-InstallLog "Installation Lumena demarree."
    Write-InstallLog "Dossier cible : $AppDir"

    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "Lumena requiert Windows 64 bits."
    }
    if (-not (Test-Path -LiteralPath $AppDir -PathType Container)) {
        throw "Le dossier d'installation n'existe pas."
    }

    $root = [System.IO.Path]::GetPathRoot($AppDir)
    $drive = New-Object System.IO.DriveInfo($root)
    $minimumFree = 4GB
    if ($drive.AvailableFreeSpace -lt $minimumFree) {
        $availableGiB = [Math]::Round($drive.AvailableFreeSpace / 1GB, 1)
        throw "Espace disque insuffisant : $availableGiB Gio disponibles, 4 Gio requis pour finaliser l'installation."
    }

    $wheelhouse = Join-Path $AppDir "wheelhouse"
    if (-not (Test-Path -LiteralPath $wheelhouse -PathType Container)) {
        $sourceWheelhouse = Join-Path $AppDir "installer\wheelhouse"
        if (Test-Path -LiteralPath $sourceWheelhouse -PathType Container) {
            $wheelhouse = $sourceWheelhouse
        }
    }
    $requirements = Join-Path $AppDir "requirements-lock.txt"
    if (-not (Test-Path -LiteralPath $wheelhouse -PathType Container)) {
        throw "Le cache de dependances hors ligne est absent."
    }
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        throw "requirements-lock.txt est absent."
    }

    $python = Resolve-CompatiblePython
    if (-not $python) {
        $python = Install-BundledPython
    } else {
        Write-InstallLog "Python 3.12 x64 compatible detecte."
    }

    Install-WebView2Runtime
    Install-VCRuntime
    if ($PreflightOnly) {
        Write-InstallLog "Preflight termine avec succes."
        exit 0
    }

    $venvDir = Join-Path $AppDir "venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if ((Test-Path -LiteralPath $venvDir) -and -not (Test-CompatiblePython -Path $venvPython)) {
        Write-InstallLog "Environnement virtuel incomplet : recreation."
        Remove-Item -LiteralPath $venvDir -Recurse -Force
    }
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        Write-InstallLog "Creation de l'environnement virtuel..."
        Invoke-LoggedCommand -FilePath $python -Arguments @("-m", "venv", $venvDir)
    }
    if (-not (Test-CompatiblePython -Path $venvPython)) {
        throw "L'environnement virtuel cree est invalide."
    }
    Write-InstallLog "Environnement virtuel verifie."

    Write-InstallLog "Installation des outils Python depuis le cache hors ligne..."
    Invoke-LoggedCommand -FilePath $venvPython -Arguments @(
        "-m", "pip", "install", "--no-index", "--find-links", $wheelhouse,
        "wheel", "setuptools", "pip"
    )

    Write-InstallLog "Installation des dependances Lumena depuis le cache hors ligne..."
    Invoke-LoggedCommand -FilePath $venvPython -Arguments @(
        "-m", "pip", "install", "--no-index", "--find-links", $wheelhouse,
        "-r", $requirements
    )

    Assert-ChromaRuntime -PythonPath $venvPython

    $envFile = Join-Path $AppDir ".env"
    $envExample = Join-Path $AppDir ".env.example"
    if (-not (Test-Path -LiteralPath $envFile) -and (Test-Path -LiteralPath $envExample)) {
        Copy-Item -LiteralPath $envExample -Destination $envFile
        Write-InstallLog "Configuration locale initialisee."
    }

    $browserBundle = Join-Path $AppDir "playwright-browsers"
    if (-not (Test-Path -LiteralPath $browserBundle -PathType Container)) {
        $sourceBrowserBundle = Join-Path $AppDir "installer\playwright-browsers"
        if (Test-Path -LiteralPath $sourceBrowserBundle -PathType Container) {
            $browserBundle = $sourceBrowserBundle
        }
    }
    if (Test-Path -LiteralPath $browserBundle -PathType Container) {
        $env:PLAYWRIGHT_BROWSERS_PATH = $browserBundle
        $chromium = Get-ChildItem -LiteralPath $browserBundle -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $chromium) {
            throw "Le navigateur Chromium embarque est incomplet."
        }
        Write-InstallLog "Navigateur Chromium embarque verifie."
    } else {
        Write-InstallLog "Installation du navigateur Chromium..."
        Invoke-LoggedCommand -FilePath $venvPython -Arguments @("-m", "playwright", "install", "chromium")
    }

    Push-Location $AppDir
    try {
        Write-InstallLog "Initialisation des dossiers Lumena..."
        Invoke-LoggedCommand -FilePath $venvPython -Arguments @(
            "-c", "from src.utils.paths import validate_instance_dirs; validate_instance_dirs(create=True)"
        )
        Invoke-LoggedCommand -FilePath $venvPython -Arguments @(
            "-c", "import fastapi, httpx, playwright, webview, chromadb_rust_bindings; print('Runtime Lumena verifie')"
        )
    } finally {
        Pop-Location
    }

    Write-InstallLog "Installation Lumena terminee avec succes."
    exit 0
} catch {
    Write-InstallLog ("ERREUR : " + $_.Exception.Message)
    Write-InstallLog ("Consultez ce journal : " + $LogFile)
    exit 1
}
