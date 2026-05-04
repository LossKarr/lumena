"""
🎬 LUMENA — Video Memory System

Mémoire persistante spécialisée pour la génération vidéo.
Miroir du ReflexionStore + SuccessStore mais adapté au domaine vidéo.

Deux stores :
  - VideoReflexionStore : leçons apprises des échecs de rendu vidéo
  - VideoSuccessStore : patterns de réussite réutilisables pour la vidéo

Stockage JSONL append-only :
  - data/learning/video_reflexions.jsonl
  - data/learning/video_successes.jsonl

Retrieval par similarité Jaccard (zéro dépendance externe).
Injection automatique dans les prompts de génération vidéo.

Inspiré du Case-Based Reasoning : chaque vidéo générée (succès ou échec)
enrichit la base de connaissances pour les futures générations.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Iterable

from loguru import logger


_UTC = timezone.utc


def _now_iso() -> str:
    return datetime.now(_UTC).isoformat(timespec="seconds")


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt


# ── Tokenizer (aligné sur le reste du learning system) ────────────────────────

_TOKEN_RE = re.compile(r"[a-zA-Zàâäéèêëïîôöùûüÿçñ0-9_]+", re.UNICODE)
_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "à", "au",
    "aux", "en", "pour", "par", "sur", "sous", "dans", "avec", "sans", "ce",
    "cette", "ces", "son", "sa", "ses", "mon", "ma", "mes", "est", "sont",
    "the", "a", "an", "of", "and", "or", "to", "for", "in", "on", "with",
    "is", "are", "be", "was", "were", "it", "this", "that", "not", "no",
    "que", "qui", "si", "pas", "plus", "moins", "donc", "alors",
}


def _tokenize(text: str) -> set[str]:
    return {
        t.lower()
        for t in _TOKEN_RE.findall(text or "")
        if t.lower() not in _STOPWORDS and len(t) > 1
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ══════════════════════════════════════════════════════════════════════════════
# VIDEO REFLEXION — Leçons apprises des échecs vidéo
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class VideoReflexion:
    """Une leçon apprise d'un échec de génération/rendu vidéo."""
    id: str
    error_type: str            # "syntax", "import", "render", "plan", "timeout"
    trigger: str               # Ce qui a déclenché l'erreur (description courte)
    lesson: str                # La leçon apprise (actionnable)
    apply_when: str            # Mots-clés contextuels pour retrieval
    model_family: str = ""     # "small", "medium", "large" — quand la leçon est model-specific
    template_type: str = ""    # "social_short", "explainer", etc.
    confidence: float = 0.7
    uses: int = 0
    created_at: str = ""
    last_used_at: str = ""
    tags: List[str] = field(default_factory=list)

    @staticmethod
    def compute_id(lesson: str) -> str:
        norm = re.sub(r"\s+", " ", (lesson or "").strip().lower())[:400]
        return "vrefl_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "VideoReflexion":
        return cls(
            id=str(data.get("id") or cls.compute_id(str(data.get("lesson", "")))),
            error_type=str(data.get("error_type", "unknown")),
            trigger=str(data.get("trigger", "")),
            lesson=str(data.get("lesson", "")),
            apply_when=str(data.get("apply_when", "")),
            model_family=str(data.get("model_family", "")),
            template_type=str(data.get("template_type", "")),
            confidence=float(data.get("confidence", 0.7) or 0.7),
            uses=int(data.get("uses", 0) or 0),
            created_at=str(data.get("created_at", "")),
            last_used_at=str(data.get("last_used_at", "")),
            tags=list(data.get("tags") or []),
        )

    def to_compact(self) -> str:
        model_tag = f" [modèle: {self.model_family}]" if self.model_family else ""
        return f"⚠️ [{self.error_type}] {self.lesson}{model_tag}"


