"""
🌟 LUMENA - Gestionnaire d'Objectifs

Gère les objectifs à court et long terme de LUMENA.
Elle peut avoir des buts personnels qu'elle poursuit de manière autonome.
"""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json
import threading
from pathlib import Path
from loguru import logger


class GoalStatus(Enum):
    """Statut d'un objectif."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class GoalPriority(Enum):
    """Priorité d'un objectif."""
    LOW = 1
    MEDIUM = 5
    HIGH = 8
    CRITICAL = 10


class GoalType(Enum):
    """Types d'objectifs."""
    LEARNING = "learning"          # Apprendre quelque chose
    HELPING = "helping"            # Aider l'utilisateur
    CREATING = "creating"          # Créer quelque chose
    ORGANIZING = "organizing"      # Organiser/nettoyer
    SOCIAL = "social"              # Interaction sociale
    MAINTENANCE = "maintenance"    # Maintenance système
    EXPLORATION = "exploration"    # Explorer/découvrir


def _infer_goal_envelope_defaults(goal_type: GoalType, workspace: Optional[str]) -> tuple[str, str, bool]:
    """Retourne (tool_category, risk_level, requires_verification) pour un goal."""
    if goal_type == GoalType.SOCIAL:
        return "communication", "medium", True
    if goal_type in {GoalType.LEARNING, GoalType.EXPLORATION, GoalType.HELPING}:
        return "autonomy", "low", False
    if goal_type in {GoalType.ORGANIZING, GoalType.MAINTENANCE}:
        return ("files", "medium", False) if workspace else ("autonomy", "low", False)
    if goal_type == GoalType.CREATING:
        return ("project", "medium", False) if workspace else ("autonomy", "low", False)
    return "autonomy", "low", False


@dataclass
class Goal:
    """Un objectif de LUMENA."""
    id: str
    title: str
    description: str
    goal_type: GoalType
    priority: GoalPriority = GoalPriority.MEDIUM
    status: GoalStatus = GoalStatus.PENDING
    
    # Progression
    progress: float = 0.0  # 0-100
    steps_total: int = 1
    steps_completed: int = 0
    
    # Temps
    created_at: datetime = field(default_factory=datetime.now)
    deadline: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Métadonnées
    metadata: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    
    def update_progress(self, steps_done: int = 1):
        """Met à jour la progression."""
        self.steps_completed = min(self.steps_total, self.steps_completed + steps_done)
        self.progress = (self.steps_completed / self.steps_total) * 100
        
        if self.progress >= 100:
            self.complete()
    
    def complete(self):
        """Marque l'objectif comme complété."""
        self.status = GoalStatus.COMPLETED
        self.completed_at = datetime.now()
        self.progress = 100
    
    def fail(self, reason: str = ""):
        """Marque l'objectif comme échoué."""
        self.status = GoalStatus.FAILED
        if reason:
            self.notes.append(f"Échec: {reason}")
    
    def build_task_envelope(
        self,
        *,
        workspace: Optional[str] = None,
        budget_seconds: int = 300,
    ):
        """Construit une TaskEnvelope valide pour l'execution autonome du goal."""
        from .task_envelope import TaskEnvelope

        metadata = self.metadata if isinstance(self.metadata, dict) else {}
        resolved_workspace = (
            metadata.get("envelope_workspace")
            or metadata.get("workspace_path")
            or metadata.get("project_path")
            or workspace
        )
        default_category, default_risk, default_verify = _infer_goal_envelope_defaults(
            self.goal_type,
            resolved_workspace,
        )
        envelope = TaskEnvelope.for_autonomous(
            origin=str(metadata.get("envelope_origin") or "goals"),
            intent=str(
                metadata.get("envelope_intent")
                or self.title
                or self.description
                or "goal autonome"
            )[:200],
            workspace=resolved_workspace,
            tool_category=str(metadata.get("envelope_tool_category") or default_category),
            budget_seconds=int(metadata.get("envelope_budget_seconds") or max(10, budget_seconds)),
            risk_level=str(metadata.get("envelope_risk_level") or default_risk),
            requires_verification=bool(
                metadata.get("envelope_requires_verification", default_verify)
            ),
        )
        envelope.validate()
        return envelope

    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "goal_type": self.goal_type.value,
            "priority": self.priority.value,
            "status": self.status.value,
            "progress": self.progress,
            "steps_total": self.steps_total,
            "steps_completed": self.steps_completed,
            "created_at": self.created_at.isoformat(),
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata,
            "notes": self.notes,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        """Crée depuis un dictionnaire."""
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            goal_type=GoalType(data["goal_type"]),
            priority=GoalPriority(data["priority"]),
            status=GoalStatus(data["status"]),
            progress=data.get("progress", 0),
            steps_total=data.get("steps_total", 1),
            steps_completed=data.get("steps_completed", 0),
            created_at=datetime.fromisoformat(data["created_at"]),
            deadline=datetime.fromisoformat(data["deadline"]) if data.get("deadline") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            metadata=data.get("metadata", {}),
            notes=data.get("notes", []),
        )


