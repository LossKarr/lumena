"""
🧠 LUMENA - Context Compaction System

Gère la compression du contexte pour permettre des conversations
infinies sans dépasser les limites du LLM.

Stratégies:
1. Estimation de tokens
2. Résumé progressif des vieux messages
3. Fallback pour messages trop grands
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import logging
import re

logger = logging.getLogger("lumena.compaction")

# ─── Tiktoken (précision tokens) ──────────────────────────────────────────────
try:
    import tiktoken
    _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_AVAILABLE = True
except Exception:
    _TIKTOKEN_ENC = None
    _TIKTOKEN_AVAILABLE = False

# Constantes
DEFAULT_CONTEXT_TOKENS = int(os.environ.get("LUMENA_DEFAULT_CONTEXT_TOKENS", "128000"))  # Gemini 2.0 Flash
BASE_CHUNK_RATIO = 0.4  # Ratio pour le splitting
MIN_CHUNK_RATIO = 0.15
SAFETY_MARGIN = 1.2  # 20% de buffer pour l'estimation
DEFAULT_SUMMARY_FALLBACK = "Pas d'historique précédent."


def count_words(text: str) -> int:
    """Compte les mots dans un texte."""
    return len(text.split())


def _count_str_tokens(text: str) -> int:
    """Compte les tokens d'une string — tiktoken si dispo, sinon heuristique."""
    if _TIKTOKEN_AVAILABLE and _TIKTOKEN_ENC is not None:
        try:
            return max(1, len(_TIKTOKEN_ENC.encode(text, disallowed_special=())))
        except Exception:
            pass
    return max(1, len(text) // 4)


def estimate_tokens(content: Any) -> int:
    """
    Estime le nombre de tokens pour un contenu.
    
    Utilise tiktoken (cl100k_base) si disponible, sinon fallback ~4 chars/token.
    """
    if isinstance(content, str):
        return _count_str_tokens(content)
    elif isinstance(content, dict):
        # Message format
        text = content.get("content", "")
        if isinstance(text, str):
            return _count_str_tokens(text)
        elif isinstance(text, list):
            # Multi-part content
            total = 0
            for part in text:
                if isinstance(part, dict):
                    total += _count_str_tokens(part.get("text", ""))
                else:
                    total += _count_str_tokens(str(part))
            return total
    elif isinstance(content, list):
        return sum(estimate_tokens(item) for item in content)
    return _count_str_tokens(str(content))


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    """Estime le total de tokens pour une liste de messages."""
    return sum(estimate_tokens(msg) for msg in messages)


# ── P2 Plan Suprême : progressive tool-output pruning ────────────────────


_OBSERVATION_PREFIXES = (
    "Résultat de l'action:",
    "Resultat de l'action:",
    "OBSERVATION:",
    "Observation:",
    "Tool result:",
    "Tool output:",
)


def _looks_like_observation(msg: Dict[str, Any]) -> bool:
    """Heuristique : message user généré par une tool exec (pas une vraie question)."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content", "")
    if not isinstance(content, str):
        return False
    head = content.lstrip()[:80]
    return any(head.startswith(p) for p in _OBSERVATION_PREFIXES)


def prune_large_observations(
    messages: List[Dict[str, Any]],
    *,
    max_obs_chars: int = 3000,
    keep_recent: int = 3,
    head_chars: int = 1500,
    tail_chars: int = 500,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Réduit la taille des observations anciennes en gardant head + tail,
    les récentes restent intactes. Opt-out via LUMENA_COMPACTION_PRUNE=0.

    Args:
        messages: liste à traiter (non modifiée)
        max_obs_chars: seuil au-delà duquel une observation est considérée "large"
        keep_recent: nb d'observations récentes à préserver intactes
        head_chars: chars à garder en tête de l'observation pruned
        tail_chars: chars à garder en queue de l'observation pruned

    Returns:
        (new_messages, pruned_count) — copie enrichie + compteur diagnostic.
    """
    try:
        from src.config.codeagent_flags import COMPACTION_PRUNE
        if not COMPACTION_PRUNE:
            return list(messages), 0
    except Exception:
        return list(messages), 0

    # Repérer les indices des observations
    obs_indices = [i for i, m in enumerate(messages) if _looks_like_observation(m)]
    if len(obs_indices) <= keep_recent:
        return list(messages), 0

    # Les dernières `keep_recent` observations restent intactes
    protected = set(obs_indices[-keep_recent:])
    out: List[Dict[str, Any]] = []
    pruned_count = 0

    for i, msg in enumerate(messages):
        if i in protected or not _looks_like_observation(msg):
            out.append(msg)
            continue
        content = msg.get("content", "")
        if len(content) <= max_obs_chars:
            out.append(msg)
            continue
        # Prune : head + marker + tail
        removed = len(content) - head_chars - tail_chars
        pruned_content = (
            content[:head_chars]
            + f"\n\n[... {removed} chars pruned (observation ancienne compactée) ...]\n\n"
            + content[-tail_chars:]
        )
        out.append({**msg, "content": pruned_content})
        pruned_count += 1

    return out, pruned_count


@dataclass
class CompactionResult:
    """Résultat de la compaction."""
    messages: List[Dict[str, Any]]
    was_compacted: bool = False
    dropped_messages: int = 0
    dropped_tokens: int = 0
    summary: Optional[str] = None


class ContextCompactor:
    """
    Gère la compaction du contexte pour les conversations longues.
    
    Utilise un système de résumé progressif pour garder les informations
    importantes tout en réduisant la taille du contexte.
    """
    
    def __init__(
        self, 
        max_context_tokens: int = DEFAULT_CONTEXT_TOKENS,
        compaction_threshold: float = float(os.environ.get("LUMENA_CONTEXT_COMPACTION_THRESHOLD", "0.6")),  # Compacter à 60% du max
        keep_recent_turns: int = 5,  # Toujours garder les N derniers échanges
        llm_summarizer = None  # Fonction async pour résumer
    ):
        self.max_context_tokens = max_context_tokens
        self.compaction_threshold = compaction_threshold
        self.keep_recent_turns = keep_recent_turns
        self.llm_summarizer = llm_summarizer
        self._summary_cache: Dict[str, str] = {}
    
    async def compact_if_needed(
        self, 
        messages: List[Dict[str, Any]],
        force: bool = False
    ) -> CompactionResult:
        """
        Compacte les messages si nécessaire.
        
        Args:
            messages: Liste des messages
            force: Forcer la compaction même si pas nécessaire
        
        Returns:
            CompactionResult avec les messages (potentiellement compactés)
        """
        current_tokens = estimate_messages_tokens(messages)
        threshold_tokens = int(self.max_context_tokens * self.compaction_threshold)
        
        if not force and current_tokens <= threshold_tokens:
            return CompactionResult(messages=messages, was_compacted=False)
        
        logger.info(f"🔄 Compaction nécessaire: {current_tokens} tokens > {threshold_tokens}")
        
        # Séparer les messages récents (à garder) des anciens (à résumer)
        if len(messages) <= self.keep_recent_turns * 2:
            # Pas assez de messages pour compacter utilement
            return CompactionResult(messages=messages, was_compacted=False)
        
        # Garder les derniers échanges (user + assistant pairs)
        recent_count = self.keep_recent_turns * 2
        old_messages = messages[:-recent_count] if recent_count > 0 else messages
        recent_messages = messages[-recent_count:] if recent_count > 0 else []
        
        # Résumer les anciens messages
        summary = await self._summarize_messages(old_messages)
        
        # Créer le message de résumé
        summary_message = {
            "role": "system",
            "content": f"📋 **Résumé de la conversation précédente:**\n\n{summary}"
        }
        
        # Nouveau contexte: résumé + messages récents
        new_messages = [summary_message] + recent_messages
        
        return CompactionResult(
            messages=new_messages,
            was_compacted=True,
            dropped_messages=len(old_messages),
            dropped_tokens=estimate_messages_tokens(old_messages),
            summary=summary
        )
    
    async def _summarize_messages(self, messages: List[Dict[str, Any]]) -> str:
        """
        Résume une liste de messages.
        
        Si un LLM summarizer est disponible, l'utilise.
        Sinon, fait un résumé extractif simple.
        """
        if self.llm_summarizer:
            try:
                return await self.llm_summarizer(messages)
            except Exception as e:
                logger.warning(f"LLM summarizer failed: {e}, falling back to extractive")
        
        # Résumé extractif simple
        return self._extractive_summary(messages)
    
    def _extractive_summary(self, messages: List[Dict[str, Any]]) -> str:
        """
        Crée un résumé extractif des messages.
        
        Extrait les informations clés:
        - Décisions prises
        - Actions effectuées
        - Fichiers mentionnés
        - Erreurs rencontrées
        """
        summaries = []
        files_mentioned = set()
        actions_taken = []
        errors = []
        
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                # Extraire les fichiers mentionnés
                file_patterns = re.findall(r'[\w\-]+\.(py|js|html|css|json|md|txt)', content, re.IGNORECASE)
                files_mentioned.update(file_patterns)
                
                # Extraire les actions (✅, 📝, etc.)
                action_patterns = re.findall(r'[✅📝🗑️💡]\s*[^.!?\n]+[.!?]?', content)
                actions_taken.extend(action_patterns[:3])  # Max 3 par message
                
                # Extraire les erreurs
                if "❌" in content or "erreur" in content.lower():
                    error_match = re.search(r'❌[^.!?\n]+[.!?]?', content)
                    if error_match:
                        errors.append(error_match.group(0))
        
        # Construire le résumé
        parts = []
        
        if files_mentioned:
            parts.append(f"**Fichiers concernés:** {', '.join(list(files_mentioned)[:10])}")
        
        if actions_taken:
            parts.append("**Actions effectuées:**")
            for action in actions_taken[:5]:
                parts.append(f"  - {action.strip()}")
        
        if errors:
            parts.append("**Erreurs rencontrées:**")
            for error in errors[:3]:
                parts.append(f"  - {error.strip()}")
        
        if not parts:
            parts.append(f"Conversation de {len(messages)} messages.")
        
        return "\n".join(parts)
    
    def prune_for_budget(
        self, 
        messages: List[Dict[str, Any]], 
        budget_tokens: int
    ) -> CompactionResult:
        """
        Élague les messages pour respecter un budget de tokens.
        
        Supprime les messages les plus anciens jusqu'à respecter le budget.
        """
        current_tokens = estimate_messages_tokens(messages)
        
        if current_tokens <= budget_tokens:
            return CompactionResult(messages=messages, was_compacted=False)
        
        # Supprimer des messages du début
        dropped_messages = []
        remaining = list(messages)
        
        while estimate_messages_tokens(remaining) > budget_tokens and len(remaining) > 2:
            dropped = remaining.pop(0)
            dropped_messages.append(dropped)
        
        return CompactionResult(
            messages=remaining,
            was_compacted=True,
            dropped_messages=len(dropped_messages),
            dropped_tokens=estimate_messages_tokens(dropped_messages)
        )


def split_by_tokens(
    messages: List[Dict[str, Any]], 
    ratio: float = 0.5
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Sépare les messages en deux parties selon un ratio de tokens.
    
    Args:
        messages: Liste des messages
        ratio: Ratio pour la première partie (0.5 = 50%)
    
    Returns:
        (première_partie, deuxième_partie)
    """
    total_tokens = estimate_messages_tokens(messages)
    target_tokens = int(total_tokens * ratio)
    
    first_part = []
    current_tokens = 0
    
    for msg in messages:
        msg_tokens = estimate_tokens(msg)
        if current_tokens + msg_tokens > target_tokens:
            break
        first_part.append(msg)
        current_tokens += msg_tokens
    
    second_part = messages[len(first_part):]
    
    return first_part, second_part


# === Token Statistics ===

@dataclass
class TokenStats:
    """Statistiques de tokens."""
    total: int
    by_role: Dict[str, int]
    message_count: int
    avg_per_message: float
    percent_of_limit: float


def get_token_stats(
    messages: List[Dict[str, Any]], 
    max_tokens: int = DEFAULT_CONTEXT_TOKENS
) -> TokenStats:
    """
    Calcule les statistiques de tokens pour une liste de messages.
    """
    total = 0
    by_role: Dict[str, int] = {}
    
    for msg in messages:
        tokens = estimate_tokens(msg)
        total += tokens
        role = msg.get("role", "unknown")
        by_role[role] = by_role.get(role, 0) + tokens
    
    return TokenStats(
        total=total,
        by_role=by_role,
        message_count=len(messages),
        avg_per_message=total / max(1, len(messages)),
        percent_of_limit=(total / max_tokens) * 100
    )


def format_token_stats(stats: TokenStats) -> str:
    """Formate les statistiques de tokens pour affichage."""
    lines = [
        f"📊 **Statistiques de tokens:**",
        f"  - Total: {stats.total:,} tokens ({stats.percent_of_limit:.1f}% du max)",
        f"  - Messages: {stats.message_count}",
        f"  - Moyenne: {stats.avg_per_message:.0f} tokens/msg",
    ]
    
    if stats.by_role:
        lines.append("  - Par rôle:")
        for role, tokens in stats.by_role.items():
            lines.append(f"    • {role}: {tokens:,}")
    
    return "\n".join(lines)


# === Tests ===
if __name__ == "__main__":
    # Test estimation
    test_text = "Ceci est un test de l'estimation de tokens."
    print(f"Tokens estimés: {estimate_tokens(test_text)}")
    
    # Test messages
    test_messages = [
        {"role": "user", "content": "Bonjour, comment vas-tu?"},
        {"role": "assistant", "content": "Je vais bien, merci! Comment puis-je t'aider?"},
        {"role": "user", "content": "Peux-tu créer un fichier test.py?"},
        {"role": "assistant", "content": "✅ Fichier test.py créé avec succès!"},
    ]
    
    stats = get_token_stats(test_messages)
    print(format_token_stats(stats))
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
