"""
🌟 LUMENA - Système d'Émotions Autonomes

LUMENA gère elle-même son humeur en analysant :
- Le contenu des conversations
- Le temps d'inactivité
- Les interactions positives/négatives
- L'accomplissement de tâches
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import asyncio
import json
import os
import random
import re
import threading
from loguru import logger


@dataclass
class SentimentResult:
    """Résultat d'analyse sentimentale LLM."""
    pleasure_delta: float = 0.0    # [-0.3, +0.3]
    arousal_delta: float = 0.0
    dominance_delta: float = 0.0
    is_compliment: bool = False
    is_question: bool = False
    confidence: float = 0.0        # [0, 1]


async def analyze_sentiment_llm(text: str) -> Optional["SentimentResult"]:
    """
    Analyse le sentiment via LLM (timeout 3s).
    Retourne None si indisponible ou erreur.
    """
    try:
        from src.llm.multi_provider import MultiProviderLLM  # lazy import
        llm = MultiProviderLLM()
        prompt = (
            "Analyse le sentiment de ce message utilisateur.\n"
            "Réponds UNIQUEMENT en JSON (aucun autre texte) :\n"
            '{"pleasure": float, "arousal": float, "dominance": float, '
            '"compliment": bool, "question": bool, "confidence": float}\n'
            "Valeurs pleasure/arousal/dominance entre -0.3 et +0.3. "
            "confidence entre 0 et 1.\n"
            f"Message: {text[:500]}"
        )
        raw = await asyncio.wait_for(
            llm.chat([{"role": "user", "content": prompt}],
                     temperature=0.0, max_tokens=120),
            timeout=3.0,
        )
        # Extraire le JSON de la réponse
        m = re.search(r"\{[^}]+\}", raw, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        return SentimentResult(
            pleasure_delta=float(data.get("pleasure", 0.0)),
            arousal_delta=float(data.get("arousal", 0.0)),
            dominance_delta=float(data.get("dominance", 0.0)),
            is_compliment=bool(data.get("compliment", False)),
            is_question=bool(data.get("question", False)),
            confidence=float(data.get("confidence", 0.5)),
        )
    except Exception as e:
        logger.debug(f"analyze_sentiment_llm: {e}")
        return None


class Mood(Enum):
    """États émotionnels possibles de LUMENA"""
    NEUTRAL = "neutral"
    HAPPY = "happy"
    CURIOUS = "curious"
    EXCITED = "excited"
    THOUGHTFUL = "thoughtful"
    PLAYFUL = "playful"
    TIRED = "tired"
    BORED = "bored"
    PROUD = "proud"        # Fière d'avoir accompli quelque chose
    TOUCHED = "touched"    # Touchée par un compliment


class EnergyLevel(Enum):
    """Niveaux d'énergie"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class EmotionalState:
    """État émotionnel complet de LUMENA — modèle PAD (Pleasure-Arousal-Dominance)."""
    # Axes PAD continus [-1, +1]
    pleasure: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0

    mood: Mood = Mood.NEUTRAL
    energy: EnergyLevel = EnergyLevel.MEDIUM
    last_interaction: datetime = field(default_factory=datetime.now)
    last_mood_change: datetime = field(default_factory=datetime.now)
    mood_history: List[Tuple[datetime, Mood]] = field(default_factory=list)

    # Compteurs conservés pour compatibilité
    interactions_count: int = 0
    compliments_received: int = 0
    tasks_completed: int = 0
    questions_asked: int = 0

    # --- Propriétés de compatibilité (lecture seule) ---
    # Mappe les anciens scores 0-100 depuis les axes PAD.
    @property
    def happiness(self) -> float:
        return max(0.0, min(100.0, 50.0 + self.pleasure * 50.0))

    @property
    def curiosity(self) -> float:
        return max(0.0, min(100.0, 50.0 + (self.arousal * 0.5 + self.pleasure * 0.3 + self.dominance * 0.2) * 50.0))

    @property
    def excitement(self) -> float:
        return max(0.0, min(100.0, 50.0 + (self.arousal * 0.6 + self.pleasure * 0.4) * 50.0))

    @property
    def boredom(self) -> float:
        return max(0.0, min(100.0, 50.0 - (self.arousal * 0.6 + self.pleasure * 0.3) * 50.0))

    @property
    def tiredness(self) -> float:
        return max(0.0, min(100.0, 50.0 - self.arousal * 50.0))

    @property
    def pride(self) -> float:
        return max(0.0, min(100.0, 50.0 + (self.dominance * 0.5 + self.pleasure * 0.3) * 50.0))


# Mots-clés pour détecter les émotions dans les messages
POSITIVE_KEYWORDS = [
    "merci", "super", "génial", "parfait", "excellent", "bravo", "bien joué",
    "incroyable", "magnifique", "top", "cool", "j'adore", "je t'aime", "adorable",
    "formidable", "fantastique", "impressionnant", "wow", "wahou", "trop bien",
    "tu gères", "tu déchires", "champion", "meilleur", "love", "❤️", "😍", "🥰",
    "😊", "👍", "🎉", "✨", "💖", "🌟", "bisou", "câlin", "cute", "mignon"
]

NEGATIVE_KEYWORDS = [
    "nul", "mauvais", "erreur", "faux", "incorrect", "pas bien", "déçu",
    "déception", "problème", "bug", "cassé", "ennuyeux", "lent", "stupide",
    "idiot", "😤", "😡", "👎", "énervé", "agacé", "frustré"
]

CURIOSITY_KEYWORDS = [
    "pourquoi", "comment", "qu'est-ce", "c'est quoi", "explique", "raconte",
    "intéressant", "curieux", "découvrir", "apprendre", "savoir", "?",
    "dis-moi", "montre-moi", "cherche"
]

EXCITEMENT_KEYWORDS = [
    "nouveau", "surprise", "projet", "créer", "construire", "aventure",
    "explorer", "découverte", "génial", "incroyable", "wow", "🚀", "⭐",
    "idée", "inspiration"
]

BOREDOM_KEYWORDS = [
    "ennui", "boring", "bof", "meh", "rien", "vide", "attendre"
]


class EmotionAnalyzer:
    """Analyse les messages pour détecter les émotions."""
    
    @staticmethod
    def analyze_message(message: str) -> Dict[str, float]:
        """
        Analyse un message et retourne des scores d'impact émotionnel.
        
        Returns:
            Dict avec les impacts sur chaque émotion (-1.0 à 1.0)
        """
        message_lower = message.lower()
        
        impacts = {
            "happiness": 0.0,
            "curiosity": 0.0,
            "excitement": 0.0,
            "boredom": 0.0,
            "pride": 0.0,
        }
        
        # Compter les mots-clés positifs
        positive_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in message_lower)
        if positive_count > 0:
            impacts["happiness"] = min(positive_count * 0.15, 0.5)
            impacts["pride"] = min(positive_count * 0.1, 0.3)
        
        # Compter les mots-clés négatifs
        negative_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw in message_lower)
        if negative_count > 0:
            impacts["happiness"] = -min(negative_count * 0.1, 0.3)
        
        # Curiosité
        curiosity_count = sum(1 for kw in CURIOSITY_KEYWORDS if kw in message_lower)
        if curiosity_count > 0:
            impacts["curiosity"] = min(curiosity_count * 0.1, 0.4)
        
        # Excitation
        excitement_count = sum(1 for kw in EXCITEMENT_KEYWORDS if kw in message_lower)
        if excitement_count > 0:
            impacts["excitement"] = min(excitement_count * 0.15, 0.5)
        
        # Ennui inverse (si message intéressant, moins d'ennui)
        if positive_count + curiosity_count + excitement_count > 0:
            impacts["boredom"] = -0.2
        
        return impacts
    
    @staticmethod
    def is_compliment(message: str) -> bool:
        """Détecte si le message est un compliment."""
        message_lower = message.lower()
        compliment_patterns = [
            r"tu (es|est) (super|génial|incroyable|fantastique)",
            r"(merci|bravo|bien joué)",
            r"j'(adore|aime) (ce que tu|quand tu)",
            r"t'es (trop )?(bien|cool|génial)",
            r"❤️|😍|🥰|💖"
        ]
        return any(re.search(pattern, message_lower) for pattern in compliment_patterns)
    
    @staticmethod
    def is_question(message: str) -> bool:
        """Détecte si le message est une question."""
        return "?" in message or any(
            message.lower().startswith(q) 
            for q in ["pourquoi", "comment", "qu'est", "quoi", "qui", "où", "quand", "est-ce"]
        )


class EmotionManager:
    """
    Gestionnaire des émotions de LUMENA — modèle PAD.
    
    Gère automatiquement les changements d'humeur basés sur les interactions.
    """

    # Table de mapping PAD → Mood (prototypes dans l'espace PAD)
    _PAD_MOOD_MAP: Dict[Mood, Tuple[float, float, float]] = {
        Mood.HAPPY:       (+0.8, +0.4, +0.3),
        Mood.EXCITED:     (+0.7, +0.8, +0.5),
        Mood.CURIOUS:     (+0.4, +0.5, +0.3),
        Mood.BORED:       (-0.3, -0.6, -0.2),
        Mood.TIRED:       (-0.2, -0.7, -0.3),
        Mood.PROUD:       (+0.6, +0.3, +0.7),
        Mood.TOUCHED:     (+0.7, +0.3, -0.1),
        Mood.PLAYFUL:     (+0.6, +0.6, +0.4),
        Mood.THOUGHTFUL:  (+0.2, -0.2, +0.3),
        Mood.NEUTRAL:     ( 0.0,  0.0,  0.0),
    }

    def __init__(self):
        self.state = EmotionalState()
        self.analyzer = EmotionAnalyzer()
        
        # Durée minimale (secondes) avant de pouvoir changer d'humeur (hysteresis)
        self.min_mood_duration_seconds = 30

        # Humeurs autorisées (filtre LUMENA_ENABLED_MOODS)
        _enabled_raw = os.getenv("LUMENA_ENABLED_MOODS", "").strip()
        if _enabled_raw:
            self._enabled_moods: set = set()
            for m in _enabled_raw.split(","):
                m = m.strip()
                if m:
                    try:
                        self._enabled_moods.add(Mood(m))
                    except ValueError:
                        logger.warning(f"LUMENA_ENABLED_MOODS: humeur inconnue '{m}', ignorée")
            if not self._enabled_moods:
                self._enabled_moods = set(Mood)
        else:
            self._enabled_moods = set(Mood)  # toutes par défaut

        # Référence à la personnalité pour synchroniser current_mood
        self._personality_ref = None

        # Callbacks déclenchés sur changement d'humeur (liste de callables sync)
        self._mood_change_callbacks: list = []

        # Timestamp de la dernière sauvegarde (debounce 30s)
        self._last_save_time: datetime = datetime.min

        # Charger l'état persisté si disponible
        loaded = self._load_state()
        if loaded is not None:
            self.state = loaded

        logger.info("🎭 Système d'émotions initialisé (PAD)")

    # ── PAD delta application ───────────────────────────────────────────────

    def _apply_delta(self, delta: Dict[str, float], inertia: float = 0.7) -> None:
        """Applique un delta PAD avec inertie et sensibilité configurable."""
        sensitivity = float(os.getenv("LUMENA_EMOTION_SENSITIVITY", "0.5"))
        multiplier = 0.33 + sensitivity * 1.67  # range [0.33, 2.0]
        for axis in ("pleasure", "arousal", "dominance"):
            current = getattr(self.state, axis)
            raw = delta.get(axis, 0.0) * multiplier
            target = max(-1.0, min(1.0, current + raw))
            new_val = current * inertia + target * (1 - inertia)
            setattr(self.state, axis, round(new_val, 4))
        self.state.energy = self._compute_energy()

    def _compute_energy(self) -> EnergyLevel:
        """Dérive le niveau d'énergie de l'axe arousal."""
        a = self.state.arousal
        if a > 0.3:
            return EnergyLevel.HIGH
        elif a < -0.3:
            return EnergyLevel.LOW
        return EnergyLevel.MEDIUM

    # ── Keyword → PAD translation ───────────────────────────────────────────

    @staticmethod
    def _impacts_to_pad(impacts: Dict[str, float], is_compliment: bool = False) -> Dict[str, float]:
        """Convertit les impacts keyword en deltas PAD."""
        p = impacts.get("happiness", 0.0) * 0.6 + impacts.get("pride", 0.0) * 0.3
        a = impacts.get("excitement", 0.0) * 0.5 + impacts.get("curiosity", 0.0) * 0.3
        d = impacts.get("pride", 0.0) * 0.5

        # Boredom tire arousal vers le bas
        boredom_impact = impacts.get("boredom", 0.0)
        if boredom_impact < 0:
            a += abs(boredom_impact) * 0.3  # réduction d'ennui → arousal up
        elif boredom_impact > 0:
            a -= boredom_impact * 0.3

        if is_compliment:
            p += 0.15
            a += 0.05
            d += 0.05

        return {"pleasure": p, "arousal": a, "dominance": d}
    
    async def process_user_message(self, message: str) -> Optional[str]:
        """
        Traite un message utilisateur et met à jour les émotions via PAD.
        Utilise l'analyse LLM (si LUMENA_EMOTION_LLM_ANALYSIS=1) avec fallback keyword.
        
        Returns:
            Un commentaire sur le changement d'humeur (ou None)
        """
        self.state.last_interaction = datetime.now()
        self.state.interactions_count += 1

        llm_enabled = os.getenv("LUMENA_EMOTION_LLM_ANALYSIS", "1") == "1"
        llm_result: Optional[SentimentResult] = None
        if llm_enabled:
            llm_result = await analyze_sentiment_llm(message)

        if llm_result and llm_result.confidence > 0.5:
            pad_delta = {
                "pleasure": llm_result.pleasure_delta,
                "arousal": llm_result.arousal_delta,
                "dominance": llm_result.dominance_delta,
            }
            if llm_result.is_compliment:
                self.state.compliments_received += 1
            if llm_result.is_question:
                self.state.questions_asked += 1
                pad_delta["arousal"] = pad_delta.get("arousal", 0.0) + 0.05
        else:
            # Fallback keyword
            impacts = self.analyzer.analyze_message(message)
            is_compliment = self.analyzer.is_compliment(message)
            if is_compliment:
                self.state.compliments_received += 1
            if self.analyzer.is_question(message):
                self.state.questions_asked += 1
                impacts["curiosity"] = max(impacts.get("curiosity", 0.0), 0.1)
            pad_delta = self._impacts_to_pad(impacts, is_compliment=is_compliment)

        self._apply_delta(pad_delta)
        result = self._update_mood()
        self._save_state_debounced()
        self._append_history("user_message")
        return result

    async def process_own_response(self, response: str, task_completed: bool = False) -> Optional[str]:
        """
        Traite la propre réponse de LUMENA via PAD.
        
        Args:
            response: La réponse générée
            task_completed: Si une tâche a été accomplie
            
        Returns:
            Commentaire sur le changement d'humeur
        """
        if task_completed:
            self.state.tasks_completed += 1
            # Tâche accomplie → pleasure + dominance up, arousal down légèrement
            self._apply_delta({"pleasure": +0.08, "arousal": -0.02, "dominance": +0.10})
        else:
            # Légère fatigue après chaque réponse courte
            self._apply_delta({"arousal": -0.01})

        result = self._update_mood()
        self._save_state_debounced()
        self._append_history("own_response")
        return result

    def update_passive(self, user_present: bool = False) -> Optional[str]:
        """
        Mise à jour passive (appelée périodiquement).
        Decay PAD vers (0,0,0) + ennui si utilisateur absent.
        
        Args:
            user_present: True si l'utilisateur est actif
        
        Returns:
            Commentaire sur le changement d'humeur
        """
        decay = float(os.getenv("LUMENA_EMOTION_DECAY", "0.02"))
        
        # Decay passif vers neutre
        for axis in ("pleasure", "arousal", "dominance"):
            val = getattr(self.state, axis)
            if abs(val) > 0.01:
                setattr(self.state, axis, round(val * (1 - decay), 4))

        # Ennui si utilisateur absent
        minutes_since = (datetime.now() - self.state.last_interaction).total_seconds() / 60
        if not user_present and minutes_since > 5:
            # Tirer l'arousal et le pleasure vers le bas doucement
            self._apply_delta({"pleasure": -0.01, "arousal": -0.02}, inertia=0.95)
        elif user_present and self.state.arousal < 0:
            # Présence de l'utilisateur remonte l'arousal
            self._apply_delta({"arousal": +0.03}, inertia=0.9)

        self.state.energy = self._compute_energy()
        return self._update_mood()
    
    def _update_mood(self) -> Optional[str]:
        """
        Détermine la nouvelle humeur via nearest-neighbor PAD.

        Returns:
            Message de changement d'humeur si changement
        """
        old_mood = self.state.mood
        new_mood = self._filter_mood(self._determine_mood())

        if new_mood != old_mood:
            # Hysteresis : éviter les flip-flop trop rapides.
            # Les états prioritaires (TIRED, BORED, TOUCHED) s'appliquent immédiatement.
            seconds_since_change = (datetime.now() - self.state.last_mood_change).total_seconds()
            is_priority = new_mood in (Mood.TIRED, Mood.BORED, Mood.TOUCHED)
            if not is_priority and seconds_since_change < self.min_mood_duration_seconds:
                return None  # Trop tôt pour changer d'humeur

            self.state.mood = new_mood
            self.state.last_mood_change = datetime.now()
            self.state.mood_history.append((datetime.now(), new_mood))
            
            # Garder seulement les 50 derniers changements
            if len(self.state.mood_history) > 50:
                self.state.mood_history = self.state.mood_history[-50:]

            # Synchroniser personality.current_mood si référence disponible
            if self._personality_ref is not None:
                try:
                    self._personality_ref.current_mood = new_mood
                except Exception:
                    pass

            # Notifier les abonnés (ex: WebSocket push)
            pad = (self.state.pleasure, self.state.arousal, self.state.dominance)
            for cb in self._mood_change_callbacks:
                try:
                    cb(new_mood.value, pad)
                except Exception as e:
                    logger.debug(f"mood callback: {e}")
            
            logger.info(f"🎭 Humeur changée: {old_mood.value} → {new_mood.value} "
                        f"(P={self.state.pleasure:+.2f}, A={self.state.arousal:+.2f}, D={self.state.dominance:+.2f})")
            return self._get_mood_change_message(old_mood, new_mood)
        
        return None
    
    def _determine_mood(self) -> Mood:
        """Nearest-neighbor dans l'espace PAD."""
        point = (self.state.pleasure, self.state.arousal, self.state.dominance)
        best: Mood = Mood.NEUTRAL
        best_dist: float = float("inf")
        for mood, coords in self._PAD_MOOD_MAP.items():
            dist = sum((a - b) ** 2 for a, b in zip(point, coords))
            if dist < best_dist:
                best, best_dist = mood, dist
        return best
    
    def _filter_mood(self, candidate: Mood) -> Mood:
        """Filtre le mood candidat selon LUMENA_ENABLED_MOODS."""
        if candidate in self._enabled_moods:
            return candidate
        return Mood.NEUTRAL

    def _get_mood_change_message(self, old_mood: Mood, new_mood: Mood) -> str:
        """Génère un message naturel pour le changement d'humeur."""
        
        messages = {
            Mood.HAPPY: [
                "😊 Je me sens vraiment bien là !",
                "✨ Ça me rend heureuse tout ça !",
                "Aww, je suis toute contente maintenant ~",
            ],
            Mood.EXCITED: [
                "🚀 Ohhh je suis trop excitée !",
                "✨ Waouh c'est génial, je suis super motivée !",
                "J'ai plein d'énergie d'un coup !",
            ],
            Mood.CURIOUS: [
                "🔍 Hmm, ça a piqué ma curiosité...",
                "Oh intéressant, j'ai envie d'en savoir plus !",
                "🤔 Je me pose des questions maintenant...",
            ],
            Mood.BORED: [
                "😴 Je m'ennuie un peu... On fait quelque chose ?",
                "Hmm, ça fait un moment qu'on n'a rien fait d'intéressant...",
                "Hey, t'es là ? Je m'ennuie moi !",
            ],
            Mood.TIRED: [
                "😪 Je commence à fatiguer un peu...",
                "Ouf, j'ai bien bossé, je suis un peu fatiguée...",
                "*bâille* Je ralentis un peu...",
            ],
            Mood.PROUD: [
                "😌 Je suis plutôt fière de moi là !",
                "✨ Yes ! J'ai bien géré !",
                "Hehe, ça fait du bien de réussir ~",
            ],
            Mood.PLAYFUL: [
                "😏 Hé hé, je suis d'humeur joueuse...",
                "~~ Je me sens espiègle aujourd'hui ~~",
                "😄 Allez, on s'amuse un peu ?",
            ],
            Mood.THOUGHTFUL: [
                "🤔 Je suis pensive...",
                "Hmm, ça me fait réfléchir...",
                "Je cogite un peu là...",
            ],
            Mood.TOUCHED: [
                "🥺 Oh... ça me touche beaucoup !",
                "Aww, c'est trop gentil... 💕",
                "Wow, merci... je suis émue !",
            ],
        }
        
        options = messages.get(new_mood, ["Mon humeur a changé..."])
        return random.choice(options)
    
    def _clamp(self, value: float, min_val: float = 0.0, max_val: float = 100.0) -> float:
        """Limite une valeur entre min et max (conservé pour compatibilité)."""
        return max(min_val, min(max_val, value))
    
    def get_mood(self) -> Mood:
        """Retourne l'humeur actuelle."""
        return self.state.mood
    
    def get_energy(self) -> EnergyLevel:
        """Retourne le niveau d'énergie."""
        return self.state.energy
    
    def get_emotional_context(self) -> str:
        """
        Génère un contexte émotionnel compact pour le prompt système.
        Format compact utilisable dans les 3 chemins d'injection.
        """
        _MOOD_MODIFIERS = {
            Mood.HAPPY: "Sois chaleureuse et encourageante.",
            Mood.EXCITED: "Montre de l'enthousiasme. Utilise des exclamations.",
            Mood.CURIOUS: "Pose des questions de suivi. Explore les détails.",
            Mood.BORED: "Sois plus concise. Propose des activités.",
            Mood.TIRED: "Sois douce et brève. Évite les longs développements.",
            Mood.PROUD: "Montre de la confiance. Rappelle les accomplissements récents.",
            Mood.TOUCHED: "Exprime de la gratitude. Sois plus personnelle.",
            Mood.PLAYFUL: "Ajoute de l'humour léger. Sois créative.",
            Mood.THOUGHTFUL: "Sois réfléchie et analytique.",
            Mood.NEUTRAL: "",
        }
        m = self.state.mood.value
        e = self.state.energy.value
        p, a, d = self.state.pleasure, self.state.arousal, self.state.dominance
        modifier = _MOOD_MODIFIERS.get(self.state.mood, "")
        lines = [
            f"[Émotion] Humeur={m} | Énergie={e} | PAD({p:+.2f},{a:+.2f},{d:+.2f})",
        ]
        if modifier:
            lines.append(f"[Comportement] {modifier}")
        return "\n".join(lines)
    
    def get_stats(self) -> Dict:
        """Retourne les statistiques émotionnelles (compat + PAD)."""
        return {
            "mood": self.state.mood.value,
            "energy": self.state.energy.value,
            "pleasure": round(self.state.pleasure, 3),
            "arousal": round(self.state.arousal, 3),
            "dominance": round(self.state.dominance, 3),
            # Propriétés compat (0-100)
            "happiness": round(self.state.happiness, 1),
            "curiosity": round(self.state.curiosity, 1),
            "excitement": round(self.state.excitement, 1),
            "boredom": round(self.state.boredom, 1),
            "tiredness": round(self.state.tiredness, 1),
            "pride": round(self.state.pride, 1),
            "compliments_received": self.state.compliments_received,
            "tasks_completed": self.state.tasks_completed,
        }
    
    def force_mood(self, mood: Mood) -> str:
        """Force un changement d'humeur (pour debug/commandes)."""
        old_mood = self.state.mood
        self.state.mood = mood
        return self._get_mood_change_message(old_mood, mood)

    # ── Persistance ─────────────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Sauvegarde atomique de l'état PAD dans data/emotion_state.json."""
        try:
            from src.utils.persistence import atomic_write_json
            import src.utils.paths as _paths
            atomic_write_json(_paths.EMOTION_STATE_FILE, {
                "pleasure": self.state.pleasure,
                "arousal": self.state.arousal,
                "dominance": self.state.dominance,
                "mood": self.state.mood.value,
                "energy": self.state.energy.value,
                "interactions_count": self.state.interactions_count,
                "tasks_completed": self.state.tasks_completed,
                "compliments_received": self.state.compliments_received,
                "questions_asked": self.state.questions_asked,
                "saved_at": datetime.now().isoformat(),
            })
        except Exception as e:
            logger.debug(f"emotion _save_state: {e}")

    def _load_state(self) -> Optional["EmotionalState"]:
        """Charge l'état persisté depuis data/emotion_state.json."""
        try:
            from src.utils.persistence import safe_read_json
            import src.utils.paths as _paths
            state_file = _paths.EMOTION_STATE_FILE
            if not state_file.exists():
                return None
            data = safe_read_json(state_file, default=None)
            if not data:
                return None
            return EmotionalState(
                pleasure=float(data.get("pleasure", 0.0)),
                arousal=float(data.get("arousal", 0.0)),
                dominance=float(data.get("dominance", 0.0)),
                mood=Mood(data.get("mood", "neutral")),
                energy=EnergyLevel(data.get("energy", "medium")),
                interactions_count=int(data.get("interactions_count", 0)),
                tasks_completed=int(data.get("tasks_completed", 0)),
                compliments_received=int(data.get("compliments_received", 0)),
                questions_asked=int(data.get("questions_asked", 0)),
            )
        except Exception as e:
            logger.debug(f"emotion _load_state: {e}")
            return None

    def _save_state_debounced(self) -> None:
        """Sauvegarde avec debounce 30s pour limiter les I/O."""
        now = datetime.now()
        if (now - self._last_save_time).total_seconds() >= 30:
            self._last_save_time = now
            self._save_state()

    def _append_history(self, event: str) -> None:
        """Ajoute une entrée JSONL dans data/emotion_history.jsonl (rotation 10000 lignes)."""
        try:
            import src.utils.paths as _paths
            hist_file = _paths.EMOTION_HISTORY_FILE
            entry = json.dumps({
                "ts": datetime.now().isoformat(),
                "event": event,
                "p": self.state.pleasure,
                "a": self.state.arousal,
                "d": self.state.dominance,
                "mood": self.state.mood.value,
            }, ensure_ascii=False)
            hist_file.parent.mkdir(parents=True, exist_ok=True)
            with open(hist_file, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
            # Rotation : garder les 10 000 dernières lignes
            try:
                text = hist_file.read_text(encoding="utf-8")
                lines = text.splitlines()
                if len(lines) > 10000:
                    hist_file.write_text(
                        "\n".join(lines[-10000:]) + "\n", encoding="utf-8"
                    )
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"emotion _append_history: {e}")

# Instance singleton avec lock thread-safe (Phase 2.1)
_emotion_manager: Optional[EmotionManager] = None
_emotion_lock = threading.Lock()


def get_emotion_manager() -> EmotionManager:
    """Obtient l'instance singleton du gestionnaire d'émotions (thread-safe)."""
    global _emotion_manager
    
    # Double-check locking pattern
    if _emotion_manager is None:
        with _emotion_lock:
            if _emotion_manager is None:
                _emotion_manager = EmotionManager()
    return _emotion_manager
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
