"""
TaskContext — Contexte de tâche typé et immuable.

Encapsule TOUTES les informations nécessaires à l'exécution d'une tâche
déléguée (chemin du projet, intention, fichiers, mémoire, etc.).

Construit UNE SEULE FOIS dans le handler delegate_task via
TaskContext.from_delegate_call(), puis passé tel quel au sub-agent.
Élimine les bugs liés au dict non typé (intent perdu, mauvais projet, etc.).

Usage futur (Phase 2) :
    # Dans delegate_task_handler :
    task_ctx = TaskContext.from_delegate_call(description, context, project_path)
    safe_context = task_ctx.to_legacy_dict()  # rétrocompatibilité
    # Puis éventuellement : task.task_ctx = task_ctx
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# Regex pour extraire un chemin absolu Windows ou Unix depuis du texte libre.
# Supporte les espaces dans les chemins quand entouré de guillemets,
# et les chemins sans espaces en texte libre.
_PATH_QUOTED_RE = re.compile(
    r'["\']'
    r'([A-Za-z]:\\[^"\'<>|*?]+|[\\/](?:Users|workspace)[^"\'<>|*?]+|/(?:home|Users|var|tmp|opt|workspace)[^"\'<>|*?]+)'
    r'["\']'
)
_PATH_BARE_RE = re.compile(
    r'([A-Za-z]:\\[^\s\'"<>|*?]+|[\\/](?:Users|workspace)[^\s\'"<>|*?]+|/(?:home|Users|var|tmp|opt|workspace)[^\s\'"<>|*?]+)'
)


@dataclass(frozen=True)
class TaskContext:
    """
    Contexte immuable pour une tâche déléguée.

    Tous les champs sont résolus UNE SEULE FOIS à la construction.
    Le sub-agent n'a plus besoin de re-résoudre quoi que ce soit.
    """

    # ── Localisation ──
    workspace_path: Optional[Path] = None
    target_path: Optional[Path] = None  # Chemin cible demandé (peut ne pas encore exister)

    # ── Intention ──
    intent: str = "auto"  # "create" | "modify" | "read" | "unknown" | "auto"

    # ── Contenu ──
    description: str = ""
    project_files: List[str] = field(default_factory=list)
    memory_context: str = ""
    conversation_history: str = ""
    raw_llm_context: str = ""  # Le string brut passé par le LLM dans 'context'

    # ── Méta ──
    resolution_source: str = ""  # "explicit_param" | "explicit_text" | "registry" | "fallback"
    confidence: float = 0.0

    # ── Construction ─────────────────────────────────────────────────────────

    @classmethod
    def from_delegate_call(
        cls,
        description: str,
        context: Any = None,
        project_path: str = "",
        *,
        runtime_root: Optional[Path] = None,
        resolve_workspace_fn: Any = None,
        memory_fn: Any = None,
    ) -> "TaskContext":
        """
        Point d'entrée unique pour construire un TaskContext.

        Ordre de résolution du chemin :
        1. project_path (paramètre explicite)
        2. Chemin extrait du texte (description ou context string)
        3. resolve_workspace (fuzzy match registre)
        4. Fallback runtime_root

        Args:
            description: Description de la tâche.
            context: Contexte brut (dict, str JSON, ou str libre).
            project_path: Chemin explicite passé par le LLM.
            runtime_root: Racine du workspace Lumena (fallback).
            resolve_workspace_fn: Callable pour la résolution par registre.
            memory_fn: Callable(query, max_memories) → str pour la mémoire.
        """
        # ── Normaliser context en dict ──
        safe_ctx: Dict[str, Any] = {}
        raw_llm_context = ""

        if isinstance(context, str):
            raw_llm_context = context
            try:
                parsed = json.loads(context)
                if isinstance(parsed, dict):
                    safe_ctx = parsed
            except (ValueError, TypeError):
                safe_ctx = {}
        elif isinstance(context, dict):
            safe_ctx = dict(context)

        # ── Résolution du chemin ──
        resolved_path: Optional[Path] = None
        target_path: Optional[Path] = None
        intent = safe_ctx.get("intent", "auto")
        detected_intent: Optional[str] = None
        source = ""
        confidence = 0.0
        project_files: List[str] = safe_ctx.get("project_files", [])

        if intent == "auto":
            try:
                from ..utils.project_registry import _detect_intent as detect_intent

                intent_text = " ".join(
                    part for part in (
                        description or "",
                        raw_llm_context or "",
                        safe_ctx.get("raw", "") or "",
                    ) if part
                )
                detected_intent = detect_intent(intent_text)
                if detected_intent in {"create", "modify", "read"}:
                    intent = detected_intent
            except Exception:
                detected_intent = None

        # 1. project_path explicite
        if project_path:
            pp = Path(project_path)
            target_path = pp
            if pp.is_dir():
                resolved_path = pp
                source = "explicit_param"
                confidence = 1.0
                logger.info("TaskContext: project_path explicite: {}", pp)
            else:
                # ── FIX: le chemin n'existe pas — chercher le même nom de projet
                # dans d'autres dossiers datés du workspace (ex: 2026-04-27/snake →
                # trouvé dans 2026-04-26/snake).
                _relocated = cls._find_project_in_dated_dirs(pp)
                if _relocated:
                    resolved_path = _relocated
                    target_path = _relocated
                    source = "explicit_param_relocated"
                    confidence = 0.95
                    logger.info(
                        "TaskContext: project_path relocalisé: {} → {} (dossier daté différent)",
                        pp, _relocated,
                    )
                else:
                    # Le dossier n'existe pas encore (création)
                    resolved_path = pp
                    source = "explicit_param_new"
                    confidence = 0.9
                    if intent == "auto":
                        intent = "create"
                    logger.info("TaskContext: project_path (nouveau): {}", pp)

        # 2. Extraction depuis le texte
        if resolved_path is None:
            extracted = cls._extract_path_from_texts(
                [description, raw_llm_context, safe_ctx.get("raw", "")]
            )
            if extracted:
                target_path = extracted
                if extracted.is_dir():
                    resolved_path = extracted
                    source = "explicit_text"
                    confidence = 0.95
                    logger.info("TaskContext: chemin extrait du texte: {}", extracted)
                else:
                    # Chemin pas encore créé
                    resolved_path = extracted
                    source = "explicit_text_new"
                    confidence = 0.85
                    if intent == "auto":
                        intent = "create"
                    logger.info("TaskContext: chemin extrait (nouveau): {}", extracted)

        # 3. resolve_workspace (registre + fuzzy)
        if resolved_path is None and resolve_workspace_fn:
            try:
                allow_create = intent == "create" or detected_intent == "create"
                resolution = resolve_workspace_fn(
                    description, context=safe_ctx, allow_create=allow_create,
                )
                if resolution.path:
                    resolved_path = resolution.path
                    target_path = resolution.path
                    source = f"registry:{resolution.source}"
                    confidence = resolution.confidence
                    if resolution.intent and intent == "auto":
                        intent = resolution.intent
                    # Collecter les fichiers du projet
                    if not project_files:
                        project_files = cls._scan_project_files(resolution.path)
                    logger.info(
                        "TaskContext: résolu via registre: {} (intent={}, conf={:.2f})",
                        resolution.path, resolution.intent, resolution.confidence,
                    )
            except Exception as e:
                logger.debug("TaskContext: resolve_workspace failed: {}", e)

        # 4. Fallback
        if resolved_path is None and runtime_root:
            resolved_path = runtime_root
            source = "fallback"
            confidence = 0.1
            logger.debug("TaskContext: fallback runtime_root: {}", runtime_root)

        # ── Intent fallback ──
        if intent == "auto" and resolved_path:
            if resolved_path.is_dir() and any(resolved_path.iterdir()):
                intent = "modify"
            else:
                intent = "create"

        # ── Mémoire ──
        memory_context = safe_ctx.get("memory_context", "")
        if not memory_context and memory_fn:
            try:
                memory_context = memory_fn(description, 5) or ""
            except Exception:
                pass

        return cls(
            workspace_path=resolved_path,
            target_path=target_path,
            intent=intent,
            description=description,
            project_files=project_files,
            memory_context=memory_context,
            conversation_history=safe_ctx.get("conversation_history", ""),
            raw_llm_context=raw_llm_context,
            resolution_source=source,
            confidence=confidence,
        )

    # ── Extraction de chemins ────────────────────────────────────────────────

    @staticmethod
    def _extract_path_from_texts(texts: List[str]) -> Optional[Path]:
        """
        Extrait le premier chemin valide depuis une liste de textes.

        Tente d'abord les chemins entre guillemets (supportent les espaces),
        puis les chemins bare (sans espaces).
        """
        for text in texts:
            if not text:
                continue

            # D'abord : chemins entre guillemets (supportent les espaces)
            for match in _PATH_QUOTED_RE.findall(text):
                cleaned = match.rstrip(".,;:!?)")
                p = TaskContext._normalize_extracted_path(cleaned)
                # Accepter même si le dossier n'existe pas encore
                # (on vérifie que le parent existe comme heuristique)
                if p.is_dir() or (p.parent.is_dir() and not p.suffix):
                    return p

            # Ensuite : chemins bare (sans espaces)
            for match in _PATH_BARE_RE.finditer(text):
                raw = match.group(0).rstrip(".,;:!?)")
                p = TaskContext._normalize_extracted_path(raw)
                if p.is_dir() or (p.parent.is_dir() and not p.suffix):
                    return p
                # Le chemin peut avoir des espaces (ex: "projet fusee") —
                # on tente d'ajouter les mots suivants mot par mot.
                rest = text[match.end():]
                extended = raw
                for word in rest.split():
                    word_clean = word.rstrip(".,;:!?)")
                    if not word_clean or word_clean[0] in ('"', "'", '\\', '/'):
                        break
                    extended = extended + " " + word_clean
                    pe = TaskContext._normalize_extracted_path(extended)
                    if pe.is_dir():
                        return pe
                    # Si le parent n'existe plus, inutile de continuer
                    if not pe.parent.is_dir():
                        break

        return None

    @staticmethod
    def _normalize_extracted_path(raw: str) -> Path:
        """Normalise un chemin extrait du texte libre.

        Cas géré :
        - chemin Windows absolu normal : `C:\\...`
        - chemin Windows sans drive perdu dans une string : `\\Users\\...`
        - chemin slash-style sur Windows : `/Users/...` ou `/workspace/...`
        """
        cleaned = (raw or "").strip()
        if cleaned:
            _looks_drive_less_windows = bool(
                re.match(r'^[\\/](?:Users|workspace)(?:[\\/]|$)', cleaned, re.IGNORECASE)
            )
            if _looks_drive_less_windows:
                _drive = Path.cwd().drive
                if _drive:
                    cleaned = _drive + cleaned.replace("/", "\\")
        return Path(cleaned)

    @staticmethod
    def _find_project_in_dated_dirs(original: Path) -> Optional[Path]:
        """Cherche un projet dans d'autres dossiers datés du workspace.

        Si le chemin ``workspace/2026-04-27/snake-3d`` n'existe pas mais que
        ``workspace/2026-04-26/snake-3d`` existe, retourne ce dernier.

        Ne cherche que dans le parent commun (workspace/) parmi les dossiers
        dont le nom matche YYYY-MM-DD. Retourne le match le plus récent.
        """
        _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        try:
            project_name = original.name
            # Le parent du chemin daté : workspace/2026-04-27/snake → parent.parent = workspace/
            dated_parent = original.parent  # workspace/2026-04-27
            if not _DATE_RE.match(dated_parent.name):
                return None  # pas un chemin workspace/YYYY-MM-DD/project
            ws_root = dated_parent.parent  # workspace/
            if not ws_root.is_dir():
                return None

            candidates: List[Path] = []
            for d in ws_root.iterdir():
                if d.is_dir() and _DATE_RE.match(d.name) and d != dated_parent:
                    candidate = d / project_name
                    if candidate.is_dir():
                        candidates.append(candidate)

            if not candidates:
                return None
            # Trier par date décroissante → retourner le plus récent
            candidates.sort(key=lambda p: p.parent.name, reverse=True)
            return candidates[0]
        except Exception:
            return None

    @staticmethod
    def _scan_project_files(path: Path, max_files: int = 100) -> List[str]:
        """Scanne les fichiers du projet en excluant les dossiers de build."""
        _excluded = {".git", "__pycache__", "node_modules", ".venv", ".env"}
        files: List[str] = []
        try:
            for f in sorted(path.rglob("*")):
                if f.is_file():
                    rel = str(f.relative_to(path))
                    if not any(ex in rel for ex in _excluded):
                        files.append(rel)
                        if len(files) >= max_files:
                            break
        except Exception:
            pass
        return files

    # ── Conversion rétrocompatible ───────────────────────────────────────────

    def to_legacy_dict(self) -> Dict[str, Any]:
        """
        Convertit en dict compatible avec l'API actuelle (safe_context).

        Permet une migration incrémentale : le handler construit un TaskContext,
        puis appelle to_legacy_dict() pour le passer au sub-agent existant.
        """
        d: Dict[str, Any] = {}

        if self.workspace_path:
            d["workspace_path"] = str(self.workspace_path)
            d["project_dir"] = str(self.workspace_path)

        if self.intent != "auto":
            d["intent"] = self.intent

        if self.project_files:
            d["project_files"] = self.project_files

        if self.memory_context:
            d["memory_context"] = self.memory_context

        if self.conversation_history:
            d["conversation_history"] = self.conversation_history

        if self.raw_llm_context:
            d["raw"] = self.raw_llm_context

        return d

    # ── Debug ────────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """Résumé compact pour les logs."""
        parts = [f"intent={self.intent}"]
        if self.workspace_path:
            parts.append(f"ws={self.workspace_path}")
        if self.target_path and self.target_path != self.workspace_path:
            parts.append(f"target={self.target_path}")
        parts.append(f"src={self.resolution_source}")
        parts.append(f"conf={self.confidence:.2f}")
        if self.project_files:
            parts.append(f"files={len(self.project_files)}")
        return f"TaskContext({', '.join(parts)})"
