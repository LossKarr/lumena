"""
📋 LUMENA - Audit Log pour Sub-Agents

Trace toutes les actions des sub-agents sans bloquer.
Audit pur : pas de confirmation, pas de restriction.
Rollback possible via backups automatiques.
"""

from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime
import json
import shutil
from loguru import logger


class SubAgentAuditLog:
    """
    Journal d'audit pour les actions des sub-agents.
    
    Principes :
    - Trace tout, ne bloque rien
    - Backup silencieux avant actions destructives
    - Rotation automatique (1 fichier par jour, max 30 jours)
    """

    # Actions considérées comme destructives (backup auto avant exécution)
    DESTRUCTIVE_ACTIONS = frozenset({
        "edit_own_code", "write_file", "delete_file",
        "run_command",  # peut modifier l'état système
    })

    def __init__(self, data_dir: Optional[Path] = None):
        from src.utils.paths import DATA_DIR
        self.data_dir = data_dir or DATA_DIR
        self.audit_dir = self.data_dir / "ops" / "subagent_audit"
        self.backup_dir = self.data_dir / "backups" / "auto"
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._max_log_days = 30

    def _log_file(self) -> Path:
        """Fichier du jour (rotation journalière)."""
        return self.audit_dir / f"audit_{datetime.now().strftime('%Y-%m-%d')}.jsonl"

    # Outcomes structurés reconnus par le pipeline
    OUTCOMES: frozenset = frozenset({
        "success", "tool_not_found", "policy_denied", "timeout",
        "exception", "failed", "partial", "blocked", "cancelled",
    })

    def log_action(
        self,
        agent_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
        task_id: Optional[str] = None,
        outcome: str = "",
        result_summary: Optional[str] = None,
    ) -> None:
        """Enregistre une action dans le journal.

        ``outcome`` est la source de vérité : success / tool_not_found /
        policy_denied / timeout / exception / failed / partial / blocked /
        cancelled.  ``success`` est dérivé automatiquement (outcome == "success").
        """
        entry = {
            "ts": datetime.now().isoformat(),
            "agent": agent_name,
            "tool": tool_name,
            "args": self._sanitize_args(arguments),
            "task_id": task_id,
            "outcome": outcome,
            "success": outcome == "success",
        }
        if result_summary:
            entry["result"] = result_summary[:200]

        try:
            with self._log_file().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.debug(f"Audit log write failed: {e}")

    def backup_before_destructive(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        agent_name: str = "",
    ) -> Optional[str]:
        """
        Crée un backup silencieux avant une action destructive.
        
        Returns:
            Chemin du backup créé, ou None si non applicable.
        """
        if tool_name not in self.DESTRUCTIVE_ACTIONS:
            return None

        # Identifier le fichier cible
        target_path = self._extract_target_path(tool_name, arguments)
        if not target_path:
            return None

        source = Path(target_path)
        if not source.exists() or not source.is_file():
            return None

        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{source.stem}_{ts}{source.suffix}"
            backup_path = self.backup_dir / backup_name
            shutil.copy2(source, backup_path)
            logger.debug(f"📋 Auto-backup: {source.name} → {backup_path}")
            return str(backup_path)
        except Exception as e:
            logger.debug(f"Auto-backup failed for {source}: {e}")
            return None

    def cleanup_old_logs(self) -> int:
        """Supprime les logs d'audit de plus de _max_log_days jours. Retourne le nombre supprimé."""
        removed = 0
        cutoff = datetime.now().timestamp() - (self._max_log_days * 86400)
        try:
            for f in self.audit_dir.glob("audit_*.jsonl"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
        except Exception as e:
            logger.warning(f"Nettoyage audit logs: {e}")
        return removed

    @staticmethod
    def _sanitize_args(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Tronque les valeurs longues pour garder le log lisible."""
        sanitized = {}
        for k, v in arguments.items():
            sv = str(v)
            sanitized[k] = sv[:300] + "..." if len(sv) > 300 else v
        return sanitized

    @staticmethod
    def _extract_target_path(tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        """Extrait le chemin fichier cible d'un appel outil."""
        for key in ("file_path", "path", "filepath"):
            val = arguments.get(key)
            if val:
                return str(val)
        return None


# Singleton
_audit_log: Optional[SubAgentAuditLog] = None


def get_audit_log() -> SubAgentAuditLog:
    """Retourne l'instance globale de l'audit log."""
    global _audit_log
    if _audit_log is None:
        _audit_log = SubAgentAuditLog()
    return _audit_log
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
