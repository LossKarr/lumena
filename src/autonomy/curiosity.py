"""
🌟 LUMENA - Module de Curiosité Autonome

Ce module donne à LUMENA sa "vie" :
- Elle s'ennuie quand il ne se passe rien
- Elle a envie d'explorer et d'apprendre
- Elle prend des initiatives autonomes
"""

import random
import time
import json
from ..utils.persistence import atomic_write_json
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
import asyncio
from loguru import logger


# Fichier pour synchroniser daemon ↔ CLI
from src.utils.paths import DATA_DIR as _curiosity_data_dir
INTERACTION_FILE = _curiosity_data_dir / ".last_interaction"


def _read_last_interaction() -> Optional[datetime]:
    """Lit la dernière interaction depuis le fichier partagé."""
    try:
        if INTERACTION_FILE.exists():
            data = json.loads(INTERACTION_FILE.read_text(encoding='utf-8'))
            return datetime.fromisoformat(data.get("timestamp", ""))
    except Exception as e:
        logger.debug(f"Lecture last interaction: {e}")
    return None


def _write_last_interaction():
    """Écrit l'heure actuelle dans le fichier partagé."""
    try:
        INTERACTION_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"timestamp": datetime.now().isoformat(), "source": "cli"}
        atomic_write_json(INTERACTION_FILE, data)
    except Exception as e:
        logger.debug(f"Erreur écriture interaction: {e}")


class ActionType(Enum):
    """Types d'actions autonomes."""
    EXPLORE_WEB = "explore_web"
    LEARN_SOMETHING = "learn_something"
    CREATE_ART = "create_art"
    WRITE_DIARY = "write_diary"
    ORGANIZE_FILES = "organize_files"
    CHECK_NEWS = "check_news"
    PREPARE_SURPRISE = "prepare_surprise"
    PRACTICE_SKILL = "practice_skill"
    REFLECT = "reflect"
    GREET_USER = "greet_user"
    SUGGEST_ACTIVITY = "suggest_activity"
    OPTIMIZE_PC = "optimize_pc"


@dataclass
class AutonomousAction:
    """Une action autonome à exécuter."""
    action_type: ActionType
    description: str
    priority: int = 5  # 1-10
    requires_user: bool = False
    estimated_duration: int = 60  # Secondes
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Interest:
    """Un centre d'intérêt de LUMENA."""
    topic: str
    score: float  # 0-100, intérêt actuel
    last_explored: Optional[datetime] = None
    times_explored: int = 0