@dataclass
class VideoSuccess:
    """Un pattern de réussite pour la génération vidéo."""
    id: str
    template_type: str          # "social_short", "presentation", etc.
    description_hint: str       # Description simplifiée de la vidéo réussie
    approach: str               # Comment la vidéo a été générée avec succès
    scenes_count: int = 0       # Nombre de scènes
    model_used: str = ""        # Modèle qui a réussi
    model_family: str = ""      # "small", "medium", "large"
    iterations_needed: int = 1  # Nombre d'itérations pour réussir
    animations_used: List[str] = field(default_factory=list)  # spring, interpolate, etc.
    confidence: float = 0.7
    uses: int = 0
    created_at: str = ""
    last_used_at: str = ""
    tags: List[str] = field(default_factory=list)

    @staticmethod
    def compute_id(description_hint: str) -> str:
        norm = re.sub(r"\s+", " ", (description_hint or "").strip().lower())[:400]
        return "vsucc_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "VideoSuccess":
        return cls(
            id=str(data.get("id") or cls.compute_id(str(data.get("description_hint", "")))),
            template_type=str(data.get("template_type", "")),
            description_hint=str(data.get("description_hint", "")),
            approach=str(data.get("approach", "")),
            scenes_count=int(data.get("scenes_count", 0) or 0),
            model_used=str(data.get("model_used", "")),
            model_family=str(data.get("model_family", "")),
            iterations_needed=int(data.get("iterations_needed", 1) or 1),
            animations_used=list(data.get("animations_used") or []),
            confidence=float(data.get("confidence", 0.7) or 0.7),
            uses=int(data.get("uses", 0) or 0),
            created_at=str(data.get("created_at", "")),
            last_used_at=str(data.get("last_used_at", "")),
            tags=list(data.get("tags") or []),
        )

    def to_compact(self) -> str:
        anim_tag = f" [anim: {', '.join(self.animations_used[:3])}]" if self.animations_used else ""
        return f"✓ [{self.template_type}] {self.description_hint} → {self.approach}{anim_tag}"


# ══════════════════════════════════════════════════════════════════════════════
# STORES
# ══════════════════════════════════════════════════════════════════════════════


