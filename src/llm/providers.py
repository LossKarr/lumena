"""
🌟 LUMENA - Providers LLM Multi-Modèles

Supporte plusieurs providers LLM :
- Ollama (local) : Qwen, Llama, etc.
- OpenAI : GPT-5.4, GPT-5.4 Mini, GPT-4o
- Anthropic : Claude Opus 4.6, Claude Sonnet 4.6, Claude Sonnet 4.5
- Google : Gemini 3.1 Pro, Gemini 2.5 Flash
- Moonshot : Kimi K2.5
- xAI : Grok 4.1
- DeepSeek : V3.2, Reasoner
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, FrozenSet
from enum import Enum
from loguru import logger

# Charger le fichier .env automatiquement
try:
    from dotenv import load_dotenv
    # Chercher le .env dans le dossier racine du projet
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.debug(f"✅ Fichier .env chargé: {env_path}")
except ImportError:
    logger.warning("⚠️ python-dotenv non installé. Clés API depuis variables d'environnement uniquement.")


class ProviderType(Enum):
    """Types de providers LLM."""
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    MOONSHOT = "moonshot"
    DEEPSEEK = "deepseek"
    XAI = "xai"
    NVIDIA = "nvidia"
    MINIMAX = "minimax"
    ZAI = "zai"
    # Image generation providers
    STABILITY = "stability"
    FLUX = "flux"
    IDEOGRAM = "ideogram"
    RECRAFT = "recraft"
    REPLICATE = "replicate"
    HUGGINGFACE = "huggingface"


@dataclass
class ModelConfig:
    """Configuration d'un modèle LLM."""
    name: str
    display_name: str
    provider: ProviderType
    model_id: str
    context_window: int = 32000
    max_output_tokens: int = 4096
    supports_vision: bool = False
    supports_image_generation: bool = False
    supports_video_generation: bool = False
    supports_tools: bool = True
    cost_per_million_tokens: float = 0.0  # 0 = gratuit
    description: str = ""
    badge: str = ""  # ex: "Recommandé", "Gratuit", "Nouveau"
    capabilities: FrozenSet[str] = field(default_factory=frozenset)
    
    def is_local(self) -> bool:
        """Retourne True si le modèle tourne localement."""
        return self.provider == ProviderType.OLLAMA
    
    def is_free(self) -> bool:
        """Retourne True si le modèle est gratuit."""
        return self.cost_per_million_tokens == 0.0


