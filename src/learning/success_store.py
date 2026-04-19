"""
🏆 LUMENA — Success Store

Mémoire persistante des patterns de RÉUSSITE entre sessions.

Miroir positif du Reflexion Store : au lieu d'apprendre uniquement des échecs,
on capture ce qui A MARCHÉ (quelle approche, quels outils, combien d'itérations)
pour un type de tâche donné, afin de le réinjecter comme guide en contexte
similaire.

Inspiré de "Case-Based Reasoning" (Aamodt & Plaza 1994) + pratiques modernes
de retrieval-augmented agents : chaque tâche réussie devient un exemplar
minimaliste réutilisable.

Stockage :
- JSONL append-only (diffable, auditeable humainement) :
    data/learning/successes.jsonl
- Index mémoire pour retrieval rapide (même approche Jaccard que Reflexion)
- Déduplication par hash(summary_normalized)

Interface :
    store = get_success_store()
    store.add(SuccessPattern(task_type=..., summary=..., approach=..., ...))
    hits = store.retrieve(query="...", k=3)
    store.increment_uses(hits[0].id)
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


# ── Data model ──────────────────────────────────────────────────────────────


@dataclass
class SuccessPattern:
    """Un pattern de résolution réussie, capturé pour réutilisation future."""
    id: str                            # hash stable basé sur summary
    task_type: str                     # catégorie ("bugfix", "refactor", "feature", ...)
    summary: str                       # 1 phrase : quoi résolu
    approach: str                      # 1-3 phrases : comment
    tools_used: List[str] = field(default_factory=list)
    iterations: int = 0                # nombre d'itérations à la réussite
    apply_when: str = ""               # mots-clés de contexte d'application
    confidence: float = 0.7
    uses: int = 0                      # compteur de réutilisations réussies
    created_at: str = ""
    last_used_at: str = ""
    tags: List[str] = field(default_factory=list)

    @staticmethod
    def compute_id(summary: str) -> str:
        norm = re.sub(r"\s+", " ", (summary or "").strip().lower())[:400]
        return "succ_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "SuccessPattern":
        return cls(
            id=str(data.get("id") or cls.compute_id(str(data.get("summary", "")))),
            task_type=str(data.get("task_type", "") or "other"),
            summary=str(data.get("summary", "")),
            approach=str(data.get("approach", "")),
            tools_used=list(data.get("tools_used") or []),
            iterations=int(data.get("iterations", 0) or 0),
            apply_when=str(data.get("apply_when", "") or ""),
            confidence=float(data.get("confidence", 0.7) or 0.7),
            uses=int(data.get("uses", 0) or 0),
            created_at=str(data.get("created_at") or ""),
            last_used_at=str(data.get("last_used_at") or ""),
            tags=list(data.get("tags") or []),
        )

    def to_compact(self) -> str:
        uses_tag = f" (réutilisé {self.uses}×)" if self.uses > 0 else ""
        tools_tag = f" [outils: {', '.join(self.tools_used[:3])}]" if self.tools_used else ""
        return f"✓ [{self.task_type}] {self.summary} → {self.approach}{tools_tag}{uses_tag}"


# ── Tokenizer (aligné sur ReflexionStore, zéro dépendance) ──────────────────


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


# ── Store ───────────────────────────────────────────────────────────────────


class SuccessStore:
    """Mémoire persistante + retrieval des patterns de réussite."""

    DEFAULT_PATH = Path("data/learning/successes.jsonl")

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path: Path = Path(path) if path else self.DEFAULT_PATH
        self._lock = threading.RLock()
        self._items: Dict[str, SuccessPattern] = {}
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
                        pat = SuccessPattern.from_dict(data)
                        self._items[pat.id] = pat
            except Exception as exc:
                logger.warning(f"[Success] load failed: {exc}")

    def _append(self, pat: SuccessPattern) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(pat.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"[Success] append failed: {exc}")

    def reload(self) -> None:
        self._load()

    # ── Mutations ─────────────────────────────────────────────────────────

    def add(
        self,
        *,
        task_type: str,
        summary: str,
        approach: str,
        tools_used: Optional[Iterable[str]] = None,
        iterations: int = 0,
        apply_when: str = "",
        confidence: float = 0.7,
        tags: Optional[Iterable[str]] = None,
    ) -> SuccessPattern:
        """Ajoute (ou boost si dédoublonné) un pattern de réussite."""
        summary = (summary or "").strip()
        if not summary:
            raise ValueError("summary required")
        now = _now_iso()
        pid = SuccessPattern.compute_id(summary)
        with self._lock:
            prev = self._items.get(pid)
            if prev:
                prev.confidence = min(1.0, prev.confidence + 0.1)
                prev.last_used_at = now
                # Ne perd pas l'historique outils si nouvel ajout plus pauvre
                if tools_used:
                    merged = list(dict.fromkeys(list(prev.tools_used) + list(tools_used)))
                    prev.tools_used = merged[:10]
                self._append(prev)
                return prev
            pat = SuccessPattern(
                id=pid,
                task_type=(task_type or "other").strip()[:40],
                summary=summary[:400],
                approach=(approach or "").strip()[:500],
                tools_used=list(tools_used or [])[:10],
                iterations=max(0, int(iterations or 0)),
                apply_when=(apply_when or "").strip()[:300],
                confidence=max(0.0, min(1.0, float(confidence))),
                uses=0,
                created_at=now,
                last_used_at="",
                tags=list(tags or []),
            )
            self._items[pid] = pat
            self._append(pat)
            return pat

    def increment_uses(self, pid: str) -> None:
        with self._lock:
            pat = self._items.get(pid)
            if not pat:
                return
            pat.uses += 1
            pat.last_used_at = _now_iso()
            self._append(pat)

    def forget(self, pid: str) -> bool:
        with self._lock:
            if pid not in self._items:
                return False
            del self._items[pid]
            try:
                tmp = self.path.with_suffix(".jsonl.tmp")
                tmp.parent.mkdir(parents=True, exist_ok=True)
                with tmp.open("w", encoding="utf-8") as f:
                    for p in self._items.values():
                        f.write(json.dumps(p.to_dict(), ensure_ascii=False) + "\n")
                tmp.replace(self.path)
            except Exception as exc:
                logger.warning(f"[Success] forget failed: {exc}")
            return True

    def prune(self, max_age_days: int = 180, min_uses: int = 0) -> int:
        """Supprime les patterns anciens jamais réutilisés."""
        threshold = datetime.now(_UTC) - timedelta(days=max_age_days)
        victims: List[str] = []
        with self._lock:
            for pat in list(self._items.values()):
                created = _parse_iso(pat.created_at)
                if created and created < threshold and pat.uses <= min_uses:
                    victims.append(pat.id)
        for vid in victims:
            self.forget(vid)
        return len(victims)

    # ── Retrieval ─────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        k: int = 3,
        min_score: float = 0.1,
        task_type: Optional[str] = None,
    ) -> List[SuccessPattern]:
        """Top-k patterns les plus pertinents.

        Score = Jaccard(query_tokens, corpus_tokens)
              + confidence × 0.2
              + usage_boost(log)
              + task_type_match × 0.15
              - age_decay
        """
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scored: List[tuple[float, SuccessPattern]] = []
        now = datetime.now(_UTC)
        with self._lock:
            items = list(self._items.values())
        for pat in items:
            corpus = f"{pat.summary} {pat.approach} {pat.apply_when} {' '.join(pat.tags)}"
            p_tokens = _tokenize(corpus)
            base = _jaccard(q_tokens, p_tokens)
            if base < min_score:
                continue
            score = base + (pat.confidence * 0.2)
            if pat.uses > 0:
                score += min(0.2, 0.05 * (1.0 + (pat.uses ** 0.5)))
            if task_type and pat.task_type and pat.task_type.lower() == task_type.lower():
                score += 0.15
            created = _parse_iso(pat.created_at)
            if created:
                age_days = (now - created).days
                if age_days > 180:
                    score -= min(0.1, 0.001 * (age_days - 180))
            scored.append((score, pat))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:k]]

    # ── Debug / API ───────────────────────────────────────────────────────

    def all(self) -> List[SuccessPattern]:
        with self._lock:
            return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def format_for_prompt(
        self,
        patterns: List[SuccessPattern],
        header: str = "🏆 PATTERNS DE RÉUSSITE (tâches similaires résolues auparavant)",
    ) -> str:
        if not patterns:
            return ""
        lines = [header]
        for p in patterns:
            lines.append(p.to_compact())
        return "\n".join(lines)


# ── Singleton ───────────────────────────────────────────────────────────────


_store: Optional[SuccessStore] = None
_store_lock = threading.Lock()


def get_success_store(path: Optional[Path] = None) -> SuccessStore:
    """Retourne le store global (singleton). Thread-safe."""
    global _store
    with _store_lock:
        if _store is None or (path is not None and _store.path != Path(path)):
            _store = SuccessStore(path=path)
        return _store


def reset_success_store() -> None:
    """Usage tests : réinitialise le singleton."""
    global _store
    with _store_lock:
        _store = None


# ── LLM generation (async, fire-and-forget) ─────────────────────────────────


_SUCCESS_PROMPT = """Tu es un analyste de résolutions d'agent de code. À partir des informations ci-dessous
(description de tâche + outils utilisés + résultat), extrait UN pattern de réussite
général, réutilisable pour des tâches FUTURES similaires.