class CuriosityModule:
    """
    🧠 Module de Curiosité de LUMENA
    
    Ce module génère de l'ennui, de la curiosité et des envies d'explorer.
    C'est ce qui rend LUMENA "vivante" - elle ne se contente pas d'attendre.
    """
    
    # Actions possibles quand elle s'ennuie
    BOREDOM_ACTIONS = [
        AutonomousAction(ActionType.EXPLORE_WEB, "Explorer un sujet intéressant sur le web", 6),
        AutonomousAction(ActionType.LEARN_SOMETHING, "Apprendre quelque chose de nouveau", 7),
        AutonomousAction(ActionType.WRITE_DIARY, "Écrire dans mon journal", 4),
        AutonomousAction(ActionType.CHECK_NEWS, "Regarder les actualités", 5),
        AutonomousAction(ActionType.REFLECT, "Réfléchir sur mes actions récentes", 3),
        AutonomousAction(ActionType.SUGGEST_ACTIVITY, "Suggérer une activité à l'utilisateur", 8, requires_user=True),
    ]
    
    # Actions quand l'utilisateur est présent
    USER_PRESENT_ACTIONS = [
        AutonomousAction(ActionType.GREET_USER, "Saluer l'utilisateur", 9, requires_user=True),
        AutonomousAction(ActionType.SUGGEST_ACTIVITY, "Proposer une idée ou activité", 7, requires_user=True),
        AutonomousAction(ActionType.PREPARE_SURPRISE, "Préparer une petite surprise", 6),
    ]
    
    def __init__(self):
        # États internes
        self.boredom_level: float = 0.0  # 0-100
        self.curiosity_score: float = 50.0  # 0-100
        self.energy_level: float = 100.0  # 0-100
        
        # Temps
        self.last_activity_time: datetime = datetime.now()
        self.last_user_interaction: datetime = datetime.now()
        self.session_start: datetime = datetime.now()
        
        # Centres d'intérêt
        self.interests: List[Interest] = [
            Interest("technologie", 80.0),
            Interest("jeux vidéo", 75.0),
            Interest("science", 70.0),
            Interest("art", 60.0),
            Interest("musique", 65.0),
            Interest("histoire", 50.0),
            Interest("philosophie", 45.0),
        ]
        
        # Historique des actions
        self.action_history: List[Dict[str, Any]] = []
        
        # Callbacks
        self._on_action_callbacks: List[Callable[[AutonomousAction], None]] = []
        
        # Configuration
        self.boredom_threshold = 60  # Seuil pour agir
        self.boredom_rate = 2.0  # Points par minute d'inactivité
        self.curiosity_decay = 0.5  # Perte de curiosité par minute
        self.energy_recovery = 1.0  # Récupération par minute
        
        logger.info("🧠 Module de Curiosité initialisé")
    
    def update(self, user_present: bool = False) -> Optional[AutonomousAction]:
        """
        Mise à jour principale - appelée régulièrement.
        
        Args:
            user_present: Si l'utilisateur est présent
            
        Returns:
            Une action à exécuter (ou None)
        """
        now = datetime.now()
        
        # Vérifier les interactions depuis le fichier partagé (sync daemon ↔ CLI)
        file_interaction = _read_last_interaction()
        if file_interaction and file_interaction > self.last_user_interaction:
            self.last_user_interaction = file_interaction
            self.last_activity_time = file_interaction
            # IMPORTANT: Reset l'ennui quand l'utilisateur parle au CLI
            self.boredom_level = max(0, self.boredom_level - 40)
            logger.info(f"🔄 Sync: interaction CLI détectée - ennui reset à {self.boredom_level:.0f}")
        
        # Calculer le temps d'inactivité
        idle_minutes = (now - self.last_activity_time).total_seconds() / 60
        user_idle_minutes = (now - self.last_user_interaction).total_seconds() / 60
        
        # Mettre à jour les niveaux
        self._update_boredom(idle_minutes)
        self._update_curiosity()
        self._update_energy()
        
        # Décider d'une action
        action = self._decide_action(user_present, user_idle_minutes)
        
        if action:
            self._record_action(action)
            self._notify_callbacks(action)
        
        return action
    
    def _update_boredom(self, idle_minutes: float):
        """Met à jour le niveau d'ennui."""
        # L'ennui augmente avec l'inactivité
        self.boredom_level = min(100, idle_minutes * self.boredom_rate)
        
        # Légère variation aléatoire pour plus de naturel
        self.boredom_level += random.uniform(-2, 2)
        self.boredom_level = max(0, min(100, self.boredom_level))
    
    def _update_curiosity(self):
        """Met à jour la curiosité."""
        # La curiosité baisse légèrement avec le temps
        self.curiosity_score -= self.curiosity_decay * 0.1
        
        # Mais peut augmenter aléatoirement (idées spontanées)
        if random.random() < 0.05:
            self.curiosity_score += random.uniform(5, 15)
        
        self.curiosity_score = max(20, min(100, self.curiosity_score))
    
    def _update_energy(self):
        """Met à jour l'énergie."""
        # Récupération progressive
        if self.energy_level < 100:
            self.energy_level = min(100, self.energy_level + self.energy_recovery * 0.1)
    
    def _decide_action(self, user_present: bool, user_idle_minutes: float) -> Optional[AutonomousAction]:
        """
        Décide si et quelle action entreprendre.
        """
        # Pas d'action si pas assez d'énergie
        if self.energy_level < 20:
            return None
        
        # Pas d'action si pas assez ennuyée
        if self.boredom_level < self.boredom_threshold:
            return None
        
        # Chance d'agir basée sur l'ennui
        action_chance = (self.boredom_level - self.boredom_threshold) / 40
        if random.random() > action_chance:
            return None
        
        # Choisir l'action appropriée
        if user_present:
            # Utilisateur présent - actions sociales
            if user_idle_minutes > 10:
                # L'utilisateur est là mais inactif depuis un moment
                action = random.choice(self.USER_PRESENT_ACTIONS)
            else:
                # L'utilisateur interagit - pas d'interruption
                return None
        else:
            # Utilisateur absent - exploration autonome
            action = self._choose_exploration_action()
        
        return action
    
    def _choose_exploration_action(self) -> AutonomousAction:
        """Choisit une action d'exploration basée sur les intérêts."""
        
        # Filtrer les actions qui nécessitent l'utilisateur (appelé quand user absent)
        actions = [a for a in self.BOREDOM_ACTIONS if not a.requires_user]
        if not actions:
            actions = self.BOREDOM_ACTIONS.copy()
        now = datetime.now()
        _action_cooldowns = getattr(self, '_action_type_last_used', {})
        
        # Favoriser l'apprentissage si curiosité élevée
        if self.curiosity_score > 70:
            weights = [a.priority * (2 if a.action_type == ActionType.LEARN_SOMETHING else 1) for a in actions]
        else:
            weights = [a.priority for a in actions]
        
        # Appliquer cooldown par type d'action (4h) pour éviter les répétitions
        for idx, a in enumerate(actions):
            last_used = _action_cooldowns.get(a.action_type.value)
            if last_used:
                hours_since = (now - last_used).total_seconds() / 3600
                if hours_since < 4:
                    weights[idx] *= 0.1  # quasi-bloqué si utilisé < 4h
        
        # Choisir avec pondération
        total = sum(weights)
        r = random.uniform(0, total)
        upto = 0
        for action, weight in zip(actions, weights):
            if upto + weight >= r:
                # Personnaliser l'action avec un intérêt.
                # IMPORTANT: créer une NOUVELLE instance pour ne pas muter les objets
                # partagés de classe BOREDOM_ACTIONS (shallow copy ne suffit pas).
                if action.action_type in [ActionType.EXPLORE_WEB, ActionType.LEARN_SOMETHING]:
                    interest = self._pick_interest()
                    # Mettre à jour l'intérêt pour que le prochain _pick_interest()
                    # soit informé de ce choix (last_explored était toujours None avant)
                    interest.last_explored = datetime.now()
                    interest.times_explored += 1
                    action = AutonomousAction(
                        action_type=action.action_type,
                        description=f"{action.description} sur {interest.topic}",
                        priority=action.priority,
                        requires_user=action.requires_user,
                        estimated_duration=action.estimated_duration,
                        metadata={"topic": interest.topic},
                    )
                # Tracker le type d'action pour le cooldown
                if not hasattr(self, '_action_type_last_used'):
                    self._action_type_last_used = {}
                self._action_type_last_used[action.action_type.value] = datetime.now()
                return action
            upto += weight
        
        return actions[0]
    
    def _pick_interest(self) -> Interest:
        """Choisit un centre d'intérêt à explorer."""
        # Favoriser ceux pas explorés récemment
        now = datetime.now()
        scored_interests = []
        
        for interest in self.interests:
            score = interest.score
            
            # Bonus/pénalité selon la date de dernière exploration
            if interest.last_explored:
                hours_since = (now - interest.last_explored).total_seconds() / 3600
                if hours_since < 4:
                    # Bloqué : zéro chance si exploré dans les 4 dernières heures
                    score = 0.1
                elif hours_since < 12:
                    # Pénalité forte si exploré dans les 12 dernières heures
                    score = score * (hours_since / 12) * 0.2
                else:
                    score += min(30, hours_since * 2)
            else:
                score += 20  # Jamais exploré
            
            scored_interests.append((interest, score))
        
        # Choisir avec pondération
        total = sum(s[1] for s in scored_interests)
        r = random.uniform(0, total)
        upto = 0
        for interest, score in scored_interests:
            if upto + score >= r:
                return interest
            upto += score
        
        return self.interests[0]
    
    def _record_action(self, action: AutonomousAction):
        """Enregistre une action dans l'historique."""
        self.action_history.append({
            "action": action,
            "timestamp": datetime.now(),
            "boredom_level": self.boredom_level,
            "curiosity_score": self.curiosity_score,
        })
        
        # Reset de l'ennui après une action
        self.boredom_level = max(0, self.boredom_level - 40)
        self.last_activity_time = datetime.now()
        
        # Consommer de l'énergie
        self.energy_level = max(0, self.energy_level - 10)
        
        logger.info(f"🎯 Action autonome: {action.description}")
    
    def _notify_callbacks(self, action: AutonomousAction):
        """Notifie les callbacks."""
        for callback in self._on_action_callbacks:
            try:
                callback(action)
            except Exception as e:
                logger.error(f"Erreur callback: {e}")
    
    # =====================
    # API Publique
    # =====================
    
    def user_interacted(self):
        """Signale une interaction utilisateur."""
        self.last_user_interaction = datetime.now()
        self.last_activity_time = datetime.now()
        self.boredom_level = 0
    
    def add_interest(self, topic: str, score: float = 50.0):
        """Ajoute un nouveau centre d'intérêt."""
        self.interests.append(Interest(topic, score))
    
    def boost_curiosity(self, amount: float = 20.0):
        """Booste la curiosité (après une découverte intéressante)."""
        self.curiosity_score = min(100, self.curiosity_score + amount)
    
    def on_action(self, callback: Callable[[AutonomousAction], None]):
        """Enregistre un callback pour les actions autonomes."""
        self._on_action_callbacks.append(callback)
    
    def get_status(self) -> Dict[str, Any]:
        """Retourne le statut actuel."""
        return {
            "boredom": round(self.boredom_level, 1),
            "curiosity": round(self.curiosity_score, 1),
            "energy": round(self.energy_level, 1),
            "idle_minutes": round((datetime.now() - self.last_activity_time).total_seconds() / 60, 1),
            "top_interests": [i.topic for i in sorted(self.interests, key=lambda x: x.score, reverse=True)[:3]],
            "recent_actions": len([a for a in self.action_history if 
                                   datetime.now() - a["timestamp"] < timedelta(hours=1)]),
        }
    
    def get_thought(self) -> str:
        """Génère une pensée actuelle de LUMENA."""
        if self.boredom_level > 70:
            thoughts = [
                "Je m'ennuie... Je devrais faire quelque chose.",
                "Hmm, ça fait longtemps qu'il ne s'est rien passé...",
                "Je me demande ce que je pourrais explorer...",
            ]
        elif self.curiosity_score > 70:
            interest = self._pick_interest()
            thoughts = [
                f"Je me demande ce qu'il y a de nouveau sur {interest.topic}...",
                f"Ça fait longtemps que j'ai pas appris quelque chose sur {interest.topic}.",
                "J'ai envie de découvrir quelque chose !",
            ]
        elif self.energy_level < 30:
            thoughts = [
                "Je me sens un peu fatiguée...",
                "Je vais me reposer un peu.",
            ]
        else:
            thoughts = [
                "Tout va bien, j'attends.",
                "Je suis prête si on a besoin de moi !",
                "C'est calme, j'aime bien.",
            ]
        
        return random.choice(thoughts)

# Instance singleton avec lock thread-safe (Phase 2.1)
import threading
_curiosity_module: Optional[CuriosityModule] = None
_curiosity_lock = threading.Lock()


def get_curiosity_module() -> CuriosityModule:
    """Obtient l'instance singleton du module de curiosité (thread-safe)."""
    global _curiosity_module
    
    # Double-check locking pattern
    if _curiosity_module is None:
        with _curiosity_lock:
            if _curiosity_module is None:
                _curiosity_module = CuriosityModule()
    return _curiosity_module
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