class GoalManager:
    """
    🎯 Gestionnaire d'Objectifs de LUMENA

    Gère les objectifs à court et long terme.
    Permet à LUMENA de poursuivre des buts de manière autonome.
    """

    ARCHIVE_AFTER_DAYS = 30  # Archiver les goals terminés/échoués après N jours

    def __init__(self, data_dir: Optional[Path] = None):
        self.goals: Dict[str, Goal] = {}
        self.data_dir = data_dir
        self._save_lock = threading.Lock()  # Thread-safe pour heartbeat + conversations en parallèle
        self._last_archive: Optional[datetime] = None
        self._is_archiving: bool = False

        if self.data_dir:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.goals_file = self.data_dir / "goals.json"
            self.archive_file = self.data_dir / "goals_archive.json"
            self._load()

        logger.info("🎯 Gestionnaire d'objectifs initialisé")
    
    def _load(self):
        """Charge les objectifs depuis le fichier."""
        if self.goals_file and self.goals_file.exists():
            try:
                with open(self.goals_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for goal_data in data:
                        goal = Goal.from_dict(goal_data)
                        goal.metadata = self._normalize_goal_metadata(goal)
                        self.goals[goal.id] = goal
                logger.info(f"Chargé {len(self.goals)} objectifs")
            except Exception as e:
                logger.error(f"Erreur chargement objectifs: {e}")
    
    def _save(self):
        """Sauvegarde les objectifs (thread-safe, écriture atomique)."""
        if hasattr(self, "goals_file") and self.goals_file:
            # Archiver les vieux goals terminés/échoués (au plus 1x par 24h)
            now = datetime.now()
            if not self._is_archiving and (
                self._last_archive is None
                or (now - self._last_archive).total_seconds() > 86400
            ):
                self._is_archiving = True
                try:
                    self._archive_old_goals()
                    self._last_archive = now
                finally:
                    self._is_archiving = False

            with self._save_lock:
                try:
                    data = [g.to_dict() for g in self.goals.values()]
                    tmp = Path(str(self.goals_file) + ".tmp")
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    tmp.replace(self.goals_file)  # Atomique sur la plupart des OS
                except Exception as e:
                    logger.error(f"Erreur sauvegarde objectifs: {e}")

    def _normalize_goal_metadata(self, goal: Goal) -> Dict[str, Any]:
        """Injecte des metadata envelope_* coherentes pour tous les goals."""
        metadata = dict(goal.metadata or {})
        requested_workspace = (
            metadata.get("envelope_workspace")
            or metadata.get("workspace_path")
            or metadata.get("project_path")
        )
        requested_budget = metadata.get("envelope_budget_seconds") or 300

        try:
            envelope = goal.build_task_envelope(
                workspace=requested_workspace,
                budget_seconds=int(requested_budget),
            )
        except Exception as e:
            logger.warning(
                "[goals] envelope invalide pour '{}' - fallback applique ({})",
                goal.title,
                e,
            )
            from .task_envelope import TaskEnvelope

            default_category, default_risk, default_verify = _infer_goal_envelope_defaults(
                goal.goal_type,
                requested_workspace,
            )
            envelope = TaskEnvelope.for_autonomous(
                origin="goals",
                intent=str(goal.title or goal.description or "goal autonome")[:200],
                workspace=requested_workspace,
                tool_category=default_category,
                budget_seconds=max(10, int(requested_budget)),
                risk_level=default_risk,
                requires_verification=default_verify,
            )
            envelope.validate()

        metadata["envelope_origin"] = envelope.origin
        metadata["envelope_intent"] = envelope.intent
        metadata["envelope_workspace"] = envelope.workspace
        metadata["envelope_tool_category"] = envelope.tool_category
        metadata["envelope_budget_seconds"] = envelope.budget_seconds
        metadata["envelope_risk_level"] = envelope.risk_level
        metadata["envelope_requires_verification"] = envelope.requires_verification
        return metadata

    def _archive_old_goals(self):
        """Déplace les vieux goals terminés/échoués vers goals_archive.json."""
        if not hasattr(self, "archive_file"):
            return

        cutoff = datetime.now() - timedelta(days=self.ARCHIVE_AFTER_DAYS)

        def _is_archivable(g: Goal) -> bool:
            if g.status == GoalStatus.COMPLETED:
                return bool(g.completed_at and g.completed_at < cutoff)
            if g.status in (GoalStatus.FAILED, GoalStatus.ABANDONED):
                return g.created_at < cutoff
            return False

        to_archive = [g for g in self.goals.values() if _is_archivable(g)]
        if not to_archive:
            return

        # Charger l'archive existante
        archived: list = []
        if self.archive_file.exists():
            try:
                archived = json.loads(self.archive_file.read_text(encoding="utf-8"))
            except Exception:
                archived = []

        archived.extend(g.to_dict() for g in to_archive)

        # Écriture atomique de l'archive
        try:
            tmp = Path(str(self.archive_file) + ".tmp")
            tmp.write_text(json.dumps(archived, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.archive_file)
        except Exception as e:
            logger.warning(f"Erreur écriture archive goals: {e}")
            return

        for g in to_archive:
            del self.goals[g.id]

        logger.info(f"📦 {len(to_archive)} goal(s) archivé(s) (>{self.ARCHIVE_AFTER_DAYS}j)")
    
    def create_goal(
        self,
        title: str,
        description: str,
        goal_type: GoalType,
        priority: GoalPriority = GoalPriority.MEDIUM,
        steps: int = 1,
        deadline: Optional[datetime] = None,
        metadata: Optional[Dict] = None
    ) -> Goal:
        """
        Crée un nouvel objectif.
        
        Args:
            title: Titre de l'objectif
            description: Description détaillée
            goal_type: Type d'objectif
            priority: Priorité
            steps: Nombre d'étapes
            deadline: Date limite optionnelle
            metadata: Données additionnelles
            
        Returns:
            L'objectif créé
        """
        goal_id = f"goal_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        goal = Goal(
            id=goal_id,
            title=title,
            description=description,
            goal_type=goal_type,
            priority=priority,
            steps_total=steps,
            deadline=deadline,
            metadata=metadata or {},
        )
        goal.metadata = self._normalize_goal_metadata(goal)
        
        self.goals[goal_id] = goal
        self._save()
        
        logger.info(f"🎯 Nouvel objectif: {title}")
        return goal
    
    def get_active_goals(self) -> List[Goal]:
        """Retourne les objectifs actifs (pending ou in_progress)."""
        return [
            g for g in self.goals.values()
            if g.status in [GoalStatus.PENDING, GoalStatus.IN_PROGRESS]
        ]
    
    def get_next_goal(self) -> Optional[Goal]:
        """
        Retourne le prochain objectif à poursuivre.
        Basé sur priorité, deadline et progression.
        """
        active = self.get_active_goals()
        if not active:
            return None
        
        # Scorer les objectifs
        def score_goal(g: Goal) -> float:
            score = g.priority.value * 10
            
            # Bonus si deadline proche
            if g.deadline:
                hours_left = (g.deadline - datetime.now()).total_seconds() / 3600
                if hours_left < 24:
                    score += 50
                elif hours_left < 72:
                    score += 20
            
            # Bonus si déjà commencé
            if g.status == GoalStatus.IN_PROGRESS:
                score += 15
            
            # Bonus basé sur progression (presque fini)
            if g.progress > 80:
                score += 10
            
            return score
        
        active.sort(key=score_goal, reverse=True)
        return active[0]
    
    def update_goal(self, goal_id: str, **kwargs):
        """Met à jour un objectif."""
        if goal_id in self.goals:
            goal = self.goals[goal_id]
            for key, value in kwargs.items():
                if hasattr(goal, key):
                    setattr(goal, key, value)
            if {"title", "description", "goal_type", "metadata"} & set(kwargs.keys()):
                goal.metadata = self._normalize_goal_metadata(goal)
            self._save()
    
    def complete_goal(self, goal_id: str):
        """Marque un objectif comme complété."""
        if goal_id in self.goals:
            self.goals[goal_id].complete()
            self._save()
            logger.info(f"✅ Objectif complété: {self.goals[goal_id].title}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne des statistiques sur les objectifs."""
        total = len(self.goals)
        completed = len([g for g in self.goals.values() if g.status == GoalStatus.COMPLETED])
        active = len(self.get_active_goals())
        
        return {
            "total": total,
            "completed": completed,
            "active": active,
            "completion_rate": (completed / total * 100) if total > 0 else 0,
            "by_type": {
                t.value: len([g for g in self.goals.values() if g.goal_type == t])
                for t in GoalType
            }
        }
    
    def suggest_goal(self) -> Goal:
        """
        Suggère un objectif basé sur le contexte.
        LUMENA peut utiliser ça pour se créer des objectifs.
        """
        suggestions = [
            ("Apprendre quelque chose sur la technologie", GoalType.LEARNING),
            ("Organiser les fichiers de Downloads", GoalType.ORGANIZING),
            ("Créer une image ou une histoire", GoalType.CREATING),
            ("Vérifier les mises à jour système", GoalType.MAINTENANCE),
            ("Explorer un nouveau sujet", GoalType.EXPLORATION),
        ]
        
        title, goal_type = suggestions[hash(datetime.now()) % len(suggestions)]
        
        return self.create_goal(
            title=title,
            description=f"Objectif auto-généré: {title}",
            goal_type=goal_type,
            priority=GoalPriority.LOW,
        )

# Instance singleton avec lock thread-safe (Phase 2.1)
_goal_manager: Optional[GoalManager] = None
_goal_manager_lock = threading.Lock()


def get_goal_manager(data_dir: Optional[Path] = None) -> GoalManager:
    """Obtient l'instance singleton du gestionnaire d'objectifs (thread-safe)."""
    global _goal_manager
    
    # Double-check locking pattern
    if _goal_manager is None:
        with _goal_manager_lock:
            if _goal_manager is None:
                _goal_manager = GoalManager(data_dir)
    return _goal_manager
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
