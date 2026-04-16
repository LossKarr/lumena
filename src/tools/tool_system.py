"""
LUMENA - Système de Tools (Facade vers ToolRegistry V2)

Facade conservée pour compatibilité (chat_with_tools, runtime context, bindings).
Toute l'exécution est déléguée à ToolRegistry V2 (src/reasoning/tool_registry.py).
"""

from typing import Dict, Any, List, Optional, Callable, Awaitable, Union
from dataclasses import dataclass
from pathlib import Path
import asyncio
import contextvars
import json
import re
import os
from loguru import logger

from .file_guardrails import WorkspaceFileGuardrails
try:
    from ..runtime.context import get_current_runtime_context
except Exception:
    get_current_runtime_context = None  # runtime context non disponible


try:
    from ..telemetry import (
        current_trace_context,
        publish_trace,
        get_file_edits_store,
        compute_workspace_relative,
        read_text_if_exists,
    )
    TELEMETRY_AVAILABLE = True
except (ImportError, AttributeError) as e:
    logger.debug("Module telemetry non disponible: {}", e)
    TELEMETRY_AVAILABLE = False
    current_trace_context = None
    get_file_edits_store = None
    compute_workspace_relative = None
    read_text_if_exists = None


@dataclass
class ToolCall:
    """Un appel d'outil par le LLM."""
    name: str
    arguments: Dict[str, Any]
    call_id: str = ""
    
    
@dataclass
class ToolResult:
    """Résultat d'exécution d'un outil."""
    success: bool
    output: str
    call_id: str = ""
    error: Optional[str] = None