Format JSON STRICT, rien d'autre :
{
  "task_type":   "<un mot parmi: bugfix, refactor, feature, test, config, docs, perf, other>",
  "summary":     "<1 phrase décrivant CE QUI a été résolu (généralisable)>",
  "approach":    "<1-3 phrases impératives décrivant COMMENT — technique réutilisable>",
  "apply_when":  "<5-15 mots-clés : quand appliquer cette approche>",
  "tags":        ["<2 à 5 tags courts>"],
  "confidence":  <float 0.5-0.9>
}

Règles :
- Le pattern doit être TRANSPOSABLE (pas lié à un fichier/nom spécifique).
- Si la tâche est triviale ou trop spécifique, confidence ≤ 0.55.
- Évite les paraphrases de la description : capture l'INSIGHT technique.
"""


def build_success_prompt(
    task_description: str,
    tools_used: List[str],
    iterations: int,
    outcome_summary: str,
) -> List[Dict[str, str]]:
    """Construit les messages LLM pour extraire un pattern de succès."""
    tools_str = ", ".join(tools_used[:10]) if tools_used else "(n/a)"
    user = (
        f"TÂCHE :\n{task_description[:600]}\n\n"
        f"OUTILS UTILISÉS : {tools_str}\n"
        f"ITÉRATIONS : {iterations}\n\n"
        f"RÉSULTAT :\n{outcome_summary[:600]}"
    )
    return [
        {"role": "system", "content": _SUCCESS_PROMPT.strip()},
        {"role": "user", "content": user.strip()},
    ]


def parse_success_llm_response(raw: str) -> Optional[Dict]:
    """Parse une réponse LLM en dict. None si format invalide."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    # Validations minimales
    if not str(data.get("summary", "")).strip():
        return None
    if not str(data.get("approach", "")).strip():
        return None
    # Normalisation
    data["task_type"] = str(data.get("task_type") or "other").strip().lower()[:40]
    data["tags"] = [str(t)[:40] for t in (data.get("tags") or [])][:5]
    try:
        data["confidence"] = float(data.get("confidence", 0.7))
    except (TypeError, ValueError):
        data["confidence"] = 0.7
    return data
