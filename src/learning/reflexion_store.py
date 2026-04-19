"""
🧠 LUMENA — Reflexion Store

Mémoire persistante des leçons apprises entre sessions.

Inspiré du paper "Reflexion: Language Agents with Verbal Reinforcement Learning"
(Shinn et al. 2023, arXiv:2303.11366) : quand l'agent échoue de façon répétée
sur un pattern (ex. grep 0-result × 3, str_replace qui ne matche pas × 2+),
on génère une réflexion structurée stockée en mémoire long-terme, puis
réinjectée par RAG dans les contextes similaires.

Stockage :
- JSONL append-only (simple, diffable, auditeable humainement) :
    data/learning/reflexions.jsonl
- Index en mémoire pour retrieval rapide (BM25-like Jaccard tokens + boost
  par triggered_by matching + decay par âge)
- Déduplication par hash(lesson_normalized)

Pas de dépendance forte à ChromaDB : le retrieval fonctionne en pur Python.
Si ChromaDB est disponible et une collection "lumena_reflexions" existe,
elle est utilisée en priorité pour la recherche sémantique.

Interface :
    store = get_reflexion_store()
    store.add(Reflexion(triggered_by=..., root_cause=..., lesson=..., apply_when=...))
    hits = store.retrieve(task_description="...", context_hint="...", k=3)
    store.increment_uses(hits[0].id)
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Iterable

from loguru import logger


_UTC = timezone.utc


def _now_iso() -> str:
    """UTC ISO-8601 string (seconds precision)."""
    return datetime.now(_UTC).isoformat(timespec="seconds")


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse un ISO-8601 en datetime tz-aware. None si invalide."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt


# ── Data model ──────────────────────────────────────────────────────────────


@dataclass
class Reflexion:
    """Une leçon apprise, structurée pour réinjection ciblée."""
    id: str                       # hash stable basé sur lesson normalisée
    triggered_by: str             # signal d'échec qui a déclenché la réflexion
    root_cause: str               # analyse de la cause profonde
    lesson: str                   # leçon actionnable (1-2 phrases)
    apply_when: str               # contexte d'application (mots-clés/conditions)
    confidence: float = 0.7       # 0.0 - 1.0
    uses: int = 0                 # compteur d'utilisations réussies
    created_at: str = ""
    last_used_at: str = ""
    tags: List[str] = field(default_factory=list)

    @staticmethod
    def compute_id(lesson: str) -> str:
        norm = re.sub(r"\s+", " ", (lesson or "").strip().lower())[:400]
        return "refl_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "Reflexion":
        return cls(
            id=str(data.get("id") or cls.compute_id(str(data.get("lesson", "")))),
            triggered_by=str(data.get("triggered_by", "")),
            root_cause=str(data.get("root_cause", "")),
            lesson=str(data.get("lesson", "")),
            apply_when=str(data.get("apply_when", "")),
            confidence=float(data.get("confidence", 0.7) or 0.7),
            uses=int(data.get("uses", 0) or 0),
            created_at=str(data.get("created_at") or ""),
            last_used_at=str(data.get("last_used_at") or ""),
            tags=list(data.get("tags") or []),
        )

    def to_compact(self) -> str:
        uses_tag = f" (appliqué {self.uses}×)" if self.uses > 0 else ""
        return f"• {self.lesson}{uses_tag}"


# ── Tokenizer simple (stable, zéro dépendance) ──────────────────────────────


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
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOPWORDS and len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ── Store ───────────────────────────────────────────────────────────────────


class ReflexionStore:
    """Mémoire persistante + retrieval des leçons."""

    DEFAULT_PATH = Path("data/learning/reflexions.jsonl")

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path: Path = Path(path) if path else self.DEFAULT_PATH
        self._lock = threading.RLock()
        self._items: Dict[str, Reflexion] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────

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
                        refl = Reflexion.from_dict(data)
                        # Dernier écrit gagne (append-only avec mises à jour)
                        self._items[refl.id] = refl
            except Exception as exc:
                logger.warning(f"[Reflexion] load failed: {exc}")

    def _append(self, refl: Reflexion) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(refl.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"[Reflexion] append failed: {exc}")

    def reload(self) -> None:
        """Force un reload depuis le disque."""
        self._load()

    # ── Mutations ─────────────────────────────────────────────────────────

    def add(
        self,
        *,
        triggered_by: str,
        root_cause: str,
        lesson: str,
        apply_when: str,
        confidence: float = 0.7,
        tags: Optional[Iterable[str]] = None,
    ) -> Reflexion:
        """Ajoute (ou met à jour par dedup) une réflexion.

        Retourne l'instance effective (peut être un pré-existant si doublon).
        """
        lesson = (lesson or "").strip()
        if not lesson:
            raise ValueError("lesson required")
        now = _now_iso()
        refl_id = Reflexion.compute_id(lesson)
        with self._lock:
            prev = self._items.get(refl_id)
            if prev:
                # Boost confiance et date si déjà vu (même leçon apprise 2 fois)
                prev.confidence = min(1.0, prev.confidence + 0.1)
                prev.last_used_at = now
                self._append(prev)
                return prev
            refl = Reflexion(
                id=refl_id,
                triggered_by=triggered_by.strip()[:500],
                root_cause=root_cause.strip()[:500],
                lesson=lesson[:500],
                apply_when=apply_when.strip()[:300],
                confidence=max(0.0, min(1.0, float(confidence))),
                uses=0,
                created_at=now,
                last_used_at="",
                tags=list(tags or []),
            )
            self._items[refl_id] = refl
            self._append(refl)
            return refl

    def increment_uses(self, refl_id: str) -> None:
        """Compte une utilisation réussie (boost retrieval futur)."""
        with self._lock:
            refl = self._items.get(refl_id)
            if not refl:
                return
            refl.uses += 1
            refl.last_used_at = _now_iso()
            self._append(refl)

    def forget(self, refl_id: str) -> bool:
        """Supprime (en mémoire et par re-écriture du fichier)."""
        with self._lock:
            if refl_id not in self._items:
                return False
            del self._items[refl_id]
            # Réécriture full du fichier (cheap, <10k entrées attendues)
            try:
                tmp = self.path.with_suffix(".jsonl.tmp")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                with tmp.open("w", encoding="utf-8") as f:
                    for r in self._items.values():
                        f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
                tmp.replace(self.path)
            except Exception as exc:
                logger.warning(f"[Reflexion] forget failed: {exc}")
            return True

    def prune(self, max_age_days: int = 90, min_uses: int = 0) -> int:
        """Archive les vieilles réflexions jamais utilisées. Retourne le nb supprimé."""
        threshold = datetime.now(_UTC) - timedelta(days=max_age_days)
        victims: List[str] = []
        with self._lock:
            for refl in list(self._items.values()):
                created = _parse_iso(refl.created_at)
                if created and created < threshold and refl.uses <= min_uses:
                    victims.append(refl.id)
        for vid in victims:
            self.forget(vid)
        return len(victims)

    # ── Retrieval ─────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        k: int = 3,
        min_score: float = 0.1,
    ) -> List[Reflexion]:
        """Retourne les top-k réflexions les plus pertinentes pour la query.

        Score = Jaccard(tokens(query), tokens(apply_when + lesson + triggered_by))
              + boost(confidence)
              + boost(usage) [log1p(uses) × 0.05]
              - decay(âge)   [jusqu'à -0.1 si > 90 jours]
        """
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scored: List[tuple[float, Reflexion]] = []
        now = datetime.now(_UTC)
        with self._lock:
            items = list(self._items.values())
        for refl in items:
            corpus = f"{refl.apply_when} {refl.lesson} {refl.triggered_by} {' '.join(refl.tags)}"
            r_tokens = _tokenize(corpus)
            base = _jaccard(q_tokens, r_tokens)
            if base < min_score:
                continue
            score = base + (refl.confidence * 0.2)
            # Boost usage (log pour éviter dominance totale)
            if refl.uses > 0:
                score += min(0.2, 0.05 * (1.0 + (refl.uses ** 0.5)))
            # Decay par âge
            created = _parse_iso(refl.created_at)
            if created:
                age_days = (now - created).days
                if age_days > 90:
                    score -= min(0.1, 0.001 * (age_days - 90))
            scored.append((score, refl))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:k]]

    # ── Debug / API ───────────────────────────────────────────────────────

    def all(self) -> List[Reflexion]:
        with self._lock:
            return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def format_for_prompt(
        self,
        reflexions: List[Reflexion],
        header: str = "🧠 LEÇONS APPRISES (sessions précédentes — applique si pertinent)",
    ) -> str:
        if not reflexions:
            return ""
        lines = [header]
        for r in reflexions:
            lines.append(r.to_compact())
        return "\n".join(lines)


# ── Singleton ───────────────────────────────────────────────────────────────


_store: Optional[ReflexionStore] = None
_store_lock = threading.Lock()


def get_reflexion_store(path: Optional[Path] = None) -> ReflexionStore:
    """Retourne le store global (singleton). Thread-safe."""
    global _store
    with _store_lock:
        if _store is None or (path is not None and _store.path != Path(path)):
            _store = ReflexionStore(path=path)
        return _store


def reset_reflexion_store() -> None:
    """Usage : tests ; réinitialise le singleton."""
    global _store
    with _store_lock:
        _store = None


# ── Generation from LLM (async, fire-and-forget) ────────────────────────────


_REFLEXION_PROMPT = """Tu es un analyste d'échecs d'agent de code. À partir du signal ci-dessous,
produis UNE leçon actionnable, stable, et réutilisable pour des tâches futures similaires.

Format JSON STRICT, rien d'autre :
{
  "triggered_by": "<2-15 mots décrivant le signal d'échec>",
  "root_cause":   "<1 phrase, cause technique réelle>",
  "lesson":       "<1-2 phrases impératives, actionnable, générale>",
  "apply_when":   "<5-15 mots-clés de contextes où appliquer cette leçon>",
  "tags":         ["<2 à 5 tags courts>"],
  "confidence":   <float 0.5-0.95>
}

Ne fais AUCUNE supposition : si le signal est ambigu, confidence ≤ 0.6.
Évite les leçons spécifiques à un fichier : elles doivent se transposer.
"""


def build_reflexion_prompt(signal: str, context: str) -> List[Dict[str, str]]:
    """Construit les messages LLM pour générer une réflexion.
    Externe à la génération pour faciliter tests.
    """
    user = f"SIGNAL D'ÉCHEC :\n{signal}\n\nCONTEXTE (trace récente) :\n{context}"
    return [
        {"role": "system", "content": _REFLEXION_PROMPT.strip()},
        {"role": "user", "content": user.strip()},
    ]


def parse_reflexion_llm_response(raw: str) -> Optional[Dict]:
    """Parse une réponse LLM en dict. None si format invalide."""
    if not raw:
        return None
    # Extrait le premier bloc JSON (tolère markdown)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    required = {"triggered_by", "root_cause", "lesson", "apply_when"}
    if not required.issubset(data.keys()):
        return None
    # Normalise tags / confidence
    data["tags"] = list(data.get("tags") or [])
    try:
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.7))))
    except (TypeError, ValueError):
        data["confidence"] = 0.7
    return data