# Configuration des modèles disponibles
AVAILABLE_MODELS: Dict[str, ModelConfig] = {
    # === OLLAMA (Local) ===
    "lumena-v1": ModelConfig(
        name="lumena-v1",
        display_name="Lumena v1.0.0 (Fine-tuné Local)",
        provider=ProviderType.OLLAMA,
        model_id="lumena-v1",
        context_window=32768,
        max_output_tokens=4096,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="Lumena v1.0.0 — QLoRA fine-tuné sur Qwen3-8B",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "qwen3-8b": ModelConfig(
        name="qwen3-8b",
        display_name="Qwen 3 8B (Local)",
        provider=ProviderType.OLLAMA,
        model_id="qwen3:8b",
        context_window=32000,
        max_output_tokens=4096,
        supports_vision=False,
        cost_per_million_tokens=0.0,
        description="Modèle local rapide et gratuit",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "qwen2.5-coder-14b": ModelConfig(
        name="qwen2.5-coder-14b",
        display_name="Qwen 2.5 Coder 14B (Local)",
        provider=ProviderType.OLLAMA,
        model_id="qwen2.5-coder:14b",
        context_window=32000,
        max_output_tokens=4096,
        supports_vision=False,
        cost_per_million_tokens=0.0,
        description="Excellent pour le code",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "deepseek-r1-7b": ModelConfig(
        name="deepseek-r1-7b",
        display_name="DeepSeek R1 7B (Local)",
        provider=ProviderType.OLLAMA,
        model_id="deepseek-r1:7b",
        context_window=32000,
        max_output_tokens=4096,
        supports_vision=False,
        cost_per_million_tokens=0.0,
        description="Raisonnement avancé",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    
    # === OPENAI ===
    "gpt-5.4": ModelConfig(
        name="gpt-5.4",
        display_name="GPT-5.4 (OpenAI)",
        provider=ProviderType.OPENAI,
        model_id="gpt-5.4",
        context_window=1000000,  # 1M tokens
        max_output_tokens=128000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=2.5,
        description="GPT-5.4 — flagship OpenAI, 1M context, 128K output, reasoning+code+agents",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use"}),
    ),
    "gpt-5.4-mini": ModelConfig(
        name="gpt-5.4-mini",
        display_name="GPT-5.4 Mini (OpenAI)",
        provider=ProviderType.OPENAI,
        model_id="gpt-5.4-mini",
        context_window=400000,  # 400K tokens
        max_output_tokens=128000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.75,
        description="GPT-5.4 Mini — rapide, 400K context, 128K output, computer use",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use"}),
    ),
    "gpt-4o": ModelConfig(
        name="gpt-4o",
        display_name="GPT-4o (OpenAI)",
        provider=ProviderType.OPENAI,
        model_id="gpt-4o",
        context_window=128000,
        max_output_tokens=16384,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=5.0,
        description="GPT-4o — legacy, remplacé par GPT-5.4",
        badge="Legacy",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    "gpt-5.4-nano": ModelConfig(
        name="gpt-5.4-nano",
        display_name="GPT-5.4 Nano (OpenAI)",
        provider=ProviderType.OPENAI,
        model_id="gpt-5.4-nano",
        context_window=128000,
        max_output_tokens=16384,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.10,
        description="GPT-5.4 Nano — ultra-léger rapide avec vision",
        capabilities=frozenset({"vision_describe", "tool_calling", "cheap_text"}),
    ),
    "gpt-4o-mini": ModelConfig(
        name="gpt-4o-mini",
        display_name="GPT-4o Mini (OpenAI)",
        provider=ProviderType.OPENAI,
        model_id="gpt-4o-mini",
        context_window=128000,
        max_output_tokens=16384,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.15,
        description="GPT-4o Mini — legacy économique vision + outils",
        badge="Legacy",
        capabilities=frozenset({"vision_describe", "tool_calling", "cheap_text"}),
    ),
    "gpt-4.1": ModelConfig(
        name="gpt-4.1",
        display_name="GPT-4.1 (OpenAI)",
        provider=ProviderType.OPENAI,
        model_id="gpt-4.1",
        context_window=1047576,  # ~1M tokens
        max_output_tokens=32768,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=2.0,
        description="GPT-4.1 — fallback stable non-reasoning, 1M context, tool use solide",
        badge="Fallback",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    "o3": ModelConfig(
        name="o3",
        display_name="o3 (OpenAI — Reasoning)",
        provider=ProviderType.OPENAI,
        model_id="o3",
        context_window=200000,
        max_output_tokens=100000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=10.0,
        description="o3 — raisonnement avancé OpenAI, 200K contexte, vision",
        badge="Reasoning",
        capabilities=frozenset({"vision_describe", "tool_calling", "reasoning"}),
    ),
    "o4-mini": ModelConfig(
        name="o4-mini",
        display_name="o4-mini (OpenAI — Reasoning)",
        provider=ProviderType.OPENAI,
        model_id="o4-mini",
        context_window=200000,
        max_output_tokens=65536,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=1.10,
        description="o4-mini — raisonnement rapide et économique, 200K contexte, vision",
        badge="Reasoning",
        capabilities=frozenset({"vision_describe", "tool_calling", "reasoning"}),
    ),
    "gpt-5.3-codex": ModelConfig(
        name="gpt-5.3-codex",
        display_name="GPT-5.3 Codex (OpenAI — Code)",
        provider=ProviderType.OPENAI,
        model_id="gpt-5.3-codex",
        context_window=400000,
        max_output_tokens=128000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=1.75,
        description="GPT-5.3 Codex — modèle agentique code le plus avancé, 400K contexte, reasoning xhigh, vision",
        badge="Code",
        capabilities=frozenset({"vision_describe", "tool_calling", "reasoning"}),
    ),

    # === ANTHROPIC ===
    "claude-opus-4.7": ModelConfig(
        name="claude-opus-4.7",
        display_name="Claude Opus 4.7 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-opus-4-7",
        context_window=1000000,  # 1M tokens
        max_output_tokens=128000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=5.0,
        description="Claude Opus 4.7 — frontier intelligence, agents+code, 1M context, 128K output",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use", "dom_assist"}),
    ),
    "claude-opus-4.6": ModelConfig(
        name="claude-opus-4.6",
        display_name="Claude Opus 4.6 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-opus-4-6",
        context_window=1000000,  # 1M tokens
        max_output_tokens=128000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=5.0,
        description="Claude Opus 4.6 — le plus intelligent, agents+code, 1M context, 128K output",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use", "dom_assist"}),
    ),
    "claude-opus-4.5": ModelConfig(
        name="claude-opus-4.5",
        display_name="Claude Opus 4.5 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-opus-4-5-20250514",
        context_window=1000000,  # 1M tokens
        max_output_tokens=64000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=4.0,
        description="Claude Opus 4.5 — intelligence Opus, 1M context, 64K output",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use", "dom_assist"}),
    ),
    "claude-opus-4": ModelConfig(
        name="claude-opus-4",
        display_name="Claude Opus 4 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-opus-4-20250514",
        context_window=200000,  # 200K tokens
        max_output_tokens=32000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=5.0,
        description="Claude Opus 4 — contexte standard 200K, plus accessible",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use"}),
    ),
    "claude-sonnet-4.6": ModelConfig(
        name="claude-sonnet-4.6",
        display_name="Claude Sonnet 4.6 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-sonnet-4-6",
        context_window=1000000,  # 1M tokens
        max_output_tokens=64000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=3.0,
        description="Claude Sonnet 4.6 — vitesse + intelligence, 1M context, 64K output",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use", "dom_assist"}),
    ),
    "claude-sonnet-4.5": ModelConfig(
        name="claude-sonnet-4.5",
        display_name="Claude Sonnet 4.5 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-sonnet-4-5-20250514",
        context_window=1000000,  # 1M tokens
        max_output_tokens=64000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=3.0,
        description="Claude Sonnet 4.5 — extended thinking, 1M context, 64K output",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use"}),
    ),
    "claude-sonnet-4": ModelConfig(
        name="claude-sonnet-4",
        display_name="Claude Sonnet 4 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-sonnet-4-20250514",
        context_window=200000,  # 200K tokens
        max_output_tokens=16000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=3.0,
        description="Claude Sonnet 4 — contexte standard 200K, bon rapport qualité/prix",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling"}),
    ),
    "claude-haiku-4.5": ModelConfig(
        name="claude-haiku-4.5",
        display_name="Claude Haiku 4.5 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-haiku-4-5-20250307",
        context_window=200000,  # 200K tokens
        max_output_tokens=16000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.8,
        description="Claude Haiku 4.5 — ultra-rapide et peu coûteux, 200K context",
        capabilities=frozenset({"vision_describe", "tool_calling", "cheap_text"}),
    ),
    "claude-3-7-sonnet": ModelConfig(
        name="claude-3-7-sonnet",
        display_name="Claude Sonnet 3.7 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-3-7-sonnet-20250219",
        context_window=200000,  # 200K tokens
        max_output_tokens=64000,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=3.0,
        description="Claude Sonnet 3.7 — extended thinking natif, 200K context",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use"}),
    ),
    "claude-3-5-sonnet": ModelConfig(
        name="claude-3-5-sonnet",
        display_name="Claude Sonnet 3.5 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-3-5-sonnet-20241022",
        context_window=200000,  # 200K tokens
        max_output_tokens=8192,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=3.0,
        description="Claude Sonnet 3.5 — excellent g\u00e9n\u00e9raliste \u00e9prouv\u00e9, 200K context",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    "claude-3-5-haiku": ModelConfig(
        name="claude-3-5-haiku",
        display_name="Claude Haiku 3.5 (Anthropic)",
        provider=ProviderType.ANTHROPIC,
        model_id="claude-3-5-haiku-20241022",
        context_window=200000,  # 200K tokens
        max_output_tokens=8192,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.8,
        description="Claude Haiku 3.5 — tr\u00e8s rapide et bon march\u00e9, 200K context",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    
    # === GOOGLE ===
    "gemini-3.1-pro": ModelConfig(
        name="gemini-3.1-pro",
        display_name="Gemini 3.1 Pro (Google)",
        provider=ProviderType.GOOGLE,
        model_id="gemini-3.1-pro-preview",
        context_window=1000000,
        max_output_tokens=65536,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=2.50,  # $2.50/M input, $10/M output
        description="Gemini 3.1 Pro — flagship Google, codage agentique, 1M context",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use"}),
    ),
    "gemini-2.5-flash": ModelConfig(
        name="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash (Google)",
        provider=ProviderType.GOOGLE,
        model_id="gemini-2.5-flash",  # Gemini 2.5 Flash
        context_window=1000000,
        max_output_tokens=65536,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.15,  # $0.15/M input, $0.60/M output (thinking $3.50/M)
        description="Gemini 2.5 Flash — rapide et abordable",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "cheap_text"}),
    ),
    "gemini-2.5-pro": ModelConfig(
        name="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro (Google)",
        provider=ProviderType.GOOGLE,
        model_id="gemini-2.5-pro",
        context_window=1048576,
        max_output_tokens=65536,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=1.25,  # $1.25/M input, $10/M output
        description="Gemini 2.5 Pro — 1M contexte, vision + grounding",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use"}),
    ),
    
    # === MOONSHOT (Kimi) ===
    "kimi-k2.5": ModelConfig(
        name="kimi-k2.5",
        display_name="Kimi K2.5 (Moonshot)",
        provider=ProviderType.MOONSHOT,
        model_id="kimi-k2.5",
        context_window=262144,          # 262K tokens (max context + output Kimi K2.5)
        max_output_tokens=262144,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.6,   # $0.60/M input, $3/M output, cache $0.10/M
        description="Kimi K2.5 — multimodal, agent swarm, 1M context, output 262K",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    "kimi-k2.6": ModelConfig(
        name="kimi-k2.6",
        display_name="Kimi K2.6 (Moonshot)",
        provider=ProviderType.MOONSHOT,
        model_id="kimi-k2.6",
        context_window=262144,          # 256K context window
        max_output_tokens=32768,        # 32K output par défaut
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.6,
        description="Kimi K2.6 — dernier modèle Moonshot, coding longue durée, multimodal (text/image/vidéo), thinking + non-thinking",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    "kimi-k2-0905-preview": ModelConfig(
        name="kimi-k2-0905-preview",
        display_name="Kimi K2 0905 Preview (Moonshot)",
        provider=ProviderType.MOONSHOT,
        model_id="kimi-k2-0905-preview",
        context_window=262144,          # 256K context window
        max_output_tokens=32768,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.6,
        description="Kimi K2 0905 Preview — long context + reasoning, Moonshot direct",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    "kimi-k2-turbo-preview": ModelConfig(
        name="kimi-k2-turbo-preview",
        display_name="Kimi K2 Turbo Preview (Moonshot)",
        provider=ProviderType.MOONSHOT,
        model_id="kimi-k2-turbo-preview",
        context_window=262144,          # 256K context window
        max_output_tokens=32768,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.6,
        description="Kimi K2 Turbo Preview — variante rapide, 256K contexte, Moonshot direct",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    "kimi-k2-thinking": ModelConfig(
        name="kimi-k2-thinking",
        display_name="Kimi K2 Thinking (Moonshot)",
        provider=ProviderType.MOONSHOT,
        model_id="kimi-k2-thinking",
        context_window=262144,          # 256K context window
        max_output_tokens=32768,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.6,
        description="Kimi K2 Thinking — mode raisonnement explicite, multi-step tool calling, Moonshot direct",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    "kimi-k2-thinking-turbo": ModelConfig(
        name="kimi-k2-thinking-turbo",
        display_name="Kimi K2 Thinking Turbo (Moonshot)",
        provider=ProviderType.MOONSHOT,
        model_id="kimi-k2-thinking-turbo",
        context_window=262144,          # 256K context window
        max_output_tokens=32768,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.6,
        description="Kimi K2 Thinking Turbo — thinking rapide, 256K contexte, Moonshot direct",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    
    # === XAI (Grok) ===
    "grok-4-1-fast-reasoning": ModelConfig(
        name="grok-4-1-fast-reasoning",
        display_name="Grok 4.1 Fast Reasoning (xAI)",
        provider=ProviderType.XAI,
        model_id="grok-4-1-fast-reasoning",
        context_window=131072,  # 128K tokens
        max_output_tokens=131072,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=2.0,
        description="Grok 4.1 Fast — raisonnement rapide, 128K contexte",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    "grok-4-1-fast-non-reasoning": ModelConfig(
        name="grok-4-1-fast-non-reasoning",
        display_name="Grok 4.1 Fast Non-Reasoning (xAI)",
        provider=ProviderType.XAI,
        model_id="grok-4-1-fast-non-reasoning",
        context_window=131072,
        max_output_tokens=131072,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=1.0,
        description="Grok 4.1 Fast — réponse directe sans raisonnement explicite",
        capabilities=frozenset({"vision_describe", "tool_calling"}),
    ),
    "grok-code-fast-1": ModelConfig(
        name="grok-code-fast-1",
        display_name="Grok Code Fast 1 (xAI)",
        provider=ProviderType.XAI,
        model_id="grok-code-fast-1",
        context_window=131072,
        max_output_tokens=131072,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=1.5,
        description="Grok Code Fast 1 — spécialisé code, ultra rapide",
        capabilities=frozenset({"tool_calling"}),
    ),
    "grok-4.20-0309-reasoning": ModelConfig(
        name="grok-4.20-0309-reasoning",
        display_name="Grok 4.20 Reasoning (xAI)",
        provider=ProviderType.XAI,
        model_id="grok-4.20-0309-reasoning",
        context_window=2_000_000,
        max_output_tokens=131072,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=2.0,
        description="Grok 4.20 — raisonnement avec 2M contexte",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use"}),
    ),
    "grok-4.20-0309-non-reasoning": ModelConfig(
        name="grok-4.20-0309-non-reasoning",
        display_name="Grok 4.20 Non-Reasoning (xAI)",
        provider=ProviderType.XAI,
        model_id="grok-4.20-0309-non-reasoning",
        context_window=2_000_000,
        max_output_tokens=131072,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=2.0,
        description="Grok 4.20 — réponses directes, 2M contexte",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use"}),
    ),
    "grok-4.20-multi-agent-0309": ModelConfig(
        name="grok-4.20-multi-agent-0309",
        display_name="Grok 4.20 Multi-Agent (xAI)",
        provider=ProviderType.XAI,
        model_id="grok-4.20-multi-agent-0309",
        context_window=2_000_000,
        max_output_tokens=131072,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=2.0,
        description="Grok 4.20 — optimisé multi-agents, 2M contexte",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling", "computer_use"}),
    ),

    # === NVIDIA NIM (Kimi gratuit via NVIDIA) ===
    "nvidia-kimi-k2-instruct": ModelConfig(
        name="nvidia-kimi-k2-instruct",
        display_name="Kimi K2 Instruct (NVIDIA NIM)",
        provider=ProviderType.NVIDIA,
        model_id="moonshotai/kimi-k2-instruct",
        context_window=131072,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="Kimi K2 Instruct — coding + agentic, gratuit via NVIDIA NIM",
        badge="Gratuit",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "nvidia-kimi-k2-instruct-0905": ModelConfig(
        name="nvidia-kimi-k2-instruct-0905",
        display_name="Kimi K2 Instruct 0905 (NVIDIA NIM)",
        provider=ProviderType.NVIDIA,
        model_id="moonshotai/kimi-k2-instruct-0905",
        context_window=131072,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="Kimi K2 Instruct 0905 — long-context + reasoning, gratuit via NVIDIA NIM",
        badge="Gratuit",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "nvidia-kimi-k2-thinking": ModelConfig(
        name="nvidia-kimi-k2-thinking",
        display_name="Kimi K2 Thinking (NVIDIA NIM)",
        provider=ProviderType.NVIDIA,
        model_id="moonshotai/kimi-k2-thinking",
        context_window=262144,
        max_output_tokens=65536,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="Kimi K2 Thinking — raisonnement avancé 256K, gratuit via NVIDIA NIM",
        badge="Gratuit",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "nvidia-deepseek-v3.2": ModelConfig(
        name="nvidia-deepseek-v3.2",
        display_name="DeepSeek V3.2 (NVIDIA NIM)",
        provider=ProviderType.NVIDIA,
        model_id="deepseek-ai/deepseek-v3.2",
        context_window=131072,
        max_output_tokens=16384,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="DeepSeek V3.2 685B — reasoning + agentic, gratuit via NVIDIA NIM",
        badge="Gratuit",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "nvidia-deepseek-v3.1": ModelConfig(
        name="nvidia-deepseek-v3.1",
        display_name="DeepSeek V3.1 Instruct (NVIDIA NIM)",
        provider=ProviderType.NVIDIA,
        model_id="deepseek-ai/deepseek-v3.1-instruct",
        context_window=131072,
        max_output_tokens=16384,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="DeepSeek V3.1 Instruct — fast reasoning + tool use, gratuit via NVIDIA NIM",
        badge="Gratuit",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "nvidia-glm-4.7": ModelConfig(
        name="nvidia-glm-4.7",
        display_name="GLM-4.7 (NVIDIA NIM)",
        provider=ProviderType.NVIDIA,
        model_id="z-ai/glm4.7",
        context_window=131072,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="GLM-4.7 — agentic coding, tool use, UI skills, multilingual, gratuit via NVIDIA NIM",
        badge="Gratuit",
        capabilities=frozenset({"vision_describe", "tool_calling", "cheap_text"}),
    ),
    "nvidia-minimax-m2.5": ModelConfig(
        name="nvidia-minimax-m2.5",
        display_name="MiniMax M2.5 (NVIDIA NIM)",
        provider=ProviderType.NVIDIA,
        model_id="minimaxai/minimax-m2.5",
        context_window=131072,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="MiniMax M2.5 via NVIDIA NIM — préférer MiniMax natif si MINIMAX_API_KEY configuré",
        badge="Gratuit",
        capabilities=frozenset({"vision_describe", "tool_calling", "cheap_text"}),
    ),

    # === MINIMAX (natif — API OpenAI-compatible) ===
    "minimax-m2.5": ModelConfig(
        name="minimax-m2.5",
        display_name="MiniMax M2.5",
        provider=ProviderType.MINIMAX,
        model_id="MiniMax-M2.5",
        context_window=204800,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.50,  # $0.50/M input, $1.10/M output
        description="MiniMax M2.5 — multi-language coding, agent integration, 204K contexte",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "minimax-m2.5-highspeed": ModelConfig(
        name="minimax-m2.5-highspeed",
        display_name="MiniMax M2.5 HighSpeed",
        provider=ProviderType.MINIMAX,
        model_id="MiniMax-M2.5-HighSpeed",
        context_window=204800,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.50,
        description="MiniMax M2.5 ~100 tokens/s, ultra-rapide",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "minimax-m2.1": ModelConfig(
        name="minimax-m2.1",
        display_name="MiniMax M2.1",
        provider=ProviderType.MINIMAX,
        model_id="MiniMax-M2.1",
        context_window=204800,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.50,
        description="MiniMax M2.1 — version stable, coding + agent",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "minimax-m2.1-highspeed": ModelConfig(
        name="minimax-m2.1-highspeed",
        display_name="MiniMax M2.1 HighSpeed",
        provider=ProviderType.MINIMAX,
        model_id="MiniMax-M2.1-HighSpeed",
        context_window=204800,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.50,
        description="MiniMax M2.1 ~100 tokens/s, ultra-rapide",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "minimax-m2.7": ModelConfig(
        name="minimax-m2.7",
        display_name="MiniMax M2.7",
        provider=ProviderType.MINIMAX,
        model_id="MiniMax-M2.7",
        context_window=204800,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.50,
        description="MiniMax M2.7 — dernier modèle, améliorations raisonnement",
        badge="beta",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),

    # === Z.AI (GLM — API OpenAI-compatible) ===
    "glm-5.1": ModelConfig(
        name="glm-5.1",
        display_name="GLM-5.1 (Z.AI)",
        provider=ProviderType.ZAI,
        model_id="glm-5.1",
        context_window=262144,
        max_output_tokens=65536,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=1.40,
        description="GLM-5.1 — flagship Z.AI, agentic engineering, tool calling avancé, 256K contexte",
        badge="Nouveau",
        capabilities=frozenset({"tool_calling", "reasoning"}),
    ),
    "glm-4.7-flashx": ModelConfig(
        name="glm-4.7-flashx",
        display_name="GLM-4.7 FlashX (Z.AI)",
        provider=ProviderType.ZAI,
        model_id="glm-4.7-flashx",
        context_window=262144,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.07,
        description="GLM-4.7 FlashX — ultra-économique, tool calling, excellent rapport qualité/prix",
        badge="Économique",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "glm-4.7-flash": ModelConfig(
        name="glm-4.7-flash",
        display_name="GLM-4.7 Flash (Z.AI)",
        provider=ProviderType.ZAI,
        model_id="glm-4.7-flash",
        context_window=262144,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="GLM-4.7 Flash — gratuit, tool calling, multilingue, 256K contexte",
        badge="Gratuit",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "glm-4.5-flash": ModelConfig(
        name="glm-4.5-flash",
        display_name="GLM-4.5 Flash (Z.AI)",
        provider=ProviderType.ZAI,
        model_id="glm-4.5-flash",
        context_window=262144,
        max_output_tokens=32768,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="GLM-4.5 Flash — gratuit, rapide, tool calling basique",
        badge="Gratuit",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "glm-4.6v-flash": ModelConfig(
        name="glm-4.6v-flash",
        display_name="GLM-4.6V Flash (Z.AI)",
        provider=ProviderType.ZAI,
        model_id="glm-4.6v-flash",
        context_window=262144,
        max_output_tokens=32768,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=0.0,
        description="GLM-4.6V Flash — vision gratuite, multimodal, analyse d'images",
        badge="Gratuit",
        capabilities=frozenset({"vision_describe", "tool_calling", "cheap_text"}),
    ),
    "glm-5v-turbo": ModelConfig(
        name="glm-5v-turbo",
        display_name="GLM-5V Turbo (Z.AI)",
        provider=ProviderType.ZAI,
        model_id="glm-5v-turbo",
        context_window=262144,
        max_output_tokens=32768,
        supports_vision=True,
        supports_tools=True,
        cost_per_million_tokens=1.20,
        description="GLM-5V Turbo — vision avancée, compréhension images haute fidélité",
        badge="Nouveau",
        capabilities=frozenset({"vision_describe", "vision_grounding", "tool_calling"}),
    ),
    "cogview-4": ModelConfig(
        name="cogview-4",
        display_name="CogView-4 (Z.AI)",
        provider=ProviderType.ZAI,
        model_id="cogview-4",
        context_window=0,
        max_output_tokens=0,
        supports_vision=False,
        supports_image_generation=True,
        supports_tools=False,
        cost_per_million_tokens=0.0,
        description="CogView-4 — génération d'images Z.AI, $0.01/image",
        badge="Nouveau",
        capabilities=frozenset({"image_generation"}),
    ),
    "cogview-4-flash": ModelConfig(
        name="cogview-4-flash",
        display_name="CogView-4 Flash (Z.AI)",
        provider=ProviderType.ZAI,
        model_id="cogview-4-flash",
        context_window=0,
        max_output_tokens=0,
        supports_vision=False,
        supports_image_generation=True,
        supports_tools=False,
        cost_per_million_tokens=0.0,
        description="CogView-4 Flash — génération d'images rapide Z.AI, $0.015/image",
        badge="Nouveau",
        capabilities=frozenset({"image_generation"}),
    ),

    # === DEEPSEEK V4 (disponible depuis le 24 avril 2026) ===
    "deepseek-v4-flash": ModelConfig(
        name="deepseek-v4-flash",
        display_name="DeepSeek V4-Flash (API)",
        provider=ProviderType.DEEPSEEK,
        model_id="deepseek-v4-flash",
        context_window=1000000,
        max_output_tokens=384000,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.14,  # $0.14/M input (cache miss), $0.28/M output
        description="DeepSeek V4-Flash — 1M contexte, 384K output, rapide et économique, remplace deepseek-chat",
        badge="Nouveau",
        capabilities=frozenset({"tool_calling", "cheap_text", "reasoning"}),
    ),
    "deepseek-v4-pro": ModelConfig(
        name="deepseek-v4-pro",
        display_name="DeepSeek V4-Pro (API)",
        provider=ProviderType.DEEPSEEK,
        model_id="deepseek-v4-pro",
        context_window=1000000,
        max_output_tokens=384000,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=1.74,  # $1.74/M input (cache miss), $3.48/M output
        description="DeepSeek V4-Pro — SOTA agentic coding, 1.6T params/49B actifs, 1M contexte, rival GPT-4o",
        badge="SOTA Code",
        capabilities=frozenset({"tool_calling", "reasoning"}),
    ),

    # === DEEPSEEK V3.2 — DÉPRÉCIÉ (inaccessible après le 24 juillet 2026 à 15h59 UTC) ===
    "deepseek-v3": ModelConfig(
        name="deepseek-v3",
        display_name="DeepSeek V3.2 ⚠️ Déprécié jul 2026 (API)",
        provider=ProviderType.DEEPSEEK,
        model_id="deepseek-chat",  # V3.2 non-thinking mode — inaccessible après jul 24, 2026 15:59 UTC
        context_window=128000,
        max_output_tokens=8192,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=0.27,  # $0.27/M input, $1.10/M output
        description="DeepSeek V3.2 — DÉPRÉCIÉ : inaccessible après le 24 juillet 2026 à 15h59 UTC. Migrer vers deepseek-v4-flash.",
        badge="Déprécié",
        capabilities=frozenset({"tool_calling", "cheap_text"}),
    ),
    "deepseek-reasoner": ModelConfig(
        name="deepseek-reasoner",
        display_name="DeepSeek Reasoner ⚠️ Déprécié jul 2026 (API)",
        provider=ProviderType.DEEPSEEK,
        model_id="deepseek-reasoner",  # V3.2 thinking mode — inaccessible après jul 24, 2026 15:59 UTC
        context_window=128000,
        max_output_tokens=65536,
        supports_vision=False,
        supports_tools=True,
        cost_per_million_tokens=2.19,  # $2.19/M input, $8.78/M output
        description="DeepSeek V3.2 Reasoner — DÉPRÉCIÉ : inaccessible après le 24 juillet 2026 à 15h59 UTC. Migrer vers deepseek-v4-pro.",
        capabilities=frozenset({"tool_calling"}),
    ),
}


def get_available_models() -> List[ModelConfig]:
    """Retourne la liste des modèles disponibles."""
    return list(AVAILABLE_MODELS.values())


def get_model_config(name: str) -> Optional[ModelConfig]:
    """Retourne la configuration d'un modèle par son nom."""
    return AVAILABLE_MODELS.get(name)


def get_default_model_for_provider(provider_name: str) -> Optional[ModelConfig]:
    """Retourne le premier modèle disponible pour un provider donné."""
    try:
        pt = ProviderType(provider_name)
    except ValueError:
        return None
    for m in AVAILABLE_MODELS.values():
        if m.provider == pt:
            return m
    return None


def get_local_models() -> List[ModelConfig]:
    """Retourne les modèles locaux (Ollama)."""
    return [m for m in AVAILABLE_MODELS.values() if m.is_local()]


def get_cloud_models() -> List[ModelConfig]:
    """Retourne les modèles cloud."""
    return [m for m in AVAILABLE_MODELS.values() if not m.is_local()]


def get_free_models() -> List[ModelConfig]:
    """Retourne les modèles gratuits."""
    return [m for m in AVAILABLE_MODELS.values() if m.is_free()]


# P5.1 — Modèles locaux validés par catégorie (utilisé par cu_router en mode local)
LOCAL_VALIDATED_MODELS: Dict[str, List[str]] = {
    "text": ["qwen3-8b", "qwen2.5-coder-14b", "deepseek-r1-7b", "lumena-v1"],
    "vision": ["minicpm-v", "llava-llama3", "gemma3", "gemma4", "bakllava", "moondream"],
    "code": ["qwen2.5-coder-14b", "deepseek-r1-7b"],
}


_PROVIDER_DISPLAY_NAMES: Dict[ProviderType, str] = {
    ProviderType.OLLAMA:    "Local (Ollama)",
    ProviderType.OPENAI:    "OpenAI",
    ProviderType.ANTHROPIC: "Anthropic",
    ProviderType.GOOGLE:    "Google",
    ProviderType.MOONSHOT:  "Moonshot",
    ProviderType.DEEPSEEK:  "DeepSeek",
    ProviderType.XAI:       "xAI",
    ProviderType.NVIDIA:    "NVIDIA NIM",
    ProviderType.MINIMAX:   "MiniMax",
}


def _cost_label(m: ModelConfig) -> str:
    """Retourne une étiquette de coût lisible pour affichage UI."""
    if m.is_local():
        return "Gratuit illimité"
    if m.cost_per_million_tokens == 0.0:
        return "Gratuit"
    if m.cost_per_million_tokens <= 0.9:
        return "Payant (pas cher)"
    if m.cost_per_million_tokens <= 3.0:
        return "Payant (très abordable)"
    return "Payant"


def build_models_info() -> Dict[str, Dict[str, Any]]:
    """Construit le dict models_info pour le wizard setup — source unique.

    Format: {model_key: {provider, cost, desc, badge?}}
    """
    result: Dict[str, Dict[str, Any]] = {}
    for key, m in AVAILABLE_MODELS.items():
        entry: Dict[str, Any] = {
            "provider": _PROVIDER_DISPLAY_NAMES.get(m.provider, m.provider.value),
            "cost":     _cost_label(m),
            "desc":     m.description,
        }
        if m.badge:
            entry["badge"] = m.badge
        result[key] = entry
    return result


# ─── Catalogue Ollama complet (du plus petit au plus grand) ────────────────
# Référence pour le wizard — ne déclare PAS ces modèles dans AVAILABLE_MODELS
# car ils sont détectés dynamiquement. Ce catalogue sert juste d'info UI.
OLLAMA_CATALOG: list[Dict[str, Any]] = [
    # ── Tiny (< 2 GB VRAM) ──
    {"id": "qwen3:0.6b",    "params": "0.6B",  "size": "~0.5 GB", "vram": "~1 GB",  "category": "llm",    "desc": "Ultra-léger, CPU suffisant"},
    {"id": "qwen3:1.7b",    "params": "1.7B",  "size": "~1.1 GB", "vram": "~2 GB",  "category": "llm",    "desc": "Très léger, fonctionne sur CPU"},
    {"id": "qwen3:4b",      "params": "4B",    "size": "~2.5 GB", "vram": "~3 GB",  "category": "llm",    "desc": "Bon compromis taille/qualité"},
    {"id": "gemma3:1b",     "params": "1B",    "size": "~0.8 GB", "vram": "~1.5 GB", "category": "llm",   "desc": "Google Gemma 3 1B — très léger"},
    {"id": "gemma3:4b",     "params": "4B",    "size": "~3.0 GB", "vram": "~3.5 GB", "category": "llm",   "desc": "Google Gemma 3 4B — équilibré"},
    {"id": "phi4-mini",     "params": "3.8B",  "size": "~2.5 GB", "vram": "~3 GB",  "category": "llm",    "desc": "Microsoft Phi-4 Mini — rapide et capable"},
    {"id": "gemma4:e2b",    "params": "2.3B",  "size": "~7.2 GB", "vram": "~8 GB",  "category": "vision", "desc": "Google Gemma 4 E2B — vision+audio, 128K ctx"},
    # ── Small (2-5 GB VRAM) ──
    {"id": "llama3.2:3b",   "params": "3B",    "size": "~2.0 GB", "vram": "~3 GB",  "category": "llm",    "desc": "Meta Llama 3.2 3B — polyvalent"},
    {"id": "deepseek-r1:7b", "params": "7B",   "size": "~4.7 GB", "vram": "~5 GB",  "category": "llm",    "desc": "Raisonnement avancé (distillé)"},
    {"id": "qwen3:8b",      "params": "8B",    "size": "~5.2 GB", "vram": "~6 GB",  "category": "llm",    "desc": "Bon équilibre vitesse/qualité"},
    {"id": "gemma4:e4b",    "params": "4.5B",  "size": "~9.6 GB", "vram": "~10 GB", "category": "vision", "desc": "Google Gemma 4 E4B — vision+audio, 128K ctx, frontière edge"},
    {"id": "gemma3:12b",    "params": "12B",   "size": "~8.1 GB", "vram": "~9 GB",  "category": "llm",    "desc": "Google Gemma 3 12B — très capable"},
    {"id": "llama3.3:8b",   "params": "8B",    "size": "~4.9 GB", "vram": "~6 GB",  "category": "llm",    "desc": "Meta Llama 3.3 8B — dernière version"},
    {"id": "mistral:7b",    "params": "7B",    "size": "~4.1 GB", "vram": "~5 GB",  "category": "llm",    "desc": "Mistral 7B — rapide et fiable"},
    # ── Medium (6-12 GB VRAM) ──
    {"id": "qwen2.5-coder:14b","params": "14B","size": "~9.0 GB", "vram": "~10 GB", "category": "code",   "desc": "Spécialisé code — excellent"},
    {"id": "qwen3:14b",     "params": "14B",   "size": "~9.0 GB", "vram": "~10 GB", "category": "llm",    "desc": "Très performant, GPU 12 GB recommandé"},
    {"id": "deepseek-r1:14b","params": "14B",  "size": "~9.0 GB", "vram": "~10 GB", "category": "llm",    "desc": "Raisonnement avancé 14B"},
    {"id": "gemma4:26b",    "params": "26B",   "size": "~18 GB",  "vram": "~20 GB", "category": "vision", "desc": "Google Gemma 4 26B MoE (4B actifs) — vision, 256K ctx, reasoning"},
    {"id": "gemma3:27b",    "params": "27B",   "size": "~17 GB",  "vram": "~18 GB", "category": "llm",    "desc": "Google Gemma 3 27B — haute qualité"},
    {"id": "mistral-small", "params": "24B",   "size": "~15 GB",  "vram": "~16 GB", "category": "llm",    "desc": "Mistral Small 24B — excellent rapport qualité/taille"},
    {"id": "codestral",     "params": "22B",   "size": "~13 GB",  "vram": "~14 GB", "category": "code",   "desc": "Mistral Codestral — code spécialisé"},
    # ── Large (16-24 GB VRAM) ──
    {"id": "qwen3:32b",     "params": "32B",   "size": "~20 GB",  "vram": "~22 GB", "category": "llm",    "desc": "Très puissant, GPU 24 GB requis"},
    {"id": "deepseek-r1:32b","params": "32B",  "size": "~20 GB",  "vram": "~22 GB", "category": "llm",    "desc": "Raisonnement expert 32B"},
    {"id": "qwen2.5-coder:32b","params": "32B","size": "~20 GB",  "vram": "~22 GB", "category": "code",   "desc": "Code expert 32B — rival Copilot"},
    {"id": "gemma4:31b",    "params": "31B",   "size": "~20 GB",  "vram": "~22 GB", "category": "vision", "desc": "Google Gemma 4 31B Dense — vision, 256K ctx, frontier local"},
    {"id": "command-r:35b", "params": "35B",   "size": "~20 GB",  "vram": "~22 GB", "category": "llm",    "desc": "Cohere Command R — RAG optimisé"},
    # ── XL (40+ GB VRAM) ──
    {"id": "llama3.3:70b",  "params": "70B",   "size": "~40 GB",  "vram": "~44 GB", "category": "llm",    "desc": "Meta Llama 3.3 70B — proche GPT-4"},
    {"id": "qwen3:235b",    "params": "235B",  "size": "~142 GB", "vram": "~150 GB","category": "llm",    "desc": "Qwen 3 235B — qualité maximale, multi-GPU"},
    {"id": "deepseek-r1:671b","params":"671B",  "size": "~400 GB", "vram": "~420 GB","category": "llm",    "desc": "DeepSeek R1 complet — machines serveur uniquement"},
    # ── Vision ──
    {"id": "minicpm-v",     "params": "8B",    "size": "~5.5 GB", "vram": "~6 GB",  "category": "vision", "desc": "Analyse d'images et screenshots"},
    {"id": "llava:7b",      "params": "7B",    "size": "~4.7 GB", "vram": "~5 GB",  "category": "vision", "desc": "LLaVA — description d'images"},
    {"id": "llava:13b",     "params": "13B",   "size": "~8.0 GB", "vram": "~9 GB",  "category": "vision", "desc": "LLaVA 13B — vision plus précise"},
    # ── Embedding ──
    {"id": "nomic-embed-text","params": "137M", "size": "~274 MB", "vram": "~0.5 GB","category": "embedding","desc": "Embeddings texte — RAG et recherche"},
    # ── Lumena fine-tuné ──
    {"id": "lumena-v1",     "params": "8B",    "size": "~5.2 GB", "vram": "~6 GB",  "category": "llm",    "desc": "Lumena v1 — QLoRA fine-tuné sur Qwen3-8B"},
]

# Index rapide du catalogue par id (ex: "gemma4:26b" → entry dict)
_CATALOG_BY_ID: Dict[str, Dict[str, Any]] = {m["id"]: m for m in OLLAMA_CATALOG}

# ── Catégories connues pour supports_vision ──
_VISION_CATEGORIES = {"vision"}
_CODE_CATEGORIES = {"code"}


def _ollama_key(model_id: str) -> str:
    """Convertit un model_id Ollama (ex: 'qwen3:8b') en clé AVAILABLE_MODELS (ex: 'qwen3-8b')."""
    return model_id.replace(":", "-").replace("/", "-")


def register_ollama_models(installed_ids: list[str]) -> int:
    """Enregistre dynamiquement les modèles Ollama installés dans AVAILABLE_MODELS."""
    return _register_ollama_models_impl(installed_ids)


def _ollama_host() -> str:
    """Retourne l'URL de base du démon Ollama (sans slash final)."""
    return os.environ.get(
        "LUMENA_OLLAMA_HOST",
        os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    ).rstrip("/")


# Cache probe persistent — évite d'appeler /api/show à chaque boot.
_PROBE_CACHE_PATH = "data/model_registry_cache.json"
_PROBE_CACHE_TTL = int(os.environ.get("LUMENA_OLLAMA_PROBE_TTL", "86400") or "86400")


def _load_probe_cache() -> Dict[str, Dict[str, Any]]:
    """Charge le cache JSON {model_id: {ts, ctx, caps, family, params_b}}."""
    try:
        import json, time
        from pathlib import Path
        p = Path(_PROBE_CACHE_PATH)
        if not p.is_file():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        # Purge des entrées expirées (évite cache empoisonné)
        now = int(time.time())
        return {
            mid: info for mid, info in data.items()
            if isinstance(info, dict) and (now - int(info.get("ts", 0))) < _PROBE_CACHE_TTL
        }
    except Exception:
        return {}


def _save_probe_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    try:
        import json
        from pathlib import Path
        p = Path(_PROBE_CACHE_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("[ollama.probe] cache save échoué: {}", exc)


def _probe_ollama_model(model_id: str, *, timeout: float = 4.0) -> Dict[str, Any]:
    """Interroge ``/api/show`` pour récupérer context_length réel + capabilities.

    Retourne un dict {ctx, caps, family, params_b, ok} avec :
        - ctx : int (context_length détecté, 0 si inconnu)
        - caps : list[str] (ex ["completion","tools","vision"])
        - family : str (architecture ex "qwen2","llama","phi3"…)
        - params_b : float | None (taille en milliards, parsée si dispo)
        - ok : bool (True si l'appel a réussi)
    """
    import httpx
    info: Dict[str, Any] = {"ctx": 0, "caps": [], "family": "", "params_b": None, "ok": False}
    try:
        resp = httpx.post(
            f"{_ollama_host()}/api/show",
            json={"name": model_id},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return info
        data = resp.json() or {}
    except Exception as exc:
        logger.debug("[ollama.probe] /api/show {} KO : {}", model_id, exc)
        return info

    info["ok"] = True
    caps = data.get("capabilities") or []
    if isinstance(caps, list):
        info["caps"] = [str(c).lower() for c in caps if c]

    mi = data.get("model_info") or {}
    if isinstance(mi, dict):
        family = str(mi.get("general.architecture") or "").lower()
        info["family"] = family
        # Clé dynamique "<family>.context_length"
        for k, v in mi.items():
            if not isinstance(k, str):
                continue
            if k.endswith(".context_length") and isinstance(v, (int, float)):
                info["ctx"] = max(info["ctx"], int(v))
        # Taille paramètres en milliards
        pc = mi.get("general.parameter_count")
        if isinstance(pc, (int, float)) and pc > 0:
            info["params_b"] = round(float(pc) / 1e9, 2)

    # Fallback ctx via champ "parameters" (ancien format)
    if info["ctx"] == 0:
        params_str = str(data.get("parameters") or "")
        import re
        m = re.search(r"num_ctx\s+(\d+)", params_str)
        if m:
            info["ctx"] = int(m.group(1))

    return info


def _params_b_from_tag(model_id: str) -> float | None:
    """Extrait la taille en milliards depuis un tag style ``qwen3:14b`` → 14.0."""
    import re
    m = re.search(r":(\d+(?:\.\d+)?)b\b", model_id.lower())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    # Format sans ":" (ex "phi4-mini", "codestral") → inconnu
    return None


def _category_from_name(model_id: str, caps: list[str]) -> str:
    """Déduit la catégorie (llm/code/vision/embedding/reasoning) d'après le nom + caps."""
    mid = model_id.lower()
    if "vision" in caps:
        return "vision"
    # Embedding
    if any(t in mid for t in ("embed", "nomic-embed", "mxbai-embed", "bge-")):
        return "embedding"
    # Vision
    if any(t in mid for t in ("llava", "minicpm-v", "moondream", "bakllava", "-vl", "vision", "gemma4")):
        return "vision"
    # Code
    if any(t in mid for t in ("coder", "code-", "codegemma", "codestral", "codellama", "starcoder", "deepseek-coder")):
        return "code"
    # Reasoning (signals via nom)
    if any(t in mid for t in ("thinking", "reasoner", "r1", "qwq", "o1-")):
        return "reasoning"
    return "llm"


def _infer_skills_for_custom(model_id: str, params_b: float | None, category: str) -> Dict[str, int]:
    """Génère un entry MODEL_SKILLS pour un modèle Ollama custom.

    Scoring basé sur la taille (params_b) + bonus par catégorie. Conservateur
    (local < cloud en règle générale).
    """
    # Base par taille
    if params_b is None:
        code, speed, reasoning, creative, research = 55, 75, 55, 52, 48
    elif params_b < 3:
        code, speed, reasoning, creative, research = 40, 92, 32, 38, 30
    elif params_b < 8:
        code, speed, reasoning, creative, research = 55, 80, 55, 50, 45
    elif params_b < 14:
        code, speed, reasoning, creative, research = 65, 72, 62, 55, 52
    elif params_b < 32:
        code, speed, reasoning, creative, research = 74, 58, 70, 62, 60
    elif params_b < 70:
        code, speed, reasoning, creative, research = 82, 45, 80, 72, 70
    else:
        code, speed, reasoning, creative, research = 88, 32, 86, 78, 78

    vision = 0
    web = max(20, research - 15)  # local → pas d'accès web natif

    # Bonus par catégorie
    if category == "code":
        code = min(92, code + 12)
        reasoning = min(88, reasoning + 3)
    elif category == "vision":
        vision = 65 if params_b and params_b < 10 else 75
        creative = min(85, creative + 5)
    elif category == "reasoning":
        reasoning = min(90, reasoning + 12)
        speed = max(20, speed - 12)
    elif category == "embedding":
        code = speed = reasoning = creative = research = web = 0  # jamais routé

    return {
        "code": code, "speed": speed, "reasoning": reasoning,
        "creative": creative, "research": research, "vision": vision, "web": web,
    }


def _register_ollama_models_impl(installed_ids: list[str]) -> int:
    """Implémentation — enregistre dynamiquement les modèles Ollama.

    Stratégie :
        1. Probe ``/api/show`` (cache TTL 24h) pour obtenir context_length réel
           + capabilities officielles (``completion``, ``tools``, ``vision``).
        2. Fallback sur le catalogue statique ``OLLAMA_CATALOG``.
        3. Fallback sur heuristique nom-based (catégorie + taille).
        4. Auto-insertion dans ``MODEL_SKILLS`` pour que le router puisse
           sélectionner le modèle custom.

    Args:
        installed_ids: liste de model_id retournés par Ollama /api/tags.

    Returns:
        Nombre de nouveaux modèles enregistrés.
    """
    probe_enabled = os.environ.get("LUMENA_OLLAMA_PROBE", "1").strip() in ("1", "true", "yes", "on")
    cache = _load_probe_cache() if probe_enabled else {}
    cache_dirty = False
    registered = 0
    # Fast-fail : si le daemon est injoignable au premier probe, on désactive
    # les probes suivants pour éviter N × timeouts au boot quand Ollama est down.
    daemon_dead = False

    for mid in installed_ids:
        key = _ollama_key(mid)
        if key in AVAILABLE_MODELS:
            continue  # déjà déclaré manuellement

        cat_entry = _CATALOG_BY_ID.get(mid, {})
        catalog_category = cat_entry.get("category", "")
        catalog_ctx = 0
        catalog_params = cat_entry.get("params", "")
        desc = cat_entry.get("desc", mid)

        # Heuristiques connues (legacy — conservées pour fallback)
        if "gemma4" in mid and any(s in mid for s in ("26b", "31b")):
            catalog_ctx = 262144
        elif "gemma4" in mid:
            catalog_ctx = 131072
        elif "qwen" in mid and any(s in catalog_params for s in ("14B", "32B", "235B")):
            catalog_ctx = 131072
        elif "llama3.3:70b" in mid:
            catalog_ctx = 131072

        # 1. Probe (si activé & daemon vivant)
        probe: Dict[str, Any] = {}
        if probe_enabled and not daemon_dead:
            probe = cache.get(mid) or {}
            if not probe:
                probe = _probe_ollama_model(mid, timeout=2.5)
                if probe.get("ok"):
                    import time
                    cache[mid] = {**probe, "ts": int(time.time())}
                    cache_dirty = True
                elif not registered:
                    # Premier échec + rien d'enregistré encore → daemon probablement down
                    daemon_dead = True
                    logger.debug("[ollama.probe] daemon injoignable, fallback heuristique pour tous")

        # 2. Context window : probe > catalogue > defaut
        ctx = int(probe.get("ctx") or 0) or catalog_ctx or 32768

        # 3. Capabilities
        probe_caps = probe.get("caps") or []
        category = catalog_category or _category_from_name(mid, probe_caps)
        is_vision = (category == "vision") or ("vision" in probe_caps)
        is_code = category == "code"
        is_embedding = category == "embedding"
        supports_tools = True
        if probe_caps and "tools" not in probe_caps and "completion" in probe_caps:
            # Modèle text-only strict (rare mais possible)
            supports_tools = False

        # 4. Taille paramètres (pour scoring skills)
        params_b = probe.get("params_b") or _params_b_from_tag(mid)

        capabilities = {"tool_calling", "cheap_text"} if supports_tools else {"cheap_text"}
        if is_vision:
            capabilities.add("vision_describe")
        if is_code:
            capabilities.add("code")
        if is_embedding:
            capabilities = {"embedding"}
            supports_tools = False

        # max_output_tokens : min(ctx/4, 16k) mais au moins 2k pour les gros ctx
        max_out = max(2048, min(ctx // 4, 16384)) if not is_embedding else 512

        AVAILABLE_MODELS[key] = ModelConfig(
            name=key,
            display_name=f"{mid} (Local Ollama)",
            provider=ProviderType.OLLAMA,
            model_id=mid,
            context_window=ctx,
            max_output_tokens=max_out,
            supports_vision=is_vision,
            supports_tools=supports_tools,
            cost_per_million_tokens=0.0,
            description=desc,
            capabilities=frozenset(capabilities),
        )

        # 5. Auto-scoring MODEL_SKILLS si custom (hors catalogue + pas déjà scoré)
        if key not in MODEL_SKILLS and not is_embedding:
            MODEL_SKILLS[key] = _infer_skills_for_custom(mid, params_b, category)
            logger.debug(
                "[ollama.auto] {} → skills auto (params={}B, cat={}, ctx={})",
                key, params_b, category, ctx,
            )

        registered += 1
        logger.info(
            "🔄 Ollama auto-config: {} (ctx={}k, cat={}, vision={}, tools={}{})",
            key, ctx // 1024, category, is_vision, supports_tools,
            ", probe=ok" if probe.get("ok") else ", probe=fallback",
        )

    if cache_dirty:
        _save_probe_cache(cache)

    return registered


def sync_ollama_models() -> int:
    """Scanne les modèles Ollama installés et les enregistre.

    Returns: nombre de nouveaux modèles enregistrés, ou 0 si Ollama inaccessible.
    """
    import httpx

    try:
        resp = httpx.get(f"{_ollama_host()}/api/tags", timeout=5)
        if resp.status_code != 200:
            return 0
        data = resp.json()
        installed = []
        for m in data.get("models", []):
            name = m.get("name", "")
            # Normaliser: "gemma4:26b-a4b-it-q4_K_M" → "gemma4:26b" (garder tag court si dans catalogue)
            base = name.split(":")[0]
            tag = name.split(":")[-1] if ":" in name else "latest"
            short = f"{base}:{tag}" if tag != "latest" else base
            # Chercher la meilleure correspondance dans le catalogue
            if short in _CATALOG_BY_ID:
                installed.append(short)
            elif name in _CATALOG_BY_ID:
                installed.append(name)
            else:
                installed.append(name)  # modèle custom hors catalogue
        count = register_ollama_models(installed)
        if count:
            logger.info(f"🔄 {count} modèle(s) Ollama enregistré(s) dynamiquement")
        return count
    except Exception:
        return 0


# ─── Model Skills Router ──────────────────────────────────────────────────
#
# Scores basés sur les benchmarks publics (mars 2026) :
#   code     → SWE-bench Verified 2024-2025, HumanEval, MBPP
#   speed    → tokens/s + latence relative (100 = le plus rapide possible)
#   reasoning→ GPQA Diamond, MATH Level 5, ARC-AGI 2024
#   creative → MT-Bench creative turns, Chatbot Arena ELO créativité
#   research → LongBench v2, FRAMES, RAG-bench, utilisation contexte long
#
# Sources : LiveBench Mar 2026, Scale AI leaderboard, Anthropic/xAI/Google/DS fiches produits.
#
MODEL_SKILLS: Dict[str, Dict[str, int]] = {
    # Scores: code, speed, reasoning, creative, research,
    #         vision (analyse image via API), web (recherche/analyse web)
    # vision=0 => le modèle n'a PAS d'API vision
    # ── Anthropic ──────────────────────────────────────────────────────────
    "claude-opus-4.7":             {"code": 92, "speed": 45, "reasoning": 96, "creative": 97, "research": 94, "vision": 96, "web": 91},
    "claude-opus-4.6":             {"code": 90, "speed": 45, "reasoning": 94, "creative": 96, "research": 92, "vision": 95, "web": 90},
    "claude-opus-4.5":             {"code": 88, "speed": 50, "reasoning": 91, "creative": 93, "research": 89, "vision": 92, "web": 87},
    "claude-opus-4":               {"code": 87, "speed": 52, "reasoning": 90, "creative": 92, "research": 87, "vision": 90, "web": 85},
    "claude-sonnet-4.6":           {"code": 89, "speed": 68, "reasoning": 88, "creative": 95, "research": 87, "vision": 93, "web": 92},
    "claude-sonnet-4.5":           {"code": 85, "speed": 72, "reasoning": 86, "creative": 90, "research": 82, "vision": 88, "web": 88},
    "claude-sonnet-4":             {"code": 84, "speed": 75, "reasoning": 84, "creative": 88, "research": 80, "vision": 85, "web": 83},
    "claude-haiku-4.5":            {"code": 74, "speed": 94, "reasoning": 72, "creative": 78, "research": 68, "vision": 72, "web": 70},
    "claude-3-7-sonnet":           {"code": 84, "speed": 65, "reasoning": 89, "creative": 88, "research": 85, "vision": 85, "web": 84},
    "claude-3-5-sonnet":           {"code": 82, "speed": 70, "reasoning": 84, "creative": 86, "research": 80, "vision": 82, "web": 80},
    "claude-3-5-haiku":            {"code": 68, "speed": 96, "reasoning": 66, "creative": 72, "research": 62, "vision": 68, "web": 65},
    # ── xAI / Grok ─────────────────────────────────────────────────────────
    "grok-code-fast-1":            {"code": 90, "speed": 95, "reasoning": 55, "creative": 40, "research": 52, "vision": 30, "web": 60},
    "grok-4-1-fast-reasoning":     {"code": 75, "speed": 80, "reasoning": 90, "creative": 60, "research": 78, "vision": 75, "web": 82},
    "grok-4-1-fast-non-reasoning": {"code": 72, "speed": 90, "reasoning": 65, "creative": 58, "research": 70, "vision": 72, "web": 78},
    "grok-4.20-0309-reasoning":    {"code": 80, "speed": 72, "reasoning": 93, "creative": 65, "research": 84, "vision": 85, "web": 88},
    "grok-4.20-0309-non-reasoning":{"code": 77, "speed": 88, "reasoning": 70, "creative": 62, "research": 76, "vision": 82, "web": 85},
    "grok-4.20-multi-agent-0309":  {"code": 82, "speed": 70, "reasoning": 88, "creative": 68, "research": 82, "vision": 80, "web": 86},
    # ── DeepSeek ───────────────────────────────────────────────────────────
    "deepseek-v4-pro":             {"code": 95, "speed": 52, "reasoning": 95, "creative": 76, "research": 92, "vision":  0, "web": 80},
    "deepseek-v4-flash":           {"code": 88, "speed": 80, "reasoning": 88, "creative": 72, "research": 82, "vision":  0, "web": 76},
    "deepseek-reasoner":           {"code": 82, "speed": 50, "reasoning": 92, "creative": 60, "research": 80, "vision":  0, "web": 72},
    "deepseek-v3":                 {"code": 84, "speed": 72, "reasoning": 82, "creative": 68, "research": 78, "vision":  0, "web": 75},
    # ── Google ─────────────────────────────────────────────────────────────
    "gemini-3.1-pro":              {"code": 86, "speed": 70, "reasoning": 90, "creative": 85, "research": 92, "vision": 95, "web": 92},
    "gemini-2.5-pro":              {"code": 84, "speed": 65, "reasoning": 88, "creative": 78, "research": 88, "vision": 92, "web": 88},
    "gemini-2.5-flash":            {"code": 78, "speed": 94, "reasoning": 74, "creative": 74, "research": 86, "vision": 88, "web": 89},
    # ── Moonshot (Kimi) ────────────────────────────────────────────────────
    "kimi-k2.5":                   {"code": 74, "speed": 75, "reasoning": 78, "creative": 72, "research": 90, "vision":  0, "web": 82},
    # ── NVIDIA NIM ─────────────────────────────────────────────────────────
    "nvidia-kimi-k2-instruct":     {"code": 80, "speed": 78, "reasoning": 76, "creative": 68, "research": 75, "vision":  0, "web": 74},
    "nvidia-kimi-k2-instruct-0905":{"code": 78, "speed": 76, "reasoning": 80, "creative": 70, "research": 82, "vision":  0, "web": 76},
    "nvidia-kimi-k2-thinking":     {"code": 76, "speed": 55, "reasoning": 86, "creative": 65, "research": 80, "vision":  0, "web": 73},
    "nvidia-deepseek-v3.2":        {"code": 86, "speed": 68, "reasoning": 88, "creative": 70, "research": 82, "vision":  0, "web": 75},
    "nvidia-deepseek-v3.1":        {"code": 82, "speed": 75, "reasoning": 84, "creative": 66, "research": 78, "vision":  0, "web": 72},
    "nvidia-glm-4.7":              {"code": 87, "speed": 82, "reasoning": 83, "creative": 82, "research": 78, "vision": 55, "web": 70},
    "nvidia-minimax-m2.5":         {"code": 85, "speed": 78, "reasoning": 80, "creative": 80, "research": 76, "vision": 52, "web": 68},
    # ── MiniMax (natif) ────────────────────────────────────────────────────
    "minimax-m2.5":                {"code": 86, "speed": 80, "reasoning": 82, "creative": 82, "research": 78, "vision":  0, "web": 70},
    "minimax-m2.5-highspeed":      {"code": 84, "speed": 95, "reasoning": 78, "creative": 78, "research": 74, "vision":  0, "web": 66},
    "minimax-m2.1":                {"code": 80, "speed": 78, "reasoning": 76, "creative": 76, "research": 72, "vision":  0, "web": 65},
    "minimax-m2.1-highspeed":      {"code": 78, "speed": 94, "reasoning": 72, "creative": 72, "research": 68, "vision":  0, "web": 62},
    "minimax-m2.7":                {"code": 88, "speed": 76, "reasoning": 85, "creative": 84, "research": 80, "vision":  0, "web": 72},
    # ── Z.AI (GLM) ────────────────────────────────────────────────────────
    "glm-5.1":                     {"code": 85, "speed": 65, "reasoning": 88, "creative": 80, "research": 82, "vision":  0, "web": 78},
    "glm-4.7-flashx":              {"code": 80, "speed": 88, "reasoning": 76, "creative": 74, "research": 72, "vision":  0, "web": 70},
    "glm-4.7-flash":               {"code": 76, "speed": 92, "reasoning": 70, "creative": 70, "research": 68, "vision":  0, "web": 66},
    "glm-4.5-flash":               {"code": 70, "speed": 95, "reasoning": 64, "creative": 64, "research": 62, "vision":  0, "web": 60},
    "glm-4.6v-flash":              {"code": 60, "speed": 90, "reasoning": 62, "creative": 65, "research": 60, "vision": 72, "web": 58},
    "glm-5v-turbo":                {"code": 72, "speed": 68, "reasoning": 74, "creative": 76, "research": 70, "vision": 85, "web": 68},
    # ── OpenAI ─────────────────────────────────────────────────────────────
    "gpt-5.4":                     {"code": 92, "speed": 75, "reasoning": 93, "creative": 90, "research": 91, "vision": 96, "web": 93},
    "gpt-5.4-mini":                {"code": 86, "speed": 88, "reasoning": 85, "creative": 82, "research": 84, "vision": 88, "web": 87},
    "gpt-5.4-nano":                {"code": 55, "speed": 94, "reasoning": 48, "creative": 45, "research": 42, "vision": 72, "web": 50},
    "gpt-4o":                      {"code": 82, "speed": 78, "reasoning": 85, "creative": 85, "research": 83, "vision": 92, "web": 88},
    "gpt-4o-mini":                 {"code": 65, "speed": 90, "reasoning": 58, "creative": 55, "research": 52, "vision": 75, "web": 62},
    "gpt-4.1":                      {"code": 88, "speed": 76, "reasoning": 86, "creative": 82, "research": 85, "vision": 90, "web": 88},
    "o3":                          {"code": 92, "speed": 35, "reasoning": 97, "creative": 72, "research": 90, "vision": 82, "web": 72},
    "o4-mini":                     {"code": 85, "speed": 65, "reasoning": 92, "creative": 62, "research": 82, "vision": 75, "web": 65},
    "gpt-5.3-codex":               {"code": 97, "speed": 55, "reasoning": 94, "creative": 70, "research": 88, "vision": 82, "web": 75},
    # ── Local (Ollama) ─────────────────────────────────────────────────────
    "qwen2.5-coder-14b":           {"code": 72, "speed": 60, "reasoning": 55, "creative": 45, "research": 45, "vision":  0, "web": 35},
    "qwen3-8b":                    {"code": 60, "speed": 75, "reasoning": 58, "creative": 55, "research": 50, "vision":  0, "web": 38},
    "deepseek-r1-7b":              {"code": 58, "speed": 70, "reasoning": 72, "creative": 45, "research": 50, "vision":  0, "web": 30},
    "lumena-v1":                   {"code": 55, "speed": 80, "reasoning": 50, "creative": 60, "research": 45, "vision":  0, "web": 25},
}


def best_model_for(
    domain: str,
    preferred_models: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Retourne le nom du meilleur modèle disponible pour un domaine.

    Règle "réserve Claude" : les modèles premium (claude-opus-4.7) ne sont
    sélectionnés que si leur score dépasse le meilleur modèle non-premium
    d'au moins _PREMIUM_THRESHOLD points. Autrement le modèle spécialisé
    moins cher gagne (grok-code pour code, deepseek-reasoner pour reasoning, etc.)

    Args:
        domain: "code" | "speed" | "reasoning" | "creative" | "research"
        preferred_models: restreindre la sélection (None = tous les modèles connus)

    Returns:
        Nom du modèle (clé AVAILABLE_MODELS) ou None si aucun disponible.
    """
    # Modèles réservés aux tâches vraiment complexes : sélectionnés seulement
    # si leur avantage de score > _PRE7", "claude-opus-4.MIUM_THRESHOLD sur le meilleur standard.
    _PREMIUM_MODELS = {"claude-opus-4.6", "claude-opus-4.5", "claude-sonnet-4.6", "claude-sonnet-4.5"}
    _PREMIUM_THRESHOLD = 10

    candidates = preferred_models or list(MODEL_SKILLS.keys())

    best_standard_name: Optional[str] = None
    best_standard_score = -1
    best_premium_name: Optional[str] = None
    best_premium_score = -1

    for model_name in candidates:
        if model_name not in MODEL_SKILLS:
            continue
        config = AVAILABLE_MODELS.get(model_name)
        if not config:
            continue
        available = config.is_local() or check_api_key(config.provider)
        if not available:
            continue
        score = MODEL_SKILLS[model_name].get(domain, 0)

        if model_name in _PREMIUM_MODELS:
            if score > best_premium_score:
                best_premium_score = score
                best_premium_name = model_name
        else:
            if score > best_standard_score:
                best_standard_score = score
                best_standard_name = model_name

    # Sélection finale : premium seulement si avantage significatif
    if best_premium_name and best_premium_score > best_standard_score + _PREMIUM_THRESHOLD:
        return best_premium_name

    return best_standard_name or best_premium_name  # fallback si aucun standard dispo


def _detect_ollama_vision_models() -> List[str]:
    """D\u00e9tecte les mod\u00e8les Ollama locaux avec support vision.

    Interroge GET /api/tags sur le serveur Ollama et croise avec
    les noms de familles vision connues.

    Returns:
        Liste de model_id Ollama vision disponibles (ex: ["llava:13b"]).
    """
    import time

    now = time.monotonic()
    cache = getattr(_detect_ollama_vision_models, "_cache", None)
    if cache and (now - cache[0]) < 120:
        return cache[1]

    ollama_host = os.getenv("LUMENA_OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    _VISION_FAMILIES = {
        "llava", "bakllava", "moondream", "minicpm-v", "llama3.2-vision",
        "llava-llama3", "llava-phi3", "obsidian", "granite3-vision",
        "granite3.1-vision", "qwen2-vl", "qwen2.5-vl",
    }
    result: List[str] = []
    try:
        import httpx
        resp = httpx.get(f"{ollama_host}/api/tags", timeout=3.0)
        if resp.status_code == 200:
            for m in resp.json().get("models", []):
                name: str = m.get("name", "")
                base = name.split(":")[0].lower()
                if base in _VISION_FAMILIES:
                    result.append(name)
    except Exception:
        pass

    _detect_ollama_vision_models._cache = (now, result)  # type: ignore[attr-defined]
    return result


def _resolve_vision_auto() -> Optional[str]:
    """R\u00e9sout le meilleur mod\u00e8le vision en mode auto, gratuit d'abord.

    Cascade :
    1. Ollama local (mod\u00e8les vision d\u00e9tect\u00e9s dynamiquement) \u2014 gratuit
    2. Mod\u00e8les cloud ultra-cheap (< $0.20/M) avec API key dispo
    3. Mod\u00e8les cloud cheap (< $1.00/M) avec API key dispo
    4. Mod\u00e8les cloud mid-tier (< $3.00/M) avec API key dispo
    5. Fallback: best_model_for("vision") classique (meilleur score absolu)

    \u00c0 chaque palier, on prend le mod\u00e8le avec le meilleur score vision.
    """
    # --- \u00c9tape 1 : Ollama local vision ---
    local_vision = _detect_ollama_vision_models()
    if local_vision:
        for name, cfg in AVAILABLE_MODELS.items():
            if cfg.provider == ProviderType.OLLAMA and cfg.supports_vision:
                return name

    # --- \u00c9tapes 2-4 : cloud par palier de co\u00fbt ---
    for max_cost in (0.20, 1.00, 3.00):
        best_name: Optional[str] = None
        best_score = -1
        for name, cfg in AVAILABLE_MODELS.items():
            if not cfg.supports_vision or cfg.is_local():
                continue
            if cfg.cost_per_million_tokens > max_cost:
                continue
            if not check_api_key(cfg.provider):
                continue
            score = MODEL_SKILLS.get(name, {}).get("vision", 0)
            if score > best_score:
                best_score = score
                best_name = name
        if best_name:
            logger.info(f"Vision auto: {best_name} (\u2264${max_cost}/M, score={best_score})")
            return best_name

    # --- \u00c9tape 5 : fallback classique ---
    return best_model_for("vision")


def get_brain_model(task: str) -> Optional[str]:
    """
    Retourne le mod\u00e8le optimal pour un type de t\u00e2che sp\u00e9cialis\u00e9.

    Lit d'abord la variable d'environnement LUMENA_BRAIN_{TASK.upper()},
    puis s\u00e9lectionne automatiquement via best_model_for(task).

    Pour vision en mode auto, utilise une cascade co\u00fbt-efficace
    (gratuit/local d'abord, puis cheap cloud, puis premium en fallback).

    Args:
        task: "vision" | "code" | "web" | "image_gen"

    Returns:
        Nom du mod\u00e8le (cl\u00e9 AVAILABLE_MODELS) ou None.
    """
    env_key = f"LUMENA_BRAIN_{task.upper()}"
    override = os.getenv(env_key, "auto").strip().lower()

    if override and override != "auto":
        config = AVAILABLE_MODELS.get(override)
        if config and check_api_key(config.provider):
            return override

    # Pour image_gen, priorit\u00e9 aux mod\u00e8les supports_image_generation
    if task == "image_gen":
        for name, cfg in AVAILABLE_MODELS.items():
            if getattr(cfg, "supports_image_generation", False) and check_api_key(cfg.provider):
                return name
        return None

    # Vision auto : cascade gratuit \u2192 cheap \u2192 premium
    if task == "vision":
        return _resolve_vision_auto()

    return best_model_for(task)


def models_with_capability(cap: str, *, available_only: bool = True) -> List[str]:
    """Retourne les modèles ayant la capacité donnée.

    Args:
        cap: ex. "vision_describe", "computer_use", "dom_assist"
        available_only: si True, filtre les modèles dont la clé API est absente.

    Returns:
        Liste de noms triés par score vision (desc) puis speed (desc).
    """
    result: List[str] = []
    for name, cfg in AVAILABLE_MODELS.items():
        if cap not in cfg.capabilities:
            continue
        if available_only and not (cfg.is_local() or check_api_key(cfg.provider)):
            continue
        result.append(name)
    # Tri par score vision desc, puis speed desc
    def _sort_key(n: str) -> tuple:
        sk = MODEL_SKILLS.get(n, {})
        return (-sk.get("vision", 0), -sk.get("speed", 0))
    result.sort(key=_sort_key)
    return result


def best_model_for_capability(cap: str) -> Optional[str]:
    """Retourne le meilleur modèle disponible pour cette capacité, ou None."""
    candidates = models_with_capability(cap, available_only=True)
    return candidates[0] if candidates else None


def check_api_key(provider: ProviderType) -> bool:
    """Vérifie si la clé API est configurée pour un provider."""
    env_vars = {
        ProviderType.OPENAI: "OPENAI_API_KEY",
        ProviderType.ANTHROPIC: "ANTHROPIC_API_KEY",
        ProviderType.GOOGLE: "GOOGLE_API_KEY",
        ProviderType.MOONSHOT: "MOONSHOT_API_KEY",
        ProviderType.DEEPSEEK: "DEEPSEEK_API_KEY",
        ProviderType.XAI: "XAI_API_KEY",
        ProviderType.NVIDIA: "NVIDIA_API_KEY",
        ProviderType.MINIMAX: "MINIMAX_API_KEY",
        ProviderType.ZAI: "ZAI_API_KEY",
        # Image generation providers
        ProviderType.STABILITY: "STABILITY_API_KEY",
        ProviderType.FLUX: "BFL_API_KEY",
        ProviderType.IDEOGRAM: "IDEOGRAM_API_KEY",
        ProviderType.RECRAFT: "RECRAFT_API_KEY",
        ProviderType.REPLICATE: "REPLICATE_API_TOKEN",
        ProviderType.HUGGINGFACE: "HUGGINGFACE_TOKEN",
    }
    
    if provider == ProviderType.OLLAMA:
        return True  # Pas de clé nécessaire
    
    env_var = env_vars.get(provider)
    if env_var:
        return bool(os.getenv(env_var))
    return False


def get_api_key(provider: ProviderType) -> Optional[str]:
    """Récupère la clé API pour un provider."""
    env_vars = {
        ProviderType.OPENAI: "OPENAI_API_KEY",
        ProviderType.ANTHROPIC: "ANTHROPIC_API_KEY",
        ProviderType.GOOGLE: "GOOGLE_API_KEY",
        ProviderType.MOONSHOT: "MOONSHOT_API_KEY",
        ProviderType.DEEPSEEK: "DEEPSEEK_API_KEY",
        ProviderType.XAI: "XAI_API_KEY",
        ProviderType.NVIDIA: "NVIDIA_API_KEY",
        ProviderType.MINIMAX: "MINIMAX_API_KEY",
        ProviderType.ZAI: "ZAI_API_KEY",
        # Image generation providers
        ProviderType.STABILITY: "STABILITY_API_KEY",
        ProviderType.FLUX: "BFL_API_KEY",
        ProviderType.IDEOGRAM: "IDEOGRAM_API_KEY",
        ProviderType.RECRAFT: "RECRAFT_API_KEY",
        ProviderType.REPLICATE: "REPLICATE_API_TOKEN",
        ProviderType.HUGGINGFACE: "HUGGINGFACE_TOKEN",
    }
    
    env_var = env_vars.get(provider)
    if env_var:
        return os.getenv(env_var)
    return None
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
