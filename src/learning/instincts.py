"""
🌟 LUMENA - Système d'Instincts (Apprentissage Continu)

Permet à Lumena d'apprendre de ses sessions et de développer
des "instincts" - des patterns de comportement appris.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import json
import threading
from loguru import logger


@dataclass
class Instinct:
    """Un instinct appris par Lumena."""
    id: str
    pattern: str  # Le pattern déclencheur
    response: str  # La réponse/action apprise
    confidence: float  # 0.0 - 1.0
    times_used: int = 0
    times_successful: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    last_used: Optional[datetime] = None
    category: str = "general"
    
    @property
    def success_rate(self) -> float:
        if self.times_used == 0:
            return 0.0
        return self.times_successful / self.times_used
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "pattern": self.pattern,
            "response": self.response,
            "confidence": self.confidence,
            "times_used": self.times_used,
            "times_successful": self.times_successful,
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "category": self.category,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Instinct":
        return cls(
            id=data["id"],
            pattern=data["pattern"],
            response=data["response"],
            confidence=data.get("confidence", 0.5),
            times_used=data.get("times_used", 0),
            times_successful=data.get("times_successful", 0),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            last_used=datetime.fromisoformat(data["last_used"]) if data.get("last_used") else None,
            category=data.get("category", "general"),
        )


@dataclass
class LearningEvent:
    """Un événement d'apprentissage."""
    event_type: str  # "success", "failure", "feedback"
    context: str
    action: str
    outcome: str
    timestamp: datetime = field(default_factory=datetime.now)
    user_feedback: Optional[str] = None


