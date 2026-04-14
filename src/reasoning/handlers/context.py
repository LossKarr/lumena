"""
context.py - HandlerContext: tout ce dont un handler fragmenté a besoin.

Remplace l'accès via `self` (ToolRegistry) dans les handlers legacy.
Chaque handler standalone reçoit un HandlerContext au lieu de `self`.

Construction: HandlerContext.from_tool_registry(registry) crée le contexte
à partir d'un ToolRegistry existant, garantissant la compatibilité.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..react import ToolRegistry


@dataclass
class HandlerContext:
    """
    Contexte unifié passé à chaque handler fragmenté.

    Regroupe toutes les dépendances que les handlers legacy accédaient
    via self.* sur ToolRegistry. Cela permet aux handlers d'être des
    fonctions standalone testables indépendamment.

    Attributes:
        lumena: Reference à LumenaCore (peut être None en tests).
        lumena_root: Racine du projet Lumena (Path absolue).
        runtime_root: Racine effective du workspace (Path absolue).
        ide_context: Contexte IDE normalisé (workspace_path, active_file_path, open_files).
        file_guardrails: Instance de WorkspaceFileGuardrails pour la résolution de chemins.
    """

    lumena: Any = None
    lumena_root: Path = field(default_factory=lambda: __import__('src.utils.paths', fromlist=['ROOT_DIR']).ROOT_DIR)
    runtime_root: Path = field(default_factory=lambda: Path.cwd())
    ide_context: Dict[str, Any] = field(default_factory=dict)
    file_guardrails: Any = None  # WorkspaceFileGuardrails

    # --- Budget temps hérité du ReAct loop ---
    budget_seconds: float = 600.0

    # --- Hub instances (lazy, optionnels) ---
    _mail_hub: Any = field(default=None, repr=False)
    _critical_alert_hub: Any = field(default=None, repr=False)
    _web_crawler: Any = field(default=None, repr=False)
    _document_hub: Any = field(default=None, repr=False)
    _search_hub: Any = field(default=None, repr=False)
    _spotify_hub: Any = field(default=None, repr=False)
    _notion_hub: Any = field(default=None, repr=False)

    # --- État mutable partagé ---
    _opened_apps_history: List[str] = field(default_factory=list, repr=False)
    _discovered_executables: set = field(default_factory=set, repr=False)

    # --- Référence au ToolRegistry parent (pour custom_tool_load/create) ---
    _tool_registry_ref: Any = field(default=None, repr=False)

    # ─── Helpers de config (reproduisent les méthodes de ToolRegistry) ─────

    def is_ide_runtime(self) -> bool:
        """True si le contexte IDE a un workspace_path valide."""
        return bool(self.ide_context.get("workspace_path"))

    @staticmethod
    def _get_env_int(key: str, default: int, minimum: int = 1) -> int:
        raw = os.getenv(key)
        if raw is None:
            return default
        try:
            value = int(str(raw).strip())
            return max(minimum, value)
        except (TypeError, ValueError):
            return default

    def ide_read_page_size(self) -> int:
        return self._get_env_int("LUMENA_IDE_READ_LINES", 200000, minimum=1000)

    def ide_list_max_items(self) -> int:
        return self._get_env_int("LUMENA_IDE_LIST_ITEMS", 20000, minimum=200)

    def ide_find_max_results(self) -> int:
        return self._get_env_int("LUMENA_IDE_FIND_RESULTS", 20000, minimum=200)

    def ide_command_timeout_sec(self) -> Optional[int]:
        raw = os.getenv("LUMENA_IDE_COMMAND_TIMEOUT_SEC")
        if raw is None:
            return 3600
        try:
            value = int(str(raw).strip())
        except Exception:
            return 3600
        if value <= 0:
            return None
        return max(30, value)

    def ide_command_output_limit(self) -> int:
        return self._get_env_int("LUMENA_IDE_OUTPUT_LIMIT", 2000000, minimum=20000)

    def patch_strict_enabled(self) -> bool:
        if self.is_ide_runtime():
            return False
        raw = os.getenv("LUMENA_PATCH_STRICT", "1")
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def resolve_path(self, path: str, *, want_dir: bool = False) -> Path:
        """
        Résout un chemin utilisateur via file_guardrails.

        Equivalent de ToolRegistry._resolve_path(path).
        """
        p = Path(path)

        # Priorité au runtime_root pour les chemins relatifs: en mode agent/IDE,
        # "." doit pointer sur le workspace effectif, pas forcément sur lumena_root.
        if not p.is_absolute():
            # Éviter le piège workspace/workspace: si l'agent passe "workspace/...",
            # on l'interprète comme relatif au runtime_root courant.
            p_norm = p.as_posix()
            if p_norm == "workspace":
                rel = Path(".")
            elif p_norm.startswith("workspace/"):
                stripped = p_norm[len("workspace/"):]
                rel = Path(stripped) if stripped else Path(".")
            else:
                rel = p

            runtime_candidate = (self.runtime_root / rel).resolve()
            if runtime_candidate.exists():
                return runtime_candidate
        else:
            runtime_candidate = p.resolve()

        if self.file_guardrails is not None:
            return self.file_guardrails.resolve_user_path(path, want_dir=want_dir)

        # Fallback sans guardrails (tests légers)
        return runtime_candidate

    # ─── Hub accessors (lazy init, même pattern que ToolRegistry) ──────────

    def get_mail_hub(self):
        """Retourne le MailHub, le crée si nécessaire."""
        if self._mail_hub is None:
            from ...tools.mail_hub import MailHub
            from ...utils.paths import MAIL_DIR
            self._mail_hub = MailHub(MAIL_DIR)
        return self._mail_hub

    def get_critical_alert_hub(self):
        """Retourne le CriticalAlertHub, le crée si nécessaire."""
        if self._critical_alert_hub is None:
            from ...tools.critical_alert_hub import CriticalAlertHub
            from ...utils.paths import ALERTS_DIR
            self._critical_alert_hub = CriticalAlertHub(ALERTS_DIR)
        return self._critical_alert_hub

    def get_web_crawler(self):
        """Retourne le WebCrawler, le crée si nécessaire."""
        if self._web_crawler is None:
            from ...tools.web_crawler import WebCrawler
            from ...utils.paths import CRAWLER_DIR
            self._web_crawler = WebCrawler(CRAWLER_DIR)
        return self._web_crawler

    def get_document_hub(self):
        """Retourne le DocumentHub, le crée si nécessaire."""
        if self._document_hub is None:
            from ...tools.document_hub import DocumentHub
            self._document_hub = DocumentHub(self.runtime_root)
        return self._document_hub

    def get_search_hub(self):
        """Retourne le SearchHub, le crée si nécessaire."""
        if self._search_hub is None:
            from ...tools.search_hub import SearchHub
            self._search_hub = SearchHub()
        return self._search_hub

    def get_spotify_hub(self):
        """Retourne le SpotifyHub, le crée si nécessaire."""
        if self._spotify_hub is None:
            from ...tools.spotify_hub import SpotifyHub
            self._spotify_hub = SpotifyHub()
        return self._spotify_hub

    def get_notion_hub(self):
        """Retourne le NotionHub, le crée si nécessaire."""
        if self._notion_hub is None:
            from ...tools.notion_hub import NotionHub
            self._notion_hub = NotionHub()
        return self._notion_hub

    # ─── Accès mémoire (via lumena) ────────────────────────────────────────

    @property
    def memory(self):
        """Accès au système de mémoire via lumena.memory."""
        if self.lumena and hasattr(self.lumena, "memory"):
            return self.lumena.memory
        return None

    # ─── Factory ───────────────────────────────────────────────────────────

    @classmethod
    def from_tool_registry(cls, registry: "ToolRegistry") -> "HandlerContext":
        """
        Construit un HandlerContext depuis un ToolRegistry existant.

        C'est le pont de compatibilité: Phase 7 utilisera cette méthode
        pour alimenter les handlers fragmentés depuis le ToolRegistry actuel.
        """
        return cls(
            lumena=registry.lumena,
            lumena_root=registry.lumena_root,
            runtime_root=registry.runtime_root,
            ide_context=registry.ide_context,
            file_guardrails=registry.file_guardrails,
            _mail_hub=registry._mail_hub_instance,
            _critical_alert_hub=registry._critical_alert_hub_instance,
            _web_crawler=registry._web_crawler_instance,
            _document_hub=registry._document_hub_instance,
            _search_hub=registry._search_hub_instance,
            _spotify_hub=registry._spotify_hub_instance,
            _notion_hub=registry._notion_hub_instance,
            _opened_apps_history=registry._opened_apps_history,
            _tool_registry_ref=registry,
        )

    @classmethod
    def for_testing(
        cls,
        *,
        lumena_root: Optional[Path] = None,
        runtime_root: Optional[Path] = None,
        ide_context: Optional[Dict[str, Any]] = None,
    ) -> "HandlerContext":
        """
        Construit un HandlerContext minimal pour les tests unitaires.

        Pas de lumena, pas de hubs — juste les paths et le file_guardrails.
        """
        from ...utils.paths import ROOT_DIR, WORKSPACE_DIR
        root = lumena_root or ROOT_DIR
        workspace = runtime_root or WORKSPACE_DIR
        workspace.mkdir(parents=True, exist_ok=True)

        # Import local pour éviter la dépendance circulaire
        try:
            from ...tools.file_guardrails import WorkspaceFileGuardrails
            # BUGFIX: utiliser root (lumena/) comme racine, pas workspace (lumena/workspace/).
            # Sinon root / "workspace" = lumena/workspace/workspace/ (double workspace).
            guardrails = WorkspaceFileGuardrails(root)
        except ImportError:
            guardrails = None

        return cls(
            lumena=None,
            lumena_root=root,
            runtime_root=workspace,
            ide_context=ide_context or {},
            file_guardrails=guardrails,
        )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