class LumenaToolSystem:
    """
    Système de tools automatique pour LUMENA.
    
    Les outils sont toujours disponibles et le LLM
    décide lui-même quand les utiliser. Pas besoin de préfixe "!".
    """
    
    def __init__(self, lumena_root: Optional[Path] = None):
        self.tools: Dict[str, Any] = {}  # Legacy (toujours vide depuis P1.2.7)
        self.lumena_root = (lumena_root or Path(__file__).parent.parent.parent).resolve()
        self.default_workspace_root = self._resolve_default_workspace_root()
        self.default_workspace_root.mkdir(parents=True, exist_ok=True)
        self.file_guardrails = WorkspaceFileGuardrails(self.lumena_root)
        self._memory_provider: Any = None
        self._canonical_memory_provider: Any = None
        self._pending_results: List[ToolResult] = []
        self._runtime_workspace_root = contextvars.ContextVar(
            "lumena_tool_workspace_root",
            default=None,
        )
        self._runtime_active_file_path = contextvars.ContextVar(
            "lumena_tool_active_file_path",
            default=None,
        )
        self._runtime_open_files = contextvars.ContextVar(
            "lumena_tool_open_files",
            default=(),
        )
        self._web_crawler_instance = None
        self._file_crawler_instance = None
        self._mail_hub_instance = None
        self._critical_alert_hub_instance = None
        self._telegram_document_sender: Optional[Callable[[str, str, str], Awaitable[bool]]] = None
        self._whatsapp_document_sender: Optional[Callable[[str, str, str], Awaitable[bool]]] = None
        self._tool_registry: Any = None  # P2.2: délégation vers ToolRegistry
        logger.info("0 outils legacy enregistres dans le Tool System")

    def _resolve_default_workspace_root(self) -> Path:
        raw = os.getenv("LUMENA_DEFAULT_WORKSPACE", "").strip()
        if raw:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = (self.lumena_root / candidate).resolve()
            else:
                candidate = candidate.resolve()
            return candidate
        from ..utils.paths import WORKSPACE_DIR
        return WORKSPACE_DIR

    def bind_memory(self, memory_provider: Any) -> None:
        """Bind the canonical memory provider from LumenaCore."""
        self._memory_provider = memory_provider
        if memory_provider is not None:
            logger.debug("ToolSystem memoire liee au provider canonique (LumenaCore.memory)")

    def bind_tool_registry(self, registry: Any) -> None:
        """Bind ToolRegistry pour déléguer l'exécution (P2.2 unification)."""
        self._tool_registry = registry
        if registry is not None:
            logger.info("ToolSystem lié au ToolRegistry ({} handlers)", len(getattr(registry, 'tools', {})))

    def bind_telegram_document_sender(
        self,
        sender: Optional[Callable[[str, str, str], Awaitable[bool]]],
    ) -> None:
        """Bind async sender callback: (file_path, chat_id, caption) -> bool."""
        self._telegram_document_sender = sender

    def bind_whatsapp_document_sender(
        self,
        sender: Optional[Callable[[str, str, str], Awaitable[bool]]],
    ) -> None:
        """Bind async sender callback WhatsApp: (file_path, phone, caption) -> bool."""
        self._whatsapp_document_sender = sender

    @staticmethod
    def _infer_workspace_from_file_paths(
        active_file_path: Optional[Union[str, Path]],
        open_files: Optional[List[str]],
    ) -> Optional[str]:
        markers = (
            ".git",
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "composer.json",
        )
        candidates: List[Path] = []

        if active_file_path:
            candidates.append(Path(str(active_file_path)))
        for item in (open_files or [])[:30]:
            value = str(item).strip()
            if value:
                candidates.append(Path(value))

        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except Exception:
                continue  # chemin non résolvable
            if not resolved.exists() or not resolved.is_file():
                continue

            current = resolved.parent
            for _ in range(10):
                if any((current / marker).exists() for marker in markers):
                    return str(current)
                if current.parent == current:
                    break
                current = current.parent
            return str(resolved.parent)

        return None

    def _get_memory_provider(self) -> Any:
        """Return bound memory provider or canonical fallback store."""
        if self._memory_provider is not None:
            return self._memory_provider

        if self._canonical_memory_provider is None:
            from ..memory.chromadb_store import LumenaMemory
            from ..utils.paths import MEMORY_DIR
            canonical_memory_dir = MEMORY_DIR
            self._canonical_memory_provider = LumenaMemory(canonical_memory_dir)
            logger.debug(
                f"ToolSystem memoire fallback canonique initialisee: {canonical_memory_dir}"
            )

        return self._canonical_memory_provider

    def push_runtime_context(
        self,
        *,
        workspace_root: Optional[Union[str, Path]] = None,
        active_file_path: Optional[Union[str, Path]] = None,
        open_files: Optional[List[str]] = None,
    ) -> Dict[str, contextvars.Token]:
        """Push runtime IDE context for a single request/thread."""
        tokens: Dict[str, contextvars.Token] = {}
        open_files_list = list(open_files or [])

        resolved_workspace_root: Optional[str] = None

        if workspace_root:
            try:
                resolved_root = Path(str(workspace_root)).expanduser().resolve()
                if resolved_root.exists() and resolved_root.is_dir():
                    resolved_workspace_root = str(resolved_root)
            except Exception:
                pass  # chemin workspace non résolvable, fallback

        if not resolved_workspace_root:
            inferred_workspace = self._infer_workspace_from_file_paths(active_file_path, open_files_list)
            if inferred_workspace:
                resolved_workspace_root = inferred_workspace
        if not resolved_workspace_root:
            resolved_workspace_root = str(self.default_workspace_root)

        if resolved_workspace_root:
            tokens["workspace_root"] = self._runtime_workspace_root.set(resolved_workspace_root)

        if active_file_path:
            try:
                resolved_active = Path(str(active_file_path)).expanduser().resolve()
                tokens["active_file_path"] = self._runtime_active_file_path.set(str(resolved_active))
            except Exception:
                tokens["active_file_path"] = self._runtime_active_file_path.set(str(active_file_path))  # fallback chemin brut

        if open_files is not None:
            cleaned: List[str] = []
            for item in open_files_list[:30]:
                value = str(item).strip()
                if not value:
                    continue
                cleaned.append(value)
            tokens["open_files"] = self._runtime_open_files.set(tuple(cleaned))

        return tokens

    def pop_runtime_context(self, tokens: Dict[str, contextvars.Token]) -> None:
        mapping = {
            "workspace_root": self._runtime_workspace_root,
            "active_file_path": self._runtime_active_file_path,
            "open_files": self._runtime_open_files,
        }
        for key in ("open_files", "active_file_path", "workspace_root"):
            token = tokens.get(key)
            if token is None:
                continue
            try:
                mapping[key].reset(token)
            except Exception:
                pass  # contextvars reset best-effort

    def get_runtime_context(self) -> Dict[str, Any]:
        return {
            "workspace_root": self._runtime_workspace_root.get(),
            "active_file_path": self._runtime_active_file_path.get(),
            "open_files": list(self._runtime_open_files.get() or ()),
        }

    def _get_effective_root(self) -> Path:
        runtime_root = self._runtime_workspace_root.get()
        if runtime_root:
            try:
                root_path = Path(runtime_root).resolve()
                if root_path.exists() and root_path.is_dir():
                    return root_path
            except Exception:
                pass  # chemin runtime non résolvable
        self.default_workspace_root.mkdir(parents=True, exist_ok=True)
        return self.default_workspace_root

    def _is_ide_runtime(self) -> bool:
        return bool(self._runtime_workspace_root.get())

    def _get_env_int(self, key: str, default: int, minimum: int = 1) -> int:
        raw = os.getenv(key)
        if raw is None:
            return default
        try:
            value = int(str(raw).strip())
            return max(minimum, value)
        except (TypeError, ValueError):
            return default

    def _ide_read_page_size(self) -> int:
        return self._get_env_int("LUMENA_IDE_READ_LINES", 200000, minimum=1000)

    def _ide_list_max_items(self) -> int:
        return self._get_env_int("LUMENA_IDE_LIST_ITEMS", 20000, minimum=200)

    def _ide_command_timeout_sec(self) -> Optional[int]:
        raw = os.getenv("LUMENA_IDE_COMMAND_TIMEOUT_SEC")
        if raw is None:
            return 3600
        try:
            value = int(str(raw).strip())
        except Exception:
            return 3600  # valeur par défaut si parsing échoue
        if value <= 0:
            return None
        return max(30, value)

    def _ide_command_output_limit(self) -> int:
        return self._get_env_int("LUMENA_IDE_OUTPUT_LIMIT", 2000000, minimum=20000)

    def _ide_grep_max_results(self, requested: int) -> int:
        floor = self._get_env_int("LUMENA_IDE_GREP_RESULTS", 10000, minimum=100)
        return max(requested, floor)

    def _ide_grep_max_file_size_bytes(self) -> int:
        mb = self._get_env_int("LUMENA_IDE_GREP_FILE_SIZE_MB", 50, minimum=1)
        return mb * 1024 * 1024

    def _ide_find_max_results(self) -> int:
        return self._get_env_int("LUMENA_IDE_FIND_RESULTS", 20000, minimum=200)

    def _get_file_guardrails(self) -> WorkspaceFileGuardrails:
        return WorkspaceFileGuardrails(self._get_effective_root())

    def _patch_strict_enabled(self) -> bool:
        # En mode IDE, permettre l'iteration rapide sans blocage patch-strict.
        if self._is_ide_runtime():
            return False
        raw = os.getenv("LUMENA_PATCH_STRICT", "1")
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def _file_cards_enabled(self) -> bool:
        raw = os.getenv("LUMENA_CHAT_FILE_CARDS", "1")
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def _trace_ids(self) -> tuple[Optional[str], Optional[str]]:
        if not TELEMETRY_AVAILABLE or current_trace_context is None:
            return None, None
        try:
            ctx = current_trace_context() or {}
            return ctx.get("trace_id"), ctx.get("turn_id")
        except Exception:
            return None, None  # telemetry context non disponible

    def _record_file_edit(
        self,
        *,
        tool_name: str,
        action: str,
        file_path: Path,
        before_content: Optional[str],
        after_content: Optional[str],
        existed_before: bool,
        summary: str,
        workspace_relative: Optional[str] = None,
    ) -> None:
        if not self._file_cards_enabled():
            return
        if not TELEMETRY_AVAILABLE or get_file_edits_store is None:
            return

        trace_id, turn_id = self._trace_ids()
        if not trace_id:
            return

        try:
            store = get_file_edits_store()
            store.start_edit_session(trace_id=trace_id, turn_id=turn_id)
            if workspace_relative is None and compute_workspace_relative is not None:
                workspace_relative = compute_workspace_relative(file_path, self._get_effective_root())
            task_id = None
            if callable(get_current_runtime_context):
                try:
                    runtime_ctx = get_current_runtime_context()
                    task_id = getattr(runtime_ctx, "task_id", None) if runtime_ctx else None
                except Exception:
                    task_id = None  # runtime context non disponible
            store.record_edit(
                trace_id=trace_id,
                turn_id=turn_id,
                task_id=task_id,
                tool_name=tool_name,
                action=action,
                file_path=str(file_path),
                workspace_relative=workspace_relative,
                before_content=before_content,
                after_content=after_content,
                existed_before=existed_before,
                summary=summary,
            )
        except Exception as exc:
            logger.debug("file_edit record skipped: {}", exc)
        
    def _iter_all_tools(self):
        """Itère sur tous les outils V2. Yields (name, description, parameters)."""
        if self._tool_registry and hasattr(self._tool_registry, 'tools'):
            for name, entry in self._tool_registry.tools.items():
                yield name, entry.get("description", ""), entry.get("parameters", {})

    def get_tools_for_provider(self, provider: str) -> List[Dict[str, Any]]:
        """
        Retourne les définitions d'outils formatées pour un provider spécifique.

        Chaque provider a un format différent pour les function calls.
        Utilise V2 + legacy-only pour exposer TOUS les outils.
        """
        tools_list = []

        for name, description, parameters in self._iter_all_tools():
            if provider == "openai":
                _props = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
                _req = parameters.get("required", []) if isinstance(parameters, dict) else []
                tools_list.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": {
                            "type": "object",
                            "properties": {
                                param_name: {
                                    "type": param.get("type", "string") if isinstance(param, dict) else "string",
                                    "description": param.get("description", "") if isinstance(param, dict) else "",
                                }
                                for param_name, param in _props.items()
                            },
                            "required": list(_req),
                        },
                    },
                })
            elif provider == "anthropic":
                _props = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
                _req = parameters.get("required", []) if isinstance(parameters, dict) else []
                tools_list.append({
                    "name": name,
                    "description": description,
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            param_name: {
                                "type": param.get("type", "string") if isinstance(param, dict) else "string",
                                "description": param.get("description", "") if isinstance(param, dict) else "",
                            }
                            for param_name, param in _props.items()
                        },
                        "required": list(_req),
                    },
                })
            else:
                tools_list.append({
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                })

        return tools_list
    
    def get_tools_prompt_section(self) -> str:
        """
        Génère la section du system prompt qui décrit les outils.
        
        Le prompt explique QUAND et COMMENT utiliser les outils.
        """
        lines = [
            "## 🛠️ Outils Disponibles",
            "",
            "Tu as accès aux outils suivants. **UTILISE-LES OBLIGATOIREMENT** quand c'est pertinent.",
            "",
            "### ⚠️ RÈGLE CRITIQUE - FORMAT D'APPEL:",
            "Pour appeler un outil, tu DOIS utiliser CE FORMAT EXACT:",
            "```",
            "[TOOL:nom_outil] {\"argument\": \"valeur\"}",
            "```",
            "",
            "### Exemples concrets:",
            "- Météo → `[TOOL:web_search] {\"query\": \"météo Lille week-end\"}`",
            "- Créer fichier → `[TOOL:write_file] {\"path\": \"test.md\", \"content\": \"# Mon fichier\"}`",
            "- Chercher souvenir → `[TOOL:memory_search] {\"query\": \"ce que je sais sur l'utilisateur\"}`",
            "- Compter souvenirs → `[TOOL:memory_stats] {}`",
            "- Date/heure → `[TOOL:get_time] {}`",

            "",
            "### Quand utiliser les outils:",
            "- **web_search**: Météo, actualités, recherches internet → OBLIGATOIRE pour infos récentes",
            "- **web_fetch**: Lire le contenu d'une page web",
            "- **write_file**: Créer ou modifier un fichier",
            "- **read_file**: Lire un fichier existant",
            "- **edit_file**: Modifier une partie precise d'un fichier existant (search & replace)",
            "- **delete_file**: Supprimer definitivement un fichier (utilise le chemin absolu pour etre sur)",
            "- **create_directory**: Créer un dossier",
            "- **list_directory**: Lister le contenu d'un dossier",
            "- **memory_search**: Chercher dans tes souvenirs",
            "- **memory_add**: Mémoriser une information importante",
            "- **memory_stats**: Obtenir le nombre reel de souvenirs/facts",
            "- **get_time**: Obtenir la date et l'heure",

            "- **run_command**: Exécuter une commande système",
            "",
            "### Liste complète des outils:",
        ]
        
        for name, description, parameters in self._iter_all_tools():
            params_str = ", ".join([f'"{ k}": "{v.get("type", "string") if isinstance(v, dict) else v}"' for k, v in parameters.items()])
            lines.append(f"- **{name}**: {description}")
            if params_str:
                lines.append(f"  Format: `[TOOL:{name}] {{{params_str}}}`")
        
        lines.extend([
            "",
            "### ⚡ Style d'utilisation:",
            "- **NE DIS JAMAIS** 'je vais chercher' ou 'je m'en occupe' - APPELLE L'OUTIL DIRECTEMENT",
            "- **NE FABRIQUE PAS** d'informations - utilise web_search pour les données réelles",
            "- Un outil par ligne, puis continue ta réponse après le résultat",
        ])
        
        return "\n".join(lines)
    
    @property
    def tool_count(self) -> int:
        """Nombre total d'outils disponibles."""
        if self._tool_registry and hasattr(self._tool_registry, 'tools'):
            return len(self._tool_registry.tools)
        return 0

    async def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """Exécute un appel d'outil via ToolRegistry V2."""
        if (
            self._tool_registry is not None
            and hasattr(self._tool_registry, 'tools')
            and tool_call.name in self._tool_registry.tools
        ):
            try:
                obs = await self._tool_registry.execute(tool_call.name, tool_call.arguments or {})
                _obs_success = getattr(obs, 'success', True)
                _obs_content = getattr(obs, 'content', str(obs))
                return ToolResult(
                    success=_obs_success,
                    output=_obs_content,
                    call_id=tool_call.call_id,
                    error=_obs_content if not _obs_success else None,
                )
            except Exception as e:
                logger.error(f"❌ ToolRegistry execution failed for {tool_call.name}: {e}")
                return ToolResult(
                    success=False,
                    output="",
                    call_id=tool_call.call_id,
                    error=str(e),
                )

        return ToolResult(
            success=False,
            output="",
            call_id=tool_call.call_id,
            error=f"Outil '{tool_call.name}' non trouvé"
        )
    
    async def execute_tool_by_name(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Compatibility wrapper for callers that pass name+args directly."""
        result = await self.execute_tool(
            ToolCall(name=tool_name, arguments=arguments or {})
        )
        if result.success:
            return result.output
        return f"❌ Erreur: {result.error or f'Execution echouee pour {tool_name}'}"

    def _is_known_tool(self, name: str) -> bool:
        """Vérifie si un outil existe (V2 ou legacy)."""
        if self._tool_registry and hasattr(self._tool_registry, 'tools') and name in self._tool_registry.tools:
            return True
        return name in self.tools

    def parse_tool_calls_from_text(self, text: str) -> List[ToolCall]:
        """
        Parse les appels d'outils depuis une réponse textuelle.
        
        Pour les modèles qui n'ont pas de function calling natif (ex: Qwen local),
        on parse le texte pour détecter les intentions d'utiliser des outils.
        
        Formats supportés:
        - `tool_name(arg1="value1", arg2="value2")`
        - `[TOOL:tool_name] {"arg1": "value1"}`
        - Action: tool_name avec arg1="value1"
        """
        tool_calls = []
        
        # Pattern 1: tool_name(args)
        pattern1 = r'(\w+)\s*\(\s*([^)]*)\s*\)'
        for match in re.finditer(pattern1, text):
            name = match.group(1)
            if self._is_known_tool(name):
                args_str = match.group(2)
                args = self._parse_args_string(args_str)
                tool_calls.append(ToolCall(name=name, arguments=args))
        
        # Pattern 2: [TOOL:name] {json}
        # Parsing robuste: supporte JSON imbrique (ex: contenu avec accolades).
        pattern2 = r'\[TOOL:(\w+)\]'
        for match in re.finditer(pattern2, text):
            name = match.group(1)
            if not self._is_known_tool(name):
                continue

            extracted = self._extract_balanced_json_object(text, match.end())
            if not extracted:
                continue

            json_payload, _ = extracted
            try:
                args = json.loads(json_payload)
                if isinstance(args, dict):
                    tool_calls.append(ToolCall(name=name, arguments=args))
            except json.JSONDecodeError:
                continue
        
        # Pattern 3: Action: tool_name avec arg=value
        pattern3 = r'Action:\s*(\w+)\s+avec\s+(.+)'
        for match in re.finditer(pattern3, text, re.IGNORECASE):
            name = match.group(1)
            if self._is_known_tool(name):
                args_str = match.group(2)
                args = self._parse_args_string(args_str)
                tool_calls.append(ToolCall(name=name, arguments=args))

        if TELEMETRY_AVAILABLE and tool_calls:
            for call in tool_calls:
                publish_trace(
                    stage="tool_parse",
                    status="ok",
                    tool_name=call.name,
                    summary=str(call.arguments),
                )
        
        return tool_calls
    
    def _parse_args_string(self, args_str: str) -> Dict[str, Any]:
        """Parse une chaîne d'arguments en dict."""
        args = {}
        # Pattern: key="value" ou key=value
        pattern = r'(\w+)\s*=\s*["\']?([^"\'=,]+)["\']?'
        for match in re.finditer(pattern, args_str):
            args[match.group(1)] = match.group(2).strip()
        return args
    
    def _extract_balanced_json_object(self, text: str, start_index: int) -> Optional[tuple[str, int]]:
        """
        Extrait un objet JSON complet a partir d'un index.

        Retourne:
            (json_text, end_index) si trouve, sinon None.
        """
        i = start_index
        while i < len(text) and text[i].isspace():
            i += 1

        if i >= len(text) or text[i] != "{":
            return None

        depth = 0
        in_string = False
        escaped = False

        for j in range(i, len(text)):
            ch = text[j]

            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == "\"":
                    in_string = False
                continue

            if ch == "\"":
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[i:j + 1], j + 1

        return None


# Instance globale avec lock thread-safe (Phase 2.1)
import threading
_tool_system: Optional[LumenaToolSystem] = None
_tool_system_lock = threading.Lock()

def get_tool_system() -> LumenaToolSystem:
    """Retourne l'instance globale du système de tools (thread-safe)."""
    global _tool_system
    
    # Double-check locking pattern
    if _tool_system is None:
        with _tool_system_lock:
            if _tool_system is None:
                _tool_system = LumenaToolSystem()
    return _tool_system
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