class InstinctSystem:
    """
    🧠 Système d'Instincts de Lumena
    
    Apprend des patterns de succès/échec pour améliorer
    les réponses futures. Les instincts sont persistés.
    """
    
    CONFIDENCE_THRESHOLD = 0.6  # Seuil pour appliquer un instinct
    DECAY_DAYS = 30  # Jours avant que la confidence décroisse
    MAX_INSTINCTS = 1000  # Au-delà, pruning automatique
    PRUNE_KEEP = 500  # Nombre d'instincts à garder après pruning
    
    def __init__(self, data_dir: Optional[Path] = None):
        from src.utils.paths import LEARNING_DIR
        self.data_dir = data_dir or LEARNING_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.instincts_file = self.data_dir / "instincts.json"
        self.events_file = self.data_dir / "learning_events.json"
        
        self.instincts: Dict[str, Instinct] = {}
        self.recent_events: List[LearningEvent] = []
        self._save_lock = threading.Lock()  # Protection écriture concurrent
        self._last_decay: Optional[datetime] = None  # Decay toutes les 24h
        self._is_decaying: bool = False  # Guard anti-récursion infinie

        # Charger les données
        self._load()

        logger.info(f"🧠 InstinctSystem initialisé ({len(self.instincts)} instincts)")
    
    def _load(self):
        """Charge les instincts depuis le disque."""
        try:
            if self.instincts_file.exists():
                data = json.loads(self.instincts_file.read_text(encoding='utf-8'))
                for instinct_data in data:
                    instinct = Instinct.from_dict(instinct_data)
                    self.instincts[instinct.id] = instinct
        except Exception as e:
            logger.warning(f"Erreur chargement instincts: {e}")
    
    def _save(self):
        """Sauvegarde les instincts sur disque (thread-safe + decay automatique)."""
        # Déclencher le decay si nécessaire (max 1x par 24h)
        # Guard _is_decaying : decay_old_instincts() appelle _save() → sans ce guard = récursion infinie
        now = datetime.now()
        if not self._is_decaying and (self._last_decay is None or (now - self._last_decay).total_seconds() > 86400):
            self._is_decaying = True
            try:
                self.decay_old_instincts()
                self._last_decay = now
            finally:
                self._is_decaying = False

        with self._save_lock:
            try:
                data = [i.to_dict() for i in self.instincts.values()]
                # Écriture atomique : temp file puis rename pour éviter la corruption
                tmp = Path(str(self.instincts_file) + ".tmp")
                tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
                tmp.replace(self.instincts_file)
            except Exception as e:
                logger.warning(f"Erreur sauvegarde instincts: {e}")
    
    def learn(
        self,
        pattern: str,
        response: str,
        was_successful: bool,
        category: str = "general"
    ) -> Optional[Instinct]:
        """
        Apprend d'une interaction.
        
        Args:
            pattern: Le contexte/pattern de la situation
            response: L'action/réponse effectuée
            was_successful: Si l'action a été un succès
            category: Catégorie de l'instinct
            
        Returns:
            L'instinct créé ou mis à jour
        """
        # Chercher un instinct existant similaire
        existing = self._find_similar_instinct(pattern, response)
        
        if existing:
            # Mettre à jour l'instinct existant
            existing.times_used += 1
            if was_successful:
                existing.times_successful += 1
                existing.confidence = min(1.0, existing.confidence + 0.05)
            else:
                existing.confidence = max(0.0, existing.confidence - 0.1)
            existing.last_used = datetime.now()
            
            self._save()
            logger.debug(f"🧠 Instinct mis à jour: {existing.id} (conf: {existing.confidence:.2f})")
            return existing
        else:
            # Créer un nouvel instinct
            instinct_id = f"inst_{len(self.instincts)}_{datetime.now().strftime('%H%M%S')}"
            instinct = Instinct(
                id=instinct_id,
                pattern=pattern[:200],  # Limiter la taille
                response=response[:500],
                confidence=0.5 if was_successful else 0.3,
                times_used=1,
                times_successful=1 if was_successful else 0,
                category=category
            )
            self.instincts[instinct_id] = instinct
            
            self._save()
            logger.info(f"🧠 Nouvel instinct appris: {instinct_id}")

            # Pruning automatique si trop d'instincts accumulés
            if len(self.instincts) > self.MAX_INSTINCTS:
                self.prune()

            return instinct
    
    def _find_similar_instinct(self, pattern: str, response: str) -> Optional[Instinct]:
        """Trouve un instinct similaire."""
        pattern_lower = pattern.lower()
        response_lower = response.lower()
        
        for instinct in self.instincts.values():
            # Similarité simple basée sur les mots communs
            pattern_words = set(pattern_lower.split())
            instinct_words = set(instinct.pattern.lower().split())
            
            if len(pattern_words & instinct_words) / max(len(pattern_words), 1) > 0.5:
                if response_lower[:50] == instinct.response.lower()[:50]:
                    return instinct
        
        return None
    
    def suggest(self, context: str) -> List[Instinct]:
        """
        Suggère des instincts basés sur le contexte actuel.
        
        Args:
            context: Le contexte de la situation
            
        Returns:
            Liste d'instincts pertinents triés par confidence
        """
        context_lower = context.lower()
        context_words = set(context_lower.split())
        
        suggestions = []
        
        for instinct in self.instincts.values():
            if instinct.confidence < self.CONFIDENCE_THRESHOLD:
                continue
            
            # Calculer la pertinence
            instinct_words = set(instinct.pattern.lower().split())
            overlap = len(context_words & instinct_words)
            
            if overlap >= 2:  # Au moins 2 mots en commun
                suggestions.append(instinct)
        
        # Trier par confidence décroissante
        suggestions.sort(key=lambda i: i.confidence, reverse=True)
        
        return suggestions[:5]  # Top 5
    
    def apply_instinct(self, instinct_id: str) -> Optional[str]:
        """
        Applique un instinct et retourne sa réponse.
        
        Args:
            instinct_id: ID de l'instinct
            
        Returns:
            La réponse de l'instinct
        """
        instinct = self.instincts.get(instinct_id)
        if not instinct:
            return None
        
        instinct.times_used += 1
        instinct.last_used = datetime.now()
        self._save()
        
        return instinct.response
    
    def provide_feedback(self, instinct_id: str, was_good: bool):
        """
        Fournit un feedback sur un instinct utilisé.
        
        Args:
            instinct_id: ID de l'instinct
            was_good: Si le résultat était bon
        """
        instinct = self.instincts.get(instinct_id)
        if not instinct:
            return
        
        if was_good:
            instinct.times_successful += 1
            instinct.confidence = min(1.0, instinct.confidence + 0.1)
        else:
            instinct.confidence = max(0.0, instinct.confidence - 0.15)
        
        self._save()
        logger.debug(f"🧠 Feedback instinct {instinct_id}: {'👍' if was_good else '👎'}")
    
    def decay_old_instincts(self):
        """Applique un decay sur les vieux instincts non utilisés."""
        now = datetime.now()
        for instinct in self.instincts.values():
            if instinct.last_used:
                days_unused = (now - instinct.last_used).days
                if days_unused > self.DECAY_DAYS:
                    decay = 0.01 * (days_unused - self.DECAY_DAYS)
                    instinct.confidence = max(0.1, instinct.confidence - decay)
        
        self._save()
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques."""
        total = len(self.instincts)
        active = sum(1 for i in self.instincts.values() if i.confidence >= self.CONFIDENCE_THRESHOLD)
        
        return {
            "total_instincts": total,
            "active_instincts": active,
            "avg_confidence": sum(i.confidence for i in self.instincts.values()) / max(total, 1),
            "categories": list(set(i.category for i in self.instincts.values())),
        }
    
    def format_instincts_summary(self) -> str:
        """Formate un résumé des instincts pour le prompt."""
        if not self.instincts:
            return ""
        
        active = [i for i in self.instincts.values() if i.confidence >= self.CONFIDENCE_THRESHOLD]
        if not active:
            return ""
        
        lines = ["## Learned Instincts"]
        for instinct in sorted(active, key=lambda x: x.confidence, reverse=True)[:5]:
            lines.append(f"- {instinct.pattern[:50]}... -> {instinct.response[:50]}... (conf: {instinct.confidence:.0%})")
        
        return "\n".join(lines)

    def prune(self, target: Optional[int] = None) -> int:
        """
        Supprime les instincts les moins performants.
        Garde les `target` instincts avec la meilleure confidence.
        Retourne le nombre d'instincts supprimés.
        """
        target = target or self.PRUNE_KEEP
        if len(self.instincts) <= target:
            return 0

        # Trier par (confidence, times_used) décroissant → garder les meilleurs
        sorted_ids = sorted(
            self.instincts.keys(),
            key=lambda k: (self.instincts[k].confidence, self.instincts[k].times_used),
            reverse=True,
        )

        keep_ids = set(sorted_ids[:target])
        to_remove = [k for k in list(self.instincts.keys()) if k not in keep_ids]

        for k in to_remove:
            del self.instincts[k]

        n_removed = len(to_remove)
        if n_removed > 0:
            logger.info(f"🧹 Pruning: {n_removed} instincts supprimés, {len(self.instincts)} restants")
            self._save()

        return n_removed


# Singleton avec lock thread-safe (Phase 2.1)
_instinct_system: Optional[InstinctSystem] = None
_instinct_system_lock = threading.Lock()


def get_instinct_system(data_dir: Optional[Path] = None) -> InstinctSystem:
    """Retourne l'instance globale du système d'instincts (thread-safe)."""
    global _instinct_system
    
    # Double-check locking pattern
    if _instinct_system is None:
        with _instinct_system_lock:
            if _instinct_system is None:
                _instinct_system = InstinctSystem(data_dir)
    return _instinct_system


if __name__ == "__main__":
    system = InstinctSystem()
    
    # Tester l'apprentissage
    system.learn(
        pattern="utilisateur demande de lire un fichier",
        response="Utiliser read_file avec le chemin spécifié",
        was_successful=True,
        category="file_operations"
    )
    
    system.learn(
        pattern="utilisateur demande de chercher du code",
        response="Utiliser grep_search ou search_code",
        was_successful=True,
        category="code_search"
    )
    
    print(f"Stats: {system.get_stats()}")
    print(f"Summary:\n{system.format_instincts_summary()}")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