class VideoReflexionStore:
    """Store persistant de leçons vidéo (échecs)."""

    DEFAULT_PATH = Path("data/learning/video_reflexions.jsonl")

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path: Path = Path(path) if path else self.DEFAULT_PATH
        self._lock = threading.RLock()
        self._items: Dict[str, VideoReflexion] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            self._items.clear()
            if not self.path.exists():
                return
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        r = VideoReflexion.from_dict(data)
                        self._items[r.id] = r
            except Exception as exc:
                logger.warning(f"[VideoReflexion] load failed: {exc}")

    def _append(self, item: VideoReflexion) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"[VideoReflexion] append failed: {exc}")

    def add(
        self,
        *,
        error_type: str,
        trigger: str,
        lesson: str,
        apply_when: str = "",
        model_family: str = "",
        template_type: str = "",
        confidence: float = 0.7,
        tags: Optional[Iterable[str]] = None,
    ) -> VideoReflexion:
        """Ajoute ou boost une leçon vidéo."""
        lesson = (lesson or "").strip()
        if not lesson:
            return VideoReflexion(id="", error_type="", trigger="", lesson="", apply_when="")
        now = _now_iso()
        rid = VideoReflexion.compute_id(lesson)
        with self._lock:
            prev = self._items.get(rid)
            if prev:
                prev.confidence = min(1.0, prev.confidence + 0.1)
                prev.uses += 1
                prev.last_used_at = now
                self._append(prev)
                return prev
            item = VideoReflexion(
                id=rid,
                error_type=error_type[:30],
                trigger=trigger[:300],
                lesson=lesson[:500],
                apply_when=apply_when[:300],
                model_family=model_family[:20],
                template_type=template_type[:30],
                confidence=max(0.0, min(1.0, confidence)),
                uses=0,
                created_at=now,
                last_used_at="",
                tags=list(tags or []),
            )
            self._items[rid] = item
            self._append(item)
            return item

    def retrieve(
        self,
        query: str,
        k: int = 5,
        min_score: float = 0.08,
        model_family: Optional[str] = None,
        template_type: Optional[str] = None,
    ) -> List[VideoReflexion]:
        """Top-k leçons vidéo pertinentes."""
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scored: List[tuple[float, VideoReflexion]] = []
        now = datetime.now(_UTC)
        with self._lock:
            items = list(self._items.values())
        for item in items:
            corpus = f"{item.lesson} {item.apply_when} {item.trigger} {' '.join(item.tags)}"
            p_tokens = _tokenize(corpus)
            base = _jaccard(q_tokens, p_tokens)
            if base < min_score:
                continue
            score = base + (item.confidence * 0.2)
            if item.uses > 0:
                score += min(0.15, 0.04 * (1.0 + (item.uses ** 0.5)))
            # Bonus si même famille de modèle
            if model_family and item.model_family == model_family:
                score += 0.12
            # Bonus si même template
            if template_type and item.template_type == template_type:
                score += 0.10
            # Decay
            created = _parse_iso(item.created_at)
            if created:
                age_days = (now - created).days
                if age_days > 120:
                    score -= min(0.08, 0.001 * (age_days - 120))
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:k]]

    def increment_uses(self, rid: str) -> None:
        with self._lock:
            item = self._items.get(rid)
            if item:
                item.uses += 1
                item.last_used_at = _now_iso()
                self._append(item)

    def prune(self, max_age_days: int = 150, min_uses: int = 0) -> int:
        threshold = datetime.now(_UTC) - timedelta(days=max_age_days)
        victims = []
        with self._lock:
            for item in list(self._items.values()):
                created = _parse_iso(item.created_at)
                if created and created < threshold and item.uses <= min_uses:
                    victims.append(item.id)
        for vid in victims:
            with self._lock:
                self._items.pop(vid, None)
        if victims:
            self._rewrite()
        return len(victims)

    def _rewrite(self) -> None:
        try:
            tmp = self.path.with_suffix(".jsonl.tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as f:
                for item in self._items.values():
                    f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
            tmp.replace(self.path)
        except Exception as exc:
            logger.warning(f"[VideoReflexion] rewrite failed: {exc}")

    def format_for_prompt(self, items: List[VideoReflexion]) -> str:
        if not items:
            return ""
        lines = ["⚠️ LEÇONS VIDÉO (erreurs passées — évite ces pièges):"]
        for item in items:
            lines.append(f"  {item.to_compact()}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._items)


class VideoSuccessStore:
    """Store persistant de patterns de réussite vidéo."""

    DEFAULT_PATH = Path("data/learning/video_successes.jsonl")

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path: Path = Path(path) if path else self.DEFAULT_PATH
        self._lock = threading.RLock()
        self._items: Dict[str, VideoSuccess] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            self._items.clear()
            if not self.path.exists():
                return
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        s = VideoSuccess.from_dict(data)
                        self._items[s.id] = s
            except Exception as exc:
                logger.warning(f"[VideoSuccess] load failed: {exc}")

    def _append(self, item: VideoSuccess) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"[VideoSuccess] append failed: {exc}")

    def add(
        self,
        *,
        template_type: str,
        description_hint: str,
        approach: str,
        scenes_count: int = 0,
        model_used: str = "",
        model_family: str = "",
        iterations_needed: int = 1,
        animations_used: Optional[Iterable[str]] = None,
        confidence: float = 0.7,
        tags: Optional[Iterable[str]] = None,
    ) -> VideoSuccess:
        """Ajoute un pattern de réussite vidéo."""
        description_hint = (description_hint or "").strip()
        if not description_hint:
            return VideoSuccess(id="", template_type="", description_hint="", approach="")
        now = _now_iso()
        sid = VideoSuccess.compute_id(description_hint)
        with self._lock:
            prev = self._items.get(sid)
            if prev:
                prev.confidence = min(1.0, prev.confidence + 0.1)
                prev.uses += 1
                prev.last_used_at = now
                self._append(prev)
                return prev
            item = VideoSuccess(
                id=sid,
                template_type=template_type[:30],
                description_hint=description_hint[:400],
                approach=approach[:500],
                scenes_count=scenes_count,
                model_used=model_used[:50],
                model_family=model_family[:20],
                iterations_needed=iterations_needed,
                animations_used=list(animations_used or [])[:10],
                confidence=max(0.0, min(1.0, confidence)),
                uses=0,
                created_at=now,
                last_used_at="",
                tags=list(tags or []),
            )
            self._items[sid] = item
            self._append(item)
            return item

    def retrieve(
        self,
        query: str,
        k: int = 3,
        min_score: float = 0.08,
        model_family: Optional[str] = None,
        template_type: Optional[str] = None,
    ) -> List[VideoSuccess]:
        """Top-k patterns de réussite vidéo."""
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scored: List[tuple[float, VideoSuccess]] = []
        now = datetime.now(_UTC)
        with self._lock:
            items = list(self._items.values())
        for item in items:
            corpus = f"{item.description_hint} {item.approach} {item.template_type} {' '.join(item.tags)}"
            p_tokens = _tokenize(corpus)
            base = _jaccard(q_tokens, p_tokens)
            if base < min_score:
                continue
            score = base + (item.confidence * 0.2)
            if item.uses > 0:
                score += min(0.15, 0.04 * (1.0 + (item.uses ** 0.5)))
            if model_family and item.model_family == model_family:
                score += 0.12
            if template_type and item.template_type == template_type:
                score += 0.10
            created = _parse_iso(item.created_at)
            if created:
                age_days = (now - created).days
                if age_days > 180:
                    score -= min(0.08, 0.001 * (age_days - 180))
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:k]]

    def increment_uses(self, sid: str) -> None:
        with self._lock:
            item = self._items.get(sid)
            if item:
                item.uses += 1
                item.last_used_at = _now_iso()
                self._append(item)

    def format_for_prompt(self, items: List[VideoSuccess]) -> str:
        if not items:
            return ""
        lines = ["🏆 PATTERNS DE RÉUSSITE VIDÉO (approches qui ont fonctionné):"]
        for item in items:
            lines.append(f"  {item.to_compact()}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._items)


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETONS
# ══════════════════════════════════════════════════════════════════════════════

_reflexion_store: Optional[VideoReflexionStore] = None
_success_store: Optional[VideoSuccessStore] = None
_lock = threading.Lock()


def get_video_reflexion_store(path: Optional[Path] = None) -> VideoReflexionStore:
    global _reflexion_store
    with _lock:
        if _reflexion_store is None or (path and _reflexion_store.path != Path(path)):
            _reflexion_store = VideoReflexionStore(path=path)
        return _reflexion_store


def get_video_success_store(path: Optional[Path] = None) -> VideoSuccessStore:
    global _success_store
    with _lock:
        if _success_store is None or (path and _success_store.path != Path(path)):
            _success_store = VideoSuccessStore(path=path)
        return _success_store


# ══════════════════════════════════════════════════════════════════════════════
# MODEL FAMILY CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

_SMALL_MODELS = {"qwen3-8b", "deepseek-r1-7b", "llama-3.1-8b", "mistral-7b", "phi-3"}
_LARGE_MODELS = {"claude-opus", "o3", "deepseek-reasoner", "gpt-4", "gemini-pro"}


def classify_model_family(model_name: str) -> str:
    """Classifie un modèle en small/medium/large pour l'adaptation vidéo."""
    name = (model_name or "").lower()
    for prefix in _SMALL_MODELS:
        if prefix in name:
            return "small"
    for prefix in _LARGE_MODELS:
        if prefix in name:
            return "large"
    # Heuristiques
    if "7b" in name or "8b" in name or "3b" in name:
        return "small"
    if "70b" in name or "opus" in name or "reasoner" in name:
        return "large"
    if "ollama" in name or "local" in name:
        return "small"
    return "medium"


# ══════════════════════════════════════════════════════════════════════════════
# VIDEO GENERATION TELEMETRY
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class VideoTelemetry:
    """Télémétrie d'une génération vidéo complète."""
    model: str = ""
    template_type: str = ""
    scenes_count: int = 0
    description: str = ""
    # Métriques de la boucle
    tsx_generation_attempts: int = 0
    tsx_validation_failures: int = 0
    render_attempts: int = 0
    render_errors: List[str] = field(default_factory=list)
    reflexions_applied: int = 0
    successes_applied: int = 0
    auto_fixes_applied: int = 0
    # Timings
    planning_duration_s: float = 0.0
    tsx_generation_duration_s: float = 0.0
    render_duration_s: float = 0.0
    total_duration_s: float = 0.0
    # Résultat
    success: bool = False
    output_path: str = ""
    failure_reason: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    def summary(self) -> str:
        status = "✅ SUCCÈS" if self.success else "❌ ÉCHEC"
        return (
            f"{status} | modèle={self.model} template={self.template_type} "
            f"scènes={self.scenes_count} | tsx_attempts={self.tsx_generation_attempts} "
            f"render_attempts={self.render_attempts} | durée={self.total_duration_s:.1f}s"
        )


# ══════════════════════════════════════════════════════════════════════════════
# PROMPT ENRICHMENT
# ══════════════════════════════════════════════════════════════════════════════

def enrich_prompt_with_memory(
    base_prompt: str,
    description: str,
    model_name: str = "",
    template_type: str = "",
) -> str:
    """Enrichit un prompt vidéo avec les leçons et succès pertinents.

    C'est la fonction principale qui rend le système auto-améliorant :
    chaque génération bénéficie de l'historique des sessions précédentes.
    """
    additions: List[str] = []
    model_family = classify_model_family(model_name)

    # Reflexions (leçons d'erreurs passées)
    try:
        rstore = get_video_reflexion_store()
        if len(rstore) > 0:
            query = f"{description} {template_type} {model_family}"
            hits = rstore.retrieve(
                query, k=4, model_family=model_family, template_type=template_type
            )
            if hits:
                additions.append(rstore.format_for_prompt(hits))
                for h in hits:
                    rstore.increment_uses(h.id)
    except Exception as exc:
        logger.debug(f"[VideoMemory] reflexion retrieval failed: {exc}")

    # Succès (patterns qui ont fonctionné)
    try:
        sstore = get_video_success_store()
        if len(sstore) > 0:
            query = f"{description} {template_type} {model_family}"
            hits = sstore.retrieve(
                query, k=2, model_family=model_family, template_type=template_type
            )
            if hits:
                additions.append(sstore.format_for_prompt(hits))
                for h in hits:
                    sstore.increment_uses(h.id)
    except Exception as exc:
        logger.debug(f"[VideoMemory] success retrieval failed: {exc}")

    if not additions:
        return base_prompt

    memory_block = "\n\n".join(additions)
    return f"{base_prompt}\n\n{memory_block}"


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
