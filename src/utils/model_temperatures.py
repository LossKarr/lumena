"""P8.MODEL_TEMPERATURES — température optimale par provider/modèle.

Certains modèles performent mieux avec des températures spécifiques :
- DeepSeek : très basse (0.0-0.1) pour suivre les instructions strictes
- Claude : 0.1-0.2 (stable mais créatif si besoin)
- GPT-4/5 : 0.1
- Gemini : 0.15
- o1/o3/o4 reasoning : 1.0 (forced par l'API)

Gardé par flag LUMENA_MODEL_TEMPERATURES.
"""
from __future__ import annotations


# Température par préfixe modèle (ordre = priorité)
_TEMP_BY_MODEL: tuple[tuple[str, float], ...] = (
    ("deepseek-reasoner", 0.0),
    ("deepseek", 0.05),
    ("claude-opus", 0.1),
    ("claude-sonnet", 0.1),
    ("claude", 0.15),
    ("gpt-5", 0.1),
    ("gpt-4", 0.1),
    ("o1", 1.0),
    ("o3", 1.0),
    ("o4", 1.0),
    ("gemini", 0.15),
    ("kimi", 0.1),
    ("minimax", 0.1),
    ("llama", 0.15),
    ("qwen", 0.15),
)


def get_model_temperature(model_name: str, fallback: float = 0.15) -> float:
    """Retourne la température recommandée pour un modèle, ou fallback.

    Si flag MODEL_TEMPERATURES off → renvoie fallback immédiatement.
    """
    try:
        from src.config.codeagent_flags import MODEL_TEMPERATURES
        if not MODEL_TEMPERATURES:
            return fallback
    except Exception:
        return fallback

    if not model_name:
        return fallback
    lc = str(model_name).lower()
    for prefix, temp in _TEMP_BY_MODEL:
        if prefix in lc:
            return temp
    return fallback


__all__ = ["get_model_temperature"]
