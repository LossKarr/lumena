"""
🏥 LUMENA - Health Check Module (Phase 6.1)

Fournit des diagnostics proactifs sur l'état du système.
"""

import os
import sys
import asyncio
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger


@dataclass
class HealthStatus:
    """Statut de santé d'un composant."""
    name: str
    healthy: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass 
class SystemHealth:
    """Statut de santé global du système."""
    overall_healthy: bool
    components: List[HealthStatus]
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire pour l'API."""
        return {
            "healthy": self.overall_healthy,
            "timestamp": self.timestamp.isoformat(),
            "components": [
                {
                    "name": c.name,
                    "healthy": c.healthy,
                    "message": c.message,
                    "details": c.details
                }
                for c in self.components
            ]
        }


class HealthChecker:
    """
    🏥 Health Checker
    
    Vérifie la santé des différents composants du système.
    """
    
    def __init__(self):
        self._checks: List[callable] = []
        self._register_default_checks()
    
    def _register_default_checks(self):
        """Enregistre les vérifications par défaut."""
        self._checks = [
            self._check_python_version,
            self._check_data_directory,
            self._check_env_file,
            self._check_ollama,
            self._check_memory,
            self._check_api_keys,
            self._check_playwright,
            self._check_disk_space,
            self._check_docker,
        ]
    
    def _check_python_version(self) -> HealthStatus:
        """Vérifie la version Python."""
        version = sys.version_info
        required = (3, 10)
        
        healthy = version >= required
        return HealthStatus(
            name="python_version",
            healthy=healthy,
            message=f"Python {version.major}.{version.minor}.{version.micro}",
            details={
                "version": f"{version.major}.{version.minor}.{version.micro}",
                "required": f"{required[0]}.{required[1]}+"
            }
        )
    
    def _check_data_directory(self) -> HealthStatus:
        """Vérifie que le répertoire data est writable."""
        from src.utils.paths import DATA_DIR
        data_dir = DATA_DIR
        
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            test_file = data_dir / ".health_check"
            test_file.write_text("test")
            test_file.unlink()
            
            return HealthStatus(
                name="data_directory",
                healthy=True,
                message="Data directory writable",
                details={"path": str(data_dir.resolve())}
            )
        except Exception as e:
            return HealthStatus(
                name="data_directory",
                healthy=False,
                message=f"Data directory not writable: {e}",
                details={"error": str(e)}
            )
    
    def _check_env_file(self) -> HealthStatus:
        """Vérifie la présence du fichier .env."""
        env_path = Path(".env")
        
        if env_path.exists():
            return HealthStatus(
                name="env_file",
                healthy=True,
                message=".env file found",
                details={"path": str(env_path.resolve())}
            )
        else:
            return HealthStatus(
                name="env_file",
                healthy=False,
                message=".env file not found (optional)",
                details={"required": False}
            )
    
    def _check_ollama(self) -> HealthStatus:
        """Vérifie si Ollama est disponible."""
        try:
            import httpx
            
            # Synchrone car health check doit être rapide
            with httpx.Client(timeout=2.0) as client:
                _ollama_host = (
                    os.getenv("LUMENA_OLLAMA_HOST", "").strip()
                    or os.getenv("OLLAMA_HOST", "http://localhost:11434")
                )
                response = client.get(f"{_ollama_host.rstrip('/')}/api/tags")
                if response.status_code == 200:
                    models = response.json().get("models", [])
                    return HealthStatus(
                        name="ollama",
                        healthy=True,
                        message=f"Ollama running ({len(models)} models)",
                        details={"models_count": len(models)}
                    )
        except Exception as e:
            logger.debug(f"Ollama health check failed: {e}")
        
        return HealthStatus(
            name="ollama",
            healthy=False,
            message="Ollama not running (optional for cloud LLMs)",
            details={"required": False, "info": "Run 'ollama serve' for local LLM"}
        )
    
    def _check_memory(self) -> HealthStatus:
        """Vérifie l'utilisation mémoire."""
        try:
            import psutil
            
            memory = psutil.virtual_memory()
            percent_used = memory.percent
            gb_available = memory.available / (1024 ** 3)
            
            healthy = percent_used < 90 and gb_available > 1
            
            return HealthStatus(
                name="memory",
                healthy=healthy,
                message=f"{gb_available:.1f}GB available ({percent_used:.0f}% used)",
                details={
                    "percent_used": percent_used,
                    "gb_available": round(gb_available, 2)
                }
            )
        except ImportError:
            return HealthStatus(
                name="memory",
                healthy=True,
                message="psutil not installed, memory check skipped",
                details={"info": "pip install psutil for memory monitoring"}
            )
    
    def _check_api_keys(self) -> HealthStatus:
        """Vérifie la présence des clés API."""
        from dotenv import load_dotenv
        load_dotenv()
        
        keys_found = []
        keys_missing = []
        
        api_keys = [
            ("ANTHROPIC_API_KEY", "Anthropic/Claude"),
            ("OPENAI_API_KEY", "OpenAI"),
            ("GOOGLE_API_KEY", "Google/Gemini"),
            ("DEEPSEEK_API_KEY", "DeepSeek"),
            ("NVIDIA_API_KEY", "NVIDIA"),
            ("MOONSHOT_API_KEY", "Moonshot/Kimi"),
            ("XAI_API_KEY", "xAI/Grok"),
            ("MINIMAX_API_KEY", "MiniMax"),
        ]
        
        for key_name, provider in api_keys:
            if os.getenv(key_name):
                keys_found.append(provider)
            else:
                keys_missing.append(provider)
        
        healthy = len(keys_found) > 0
        
        return HealthStatus(
            name="api_keys",
            healthy=healthy,
            message=f"{len(keys_found)} provider(s) configuré(s)" if healthy else "Aucun provider configuré — à faire dans le wizard",
            details={
                "required": False,
                "configured": keys_found,
                "missing": keys_missing
            }
        )
    
    def _check_playwright(self) -> HealthStatus:
        """Vérifie que Playwright Chromium est installé."""
        try:
            import playwright
            # 1. Check inside the playwright package (bundled browsers)
            pw_path = Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
            if not pw_path.exists():
                pw_path = Path(playwright.__file__).parent / "driver" / "package" / "browsers"
            # 2. Check PLAYWRIGHT_BROWSERS_PATH env var
            env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
            if env_path:
                pw_path = Path(env_path)
            # 3. Check default system path (ms-playwright)
            if not pw_path.exists():
                pw_path = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
            if not pw_path.exists():
                pw_path = Path.home() / ".cache" / "ms-playwright"
            chromium_dirs = [d for d in pw_path.iterdir() if d.is_dir() and "chromium" in d.name.lower()] if pw_path.exists() else []
            if chromium_dirs:
                return HealthStatus(
                    name="playwright",
                    healthy=True,
                    message="Playwright Chromium installé",
                    details={"required": False, "path": str(chromium_dirs[0])},
                )
            raise FileNotFoundError("Chromium browser not found")
        except Exception:
            return HealthStatus(
                name="playwright",
                healthy=False,
                message="Playwright non installé (navigation web désactivée)",
                details={
                    "required": False,
                    "hint": "python -m playwright install chromium",
                },
            )

    def _check_disk_space(self) -> HealthStatus:
        """Vérifie l'espace disque (>1 GB requis)."""
        import shutil
        total, used, free = shutil.disk_usage(".")
        gb_free = free / (1024**3)
        return HealthStatus(
            name="disk_space",
            healthy=gb_free > 1.0,
            message=f"{gb_free:.1f} GB libres",
            details={"gb_free": round(gb_free, 1)},
        )

    def _check_docker(self) -> HealthStatus:
        """Détecte Docker (optionnel — sandbox sécurité)."""
        import subprocess
        try:
            r = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return HealthStatus(
                    name="docker",
                    healthy=True,
                    message="Docker détecté — sandbox activé",
                    details={"required": False},
                )
            return HealthStatus(
                name="docker",
                healthy=False,
                message="Docker installé mais non démarré",
                details={
                    "required": False,
                    "hint": "Lancez Docker Desktop pour activer le sandbox sécurisé",
                },
            )
        except FileNotFoundError:
            return HealthStatus(
                name="docker",
                healthy=False,
                message="Docker absent — exécution locale (suffisant)",
                details={
                    "required": False,
                    "hint": "Optionnel. Pour un sandbox isolé : https://docker.com/products/docker-desktop",
                },
            )
        except Exception:
            return HealthStatus(
                name="docker",
                healthy=False,
                message="Docker absent — exécution locale (suffisant)",
                details={"required": False},
            )

    def check_all(self) -> SystemHealth:
        """Exécute toutes les vérifications."""
        components = []
        
        for check_fn in self._checks:
            try:
                status = check_fn()
                components.append(status)
            except Exception as e:
                logger.error(f"Health check failed: {check_fn.__name__}: {e}")
                components.append(HealthStatus(
                    name=check_fn.__name__.replace("_check_", ""),
                    healthy=False,
                    message=f"Check failed: {e}",
                    details={"error": str(e)}
                ))
        
        # Le système est healthy si tous les composants critiques sont OK
        critical_components = ["python_version", "data_directory"]
        overall_healthy = all(
            c.healthy for c in components 
            if c.name in critical_components
        )
        
        return SystemHealth(
            overall_healthy=overall_healthy,
            components=components
        )
    
    def check_quick(self) -> Dict[str, Any]:
        """Vérification rapide pour /api/health."""
        return {
            "status": "ok",
            "timestamp": datetime.now().isoformat()
        }


# Singleton
_health_checker: Optional[HealthChecker] = None


def get_health_checker() -> HealthChecker:
    """Retourne l'instance du health checker (thread-safe)."""
    global _health_checker
    
    import threading
    _lock = threading.Lock()
    
    if _health_checker is None:
        with _lock:
            if _health_checker is None:
                _health_checker = HealthChecker()
    
    return _health_checker
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
