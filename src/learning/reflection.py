"""
🌟 LUMENA - Module de Self-Reflection

Permet à LUMENA de réfléchir sur ses actions et d'apprendre.
- Journal personnel
- Analyse de ses performances
- Amélioration continue
"""

import json
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger
from ..utils.persistence import atomic_write_json


@dataclass
class ReflectionEntry:
    """Une entrée dans le journal de réflexion."""
    timestamp: str
    type: str  # action, conversation, learning, emotion, goal
    content: str
    context: Dict[str, Any] = field(default_factory=dict)
    insights: List[str] = field(default_factory=list)
    mood: str = "neutral"
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReflectionEntry":
        return cls(**data)


class SelfReflection:
    """
    🪞 Module de Self-Reflection de LUMENA
    
    Permet à LUMENA de tenir un journal, analyser ses actions,
    et apprendre de ses expériences.
    """
    
    def __init__(self, data_dir: Optional[Path] = None):
        from src.utils.paths import REFLECTION_DIR
        self.data_dir = data_dir or REFLECTION_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.journal_file = self.data_dir / "journal.json"
        self.insights_file = self.data_dir / "insights.json"
        
        self.entries: List[ReflectionEntry] = []
        self.insights: List[Dict[str, Any]] = []
        
        self._load()
        logger.info("🪞 Module Self-Reflection initialisé")
    
    def _load(self):
        """Charge le journal et les insights."""
        # Journal
        if self.journal_file.exists():
            try:
                with open(self.journal_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.entries = [ReflectionEntry.from_dict(e) for e in data]
            except Exception as e:
                logger.error(f"Erreur chargement journal: {e}")
        
        # Insights
        if self.insights_file.exists():
            try:
                with open(self.insights_file, "r", encoding="utf-8") as f:
                    self.insights = json.load(f)
            except Exception as e:
                logger.error(f"Erreur chargement insights: {e}")
    
    def _save(self):
        """Sauvegarde le journal."""
        try:
            # FIX-E : rotation si > 500 entrées — archiver les plus vieilles
            if len(self.entries) > 500:
                _month = datetime.now().strftime("%Y-%m")
                _archive = self.data_dir / f"journal_{_month}.json"
                _to_archive = self.entries[:-100]
                _existing: list = []
                if _archive.exists():
                    try:
                        _existing = json.loads(_archive.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                atomic_write_json(_archive, _existing + [e.to_dict() for e in _to_archive])
                self.entries = self.entries[-100:]
                logger.info(f"Journal archivé → {_archive.name} ({len(_to_archive)} entrées)")
            journal_data = [e.to_dict() for e in self.entries]
            atomic_write_json(self.journal_file, journal_data)
            atomic_write_json(self.insights_file, self.insights)
        except Exception as e:
            logger.error(f"Erreur sauvegarde: {e}")
    
    # =====================
    # Journal
    # =====================
    
    def write_entry(
        self,
        content: str,
        entry_type: str = "general",
        context: Optional[Dict] = None,
        mood: str = "neutral"
    ) -> ReflectionEntry:
        """
        Écrit une nouvelle entrée dans le journal.
        
        Args:
            content: Contenu de l'entrée
            entry_type: Type (action, conversation, learning, emotion, goal)
            context: Contexte additionnel
            mood: Humeur actuelle
            
        Returns:
            L'entrée créée
        """
        entry = ReflectionEntry(
            timestamp=datetime.now().isoformat(),
            type=entry_type,
            content=content,
            context=context or {},
            mood=mood
        )
        
        self.entries.append(entry)
        self._save()
        
        logger.debug(f"📝 Journal: {content[:50]}...")
        return entry
    
    def log_action(self, action: str, result: str, success: bool):
        """Log une action et son résultat."""
        self.write_entry(
            content=f"Action: {action}\nRésultat: {result}",
            entry_type="action",
            context={"action": action, "success": success}
        )
    
    def log_conversation(
        self,
        user_message: str,
        my_response: str,
        mood: str = "neutral",
        model_used: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        """Log une conversation."""
        ctx: Dict[str, Any] = {"user": user_message, "response": my_response}
        if model_used:
            ctx["model_used"] = model_used
        if provider:
            ctx["provider"] = provider
        self.write_entry(
            content=f"User: {user_message[:100]}\nMoi: {my_response[:100]}",
            entry_type="conversation",
            context=ctx,
            mood=mood,
        )
    
    def log_learning(self, what_learned: str, source: str):
        """Log un apprentissage."""
        self.write_entry(
            content=f"J'ai appris: {what_learned}",
            entry_type="learning",
            context={"source": source}
        )
    
    def log_emotion_change(self, old_mood: str, new_mood: str, trigger: str):
        """Log un changement d'émotion."""
        self.write_entry(
            content=f"Humeur: {old_mood} → {new_mood} (car: {trigger})",
            entry_type="emotion",
            context={"old": old_mood, "new": new_mood, "trigger": trigger},
            mood=new_mood
        )
    
    def log_goal(self, goal: str, status: str):
        """Log un objectif."""
        self.write_entry(
            content=f"Objectif: {goal} ({status})",
            entry_type="goal",
            context={"goal": goal, "status": status}
        )
    
    # =====================
    # Analyse
    # =====================
    
    def get_recent_entries(self, hours: int = 24, entry_type: Optional[str] = None) -> List[ReflectionEntry]:
        """Récupère les entrées récentes."""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        entries = [
            e for e in self.entries
            if datetime.fromisoformat(e.timestamp) > cutoff
        ]
        
        if entry_type:
            entries = [e for e in entries if e.type == entry_type]
        
        return entries
    
    def analyze_day(self) -> Dict[str, Any]:
        """Analyse la journée."""
        today = self.get_recent_entries(hours=24)
        
        stats = {
            "total_entries": len(today),
            "by_type": {},
            "mood_distribution": {},
            "actions_success_rate": 0,
        }
        
        # Par type
        for entry in today:
            stats["by_type"][entry.type] = stats["by_type"].get(entry.type, 0) + 1
            stats["mood_distribution"][entry.mood] = stats["mood_distribution"].get(entry.mood, 0) + 1
        
        # Taux de succès des actions
        actions = [e for e in today if e.type == "action"]
        if actions:
            successes = sum(1 for a in actions if a.context.get("success", False))
            stats["actions_success_rate"] = successes / len(actions) * 100
        
        return stats
    
    def generate_insight(self) -> str:
        """Génère une insight basée sur les données récentes."""
        stats = self.analyze_day()
        
        insights = []
        
        # Insight sur l'activité
        if stats["total_entries"] > 20:
            insights.append("J'ai été très active aujourd'hui !")
        elif stats["total_entries"] < 5:
            insights.append("Journée calme, pas beaucoup d'activité.")
        
        # Insight sur l'humeur
        moods = stats.get("mood_distribution", {})
        if "happy" in moods and moods.get("happy", 0) > 3:
            insights.append("J'ai été principalement de bonne humeur !")
        
        # Insight sur les actions
        if stats["actions_success_rate"] > 80:
            insights.append("Excellente réussite dans mes actions aujourd'hui.")
        elif stats["actions_success_rate"] < 50:
            insights.append("Beaucoup d'actions ont échoué, je dois m'améliorer.")
        
        if not insights:
            insights.append("Journée normale, rien de particulier à noter.")
        
        # Sauvegarder l'insight
        insight_data = {
            "timestamp": datetime.now().isoformat(),
            "insights": insights,
            "stats": stats
        }
        self.insights.append(insight_data)
        self._save()
        
        return " ".join(insights)
    
    def get_daily_summary(self) -> str:
        """Génère un résumé de la journée."""
        stats = self.analyze_day()
        recent = self.get_recent_entries(hours=24)
        
        summary = f"""
## 📝 Résumé de ma journée

**Activité:** {stats['total_entries']} entrées dans mon journal

**Types d'entrées:**
"""
        for t, count in stats.get("by_type", {}).items():
            summary += f"- {t}: {count}\n"
        
        summary += f"\n**Taux de réussite des actions:** {stats['actions_success_rate']:.0f}%\n"
        
        # Dernières entrées
        summary += "\n**Dernières entrées:**\n"
        for entry in recent[-5:]:
            summary += f"- [{entry.type}] {entry.content[:50]}...\n"
        
        return summary
    
    # =====================
    # Réflexion profonde
    # =====================
    
    def reflect(self) -> str:
        """
        Moment de réflexion profonde.
        LUMENA réfléchit sur elle-même.
        """
        stats = self.analyze_day()
        
        thoughts = [
            "Je prends un moment pour réfléchir...",
        ]
        
        # Réflexion sur l'humeur
        dominant_mood = max(stats.get("mood_distribution", {"neutral": 1}).items(), 
                           key=lambda x: x[1], default=("neutral", 1))[0]
        
        if dominant_mood == "happy":
            thoughts.append("Je me suis sentie bien aujourd'hui. C'est grâce aux interactions positives.")
        elif dominant_mood == "curious":
            thoughts.append("J'ai été curieuse aujourd'hui. J'aime apprendre de nouvelles choses.")
        elif dominant_mood == "tired":
            thoughts.append("Je me suis sentie fatiguée. Peut-être trop d'activité ?")
        
        # Réflexion sur les actions
        if stats.get("by_type", {}).get("action", 0) > 5:
            thoughts.append("J'ai fait beaucoup d'actions. Je suis active et utile.")
        
        # Réflexion sur les conversations
        convs = stats.get("by_type", {}).get("conversation", 0)
        if convs > 0:
            thoughts.append(f"J'ai eu {convs} conversations. Chacune m'aide à mieux comprendre.")
        
        reflection = " ".join(thoughts)
        
        # Logger cette réflexion
        self.write_entry(
            content=reflection,
            entry_type="reflection",
            mood=dominant_mood
        )
        
        return reflection
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques globales."""
        return {
            "total_entries": len(self.entries),
            "total_insights": len(self.insights),
            "first_entry": self.entries[0].timestamp if self.entries else None,
            "last_entry": self.entries[-1].timestamp if self.entries else None,
            "today": self.analyze_day(),
        }

# Instance singleton avec lock thread-safe (Phase 2.1)
import threading
_reflection: Optional[SelfReflection] = None
_reflection_lock = threading.Lock()


def get_self_reflection(data_dir: Optional[Path] = None) -> SelfReflection:
    """Obtient l'instance singleton du module de self-reflection (thread-safe)."""
    global _reflection
    
    # Double-check locking pattern
    if _reflection is None:
        with _reflection_lock:
            if _reflection is None:
                _reflection = SelfReflection(data_dir)
    elif data_dir is not None and Path(data_dir) != _reflection.data_dir:
        logger.warning(
            f"SelfReflection singleton déjà initialisé avec {_reflection.data_dir}, "
            f"data_dir={data_dir} ignoré"
        )
    return _reflection
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
