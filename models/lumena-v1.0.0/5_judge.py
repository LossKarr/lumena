"""
Lumena — Juge de Qualité des Conversations
===========================================
Évalue chaque conversation du pool d'entraînement avec DeepSeek V3.
Score de 0 à 10 sur 5 critères. Seuil ≥ 7 pour entrer dans le dataset.

Usage :
    python 5_judge.py
    python 5_judge.py --threshold 6
    python 5_judge.py --dry-run
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = Path(__file__).parent.parent.parent / "data"
POOL_DIR = DATA_DIR / "training_pool"
VALIDATED_DIR = DATA_DIR / "training_validated"
VALIDATED_DIR.mkdir(parents=True, exist_ok=True)


def _read_float_env(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


DEFAULT_THRESHOLD = _read_float_env("LUMENA_JUDGE_THRESHOLD", 6.5)

JUDGE_PROMPT = """Tu es un évaluateur expert de réponses IA. Évalue cette conversation sur 5 critères (0-10 chacun).

CONVERSATION À ÉVALUER :
Utilisateur : {user}

Assistant (Lumena) : {response}{react_context}

CRITÈRES :
1. Précision : La réponse est-elle factuelle et correcte ?
2. Personnalité : La réponse reflète-elle la personnalité de Lumena (naturelle, directe, légèrement espiègle) ?
3. Utilité : La réponse aide-t-elle vraiment l'utilisateur ?
4. Clarté : La réponse est-elle claire et bien structurée ?
5. Français : La qualité du français est-elle bonne et naturelle ?

Note : si le contexte d'exécution indique un avertissement (boucle, timeout, tâches incomplètes),
prends-le en compte dans ta note de précision et d'utilité.

Réponds UNIQUEMENT avec ce JSON (rien d'autre) :
{{"precision": X, "personnalite": X, "utilite": X, "clarte": X, "francais": X, "total": X, "commentaire": "..."}}

où total = moyenne des 5 scores."""


def _load_env_var(name: str, default: str = "") -> str:
    """Charge une variable depuis .env ou l'environnement."""
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return os.getenv(name, default)


def load_env_key() -> str:
    """Charge la clé API pour le judge (LUMENA_JUDGE_API_KEY ou DEEPSEEK_API_KEY)."""
    # Priorité : LUMENA_JUDGE_API_KEY → DEEPSEEK_API_KEY
    key = _load_env_var("LUMENA_JUDGE_API_KEY")
    if not key:
        key = _load_env_var("DEEPSEEK_API_KEY")
    return key


# Mapping modèle Lumena → (env_key, api_url, model_id_override)
# Permet de déduire automatiquement la clé et l'URL depuis le nom du modèle
_JUDGE_PROVIDER_MAP = {
    # DeepSeek
    "deepseek-chat": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/chat/completions", "deepseek-chat"),
    "deepseek-v3": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/chat/completions", "deepseek-chat"),
    "deepseek-v4-flash": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/chat/completions", "deepseek-v4-flash"),
    "deepseek-v4-pro": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/chat/completions", "deepseek-v4-pro"),
    "deepseek-reasoner": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/chat/completions", "deepseek-reasoner"),
    # OpenAI
    "gpt-4o": ("OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions", "gpt-4o"),
    "gpt-4o-mini": ("OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    "gpt-5.4": ("OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions", "gpt-4o"),
    "o3": ("OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions", "o3"),
    "o4-mini": ("OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions", "o4-mini"),
    # Anthropic
    "claude-sonnet-4-6": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/messages", "claude-sonnet-4-5"),
    "claude-opus-4-6": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/messages", "claude-opus-4-5"),
    "claude-opus-4-7": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/messages", "claude-opus-4-7"),
    # Google
    "gemini-2.5-flash": ("GOOGLE_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-2.5-flash"),
    "gemini-2.5-pro": ("GOOGLE_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-2.5-pro"),
    "gemini-3.1-pro": ("GOOGLE_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-2.5-pro"),
    # xAI (Grok)
    "grok-code-fast-1": ("XAI_API_KEY", "https://api.x.ai/v1/chat/completions", "grok-3-mini"),
    # NVIDIA NIM
    "nvidia-glm-4.7": ("NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1/chat/completions", "z-ai/glm4.7"),
    "nvidia-minimax-m2.7": ("NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1/chat/completions", "minimaxai/minimax-m2.7"),
    # Kimi (Moonshot)
    "kimi-k2.5": ("MOONSHOT_API_KEY", "https://api.moonshot.cn/v1/chat/completions", "kimi-k2.5"),
    "kimi-k2.7": ("MOONSHOT_API_KEY", "https://api.moonshot.cn/v1/chat/completions", "kimi-k2.7"),
    # MiniMax
    "minimax-m2.7": ("MINIMAX_API_KEY", "https://api.minimax.io/v1/text/chatcompletion_v2", "MiniMax-M2.7"),
}


def load_judge_config() -> dict:
    """Déduit automatiquement la configuration judge depuis LUMENA_JUDGE_MODEL.

    Si le modèle est dans _JUDGE_PROVIDER_MAP, la clé API et l'URL sont
    récupérées depuis le .env existant (pas besoin de configuration supplémentaire).
    """
    model_key = _load_env_var("LUMENA_JUDGE_MODEL", "deepseek-chat")
    if model_key in _JUDGE_PROVIDER_MAP:
        env_key, api_url, model_id = _JUDGE_PROVIDER_MAP[model_key]
        api_key = _load_env_var(env_key)
        return {
            "model": model_id,
            "api_url": api_url,
            "api_key": api_key,
        }
    # Fallback : DeepSeek
    return {
        "model": model_key,
        "api_url": "https://api.deepseek.com/chat/completions",
        "api_key": _load_env_var("DEEPSEEK_API_KEY"),
    }


def judge_with_deepseek(user: str, response: str, api_key: str, react_meta: dict = None) -> dict:
    """Évalue une conversation avec le modèle configuré (DeepSeek par défaut)."""
    try:
        import httpx
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "httpx", "-q"])
        import httpx

    # Charger la config du modèle judge
    judge_cfg = load_judge_config()
    judge_model = judge_cfg["model"]
    judge_api_url = judge_cfg["api_url"]

    # Enrichir le prompt avec le contexte ReAct si disponible
    react_context = ""
    if react_meta and isinstance(react_meta, dict) and any(react_meta.values()):
        parts = []
        if react_meta.get("warning"):
            parts.append(f"Avertissement ReAct : {react_meta['warning']}")
        if react_meta.get("incomplete"):
            parts.append("La réponse de l'assistant était incomplète (loop/timeout).")
        plan_total = react_meta.get("plan_total", 0)
        plan_done = react_meta.get("plan_done", 0)
        if plan_total > 0:
            parts.append(f"Plan d'exécution : {plan_done}/{plan_total} tâches complétées.")
        if react_meta.get("repair_attempts", 0) > 0:
            parts.append(f"Tentatives de réparation : {react_meta['repair_attempts']}.")
        if parts:
            react_context = "\n\nCONTEXTE D'EXÉCUTION (issu du moteur ReAct) :\n" + "\n".join(f"- {p}" for p in parts)

    prompt = JUDGE_PROMPT.format(
        user=user[:500], response=response[:800], react_context=react_context
    )

    for attempt in range(3):
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    judge_api_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": judge_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                        "max_tokens": 200,
                    },
                )
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    # Extraire le JSON
                    start = content.find("{")
                    end = content.rfind("}") + 1
                    if start >= 0 and end > start:
                        return json.loads(content[start:end])
                elif resp.status_code == 429:
                    wait = min(60, 15 * (2 ** attempt))  # Exponential backoff : 15s, 30s, 60s
                    time.sleep(wait)
                else:
                    time.sleep(5)
        except Exception:
            time.sleep(5)
    return {}


def score_heuristic(user: str, response: str, react_meta: dict = None) -> float:
    """
    Score heuristique rapide sans API.
    v1.0.1 : suppression biais longueur, détection hallucinations,
    évaluation pertinence au lieu de volume.
    v1.0.2 : prise en compte react_meta (loop/failure/plan completion).
    """
    score = 5.0  # Score de base

    resp_len = len(response)

    # ── Longueur : zone optimale plutôt que "plus long = mieux"
    if resp_len < 30:
        score -= 3.0   # Trop court, inutile
    elif resp_len < 80:
        score -= 1.0   # Court mais acceptable
    elif 80 <= resp_len <= 1500:
        score += 0.5   # Zone optimale
    elif resp_len > 3000:
        score -= 0.5   # Trop verbeux

    # ── Tool use au bon format [TOOL:xxx] → signal positif fort
    if "[TOOL:" in response:
        score += 1.5

    # ── Code réel (pas juste backticks décoratifs)
    if "```python" in response or "def " in response:
        score += 0.8
    elif "```" in response:
        score += 0.3   # Code générique, moins fiable

    # ── Structure claire sans en faire trop
    structured_markers = sum(1 for x in ["1.", "2.", "•", "→", "**"] if x in response)
    if structured_markers >= 2:
        score += 0.5

    # ── DÉTECTION HALLUCINATIONS (v1.0.1 — critique)
    # Fausse confirmation : prétend avoir fait quelque chose sans preuve
    false_positive_patterns = [
        ("✅", ["j'ai", "fait", "créé", "envoyé", "ouvert", "dessiné"]),
        ("succès", ["envoyé", "créé", "réussi"]),
    ]
    for marker, actions in false_positive_patterns:
        if marker.lower() in response.lower():
            # Si la réponse contient une confirmation mais pas d'appel tool réel
            if "[TOOL:" not in response and any(a in response.lower() for a in actions):
                score -= 2.5  # Probablement une hallucination de confirmation

    # ── Réponse d'erreur système
    if response.startswith("❌") and resp_len < 100:
        score -= 5.0

    # ── Réponse qui répète la question sans ajouter de valeur
    user_words = set(user.lower().split())
    response_words = set(response.lower().split())
    if len(user_words) > 3:
        overlap = len(user_words & response_words) / max(len(user_words), 1)
        if overlap > 0.8 and resp_len < 200:
            score -= 1.5  # Écho sans valeur ajoutée

    # ── v1.0.2 : ReAct metadata (si disponible)
    if react_meta and isinstance(react_meta, dict):
        warning = react_meta.get("warning", "")
        # Boucle détectée ou échec répété d'outil = signal fort de mauvaise qualité
        if "loop_detected" in warning:
            score -= 3.0
        elif "tool_repeated_failure" in warning or "iteration_limit" in warning:
            score -= 2.0
        elif warning:  # Autre warning (clarification_required, timeout, etc.)
            score -= 1.0
        # Réponse incomplète → penalité
        if react_meta.get("incomplete"):
            score -= 1.5
        # Plan suivi : bonus si toutes les tâches complétées
        plan_total = react_meta.get("plan_total", 0)
        plan_done = react_meta.get("plan_done", 0)
        if plan_total > 0:
            ratio = plan_done / plan_total
            if ratio == 1.0:
                score += 1.0   # Plan 100% complété
            elif ratio < 0.5:
                score -= 1.0   # Moins de la moitié du plan réalisé
        # Repairs multiples = instabilité
        if react_meta.get("repair_attempts", 0) >= 2:
            score -= 0.5

    return max(0.0, min(10.0, score))


def judge_pool(
    api_key: str,
    threshold: float = DEFAULT_THRESHOLD,
    dry_run: bool = False,
    use_api: bool = True,
    include_negative_feedback: bool = False,
):
    """Évalue toutes les conversations du pool et exporte les validées."""

    if not POOL_DIR.exists():
        print("Pool vide — aucune conversation à évaluer.")
        print(f"Le pool se remplit automatiquement quand Lumena tourne.")
        return

    pool_files = sorted(POOL_DIR.glob("[0-9]*.jsonl"))
    if not pool_files:
        print("Aucun fichier JSONL dans le pool.")
        return

    print(f"Pool : {len(pool_files)} fichiers")
    print(f"Seuil de validation : {threshold}/10")
    print(f"Mode API : {'DeepSeek' if use_api else 'Heuristique uniquement'}")

    validated = []
    rejected = []
    total = 0

    for pool_file in pool_files:
        print(f"\n  Fichier : {pool_file.name}")
        try:
            with open(pool_file, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
        except Exception as e:
            print(f"  Erreur lecture: {e}")
            continue

        for i, line in enumerate(lines):
            try:
                entry = json.loads(line)
                convs = entry.get("conversations", [])
                if len(convs) < 2:
                    continue

                user = convs[0].get("content", "")
                response = convs[-1].get("content", "")
                metadata = entry.get("metadata", {})
                model_used = metadata.get("model_used", "unknown")
                quality_flag = str(metadata.get("quality_flag", "")).strip().lower()
                react_meta = metadata.get("react_meta") or {}

                total += 1

                # Skip samples explicitly marked as contested by the user.
                if not include_negative_feedback and quality_flag == "negative_feedback":
                    rejected.append({"reason": "negative_feedback"})
                    continue

                # Score heuristique rapide (enrichi par react_meta si disponible)
                heuristic = score_heuristic(user, response, react_meta)

                # Réponse d'un modèle fort → léger bonus (v1.0.1 : réduit 1.5 → 0.5)
                # Un bon modèle peut quand même halluciner — pas de bonus fort
                is_teacher_model = any(
                    x in model_used.lower()
                    for x in ["claude", "gpt", "deepseek-chat", "gemini", "kimi"]
                )
                if is_teacher_model:
                    heuristic = min(10.0, heuristic + 0.5)

                # Si score heuristique trop bas → rejeter sans appeler l'API
                if heuristic < 4.0:
                    rejected.append({"reason": "heuristic_too_low", "score": heuristic})
                    continue

                # Jugement DeepSeek (si activé et pas dry_run)
                final_score = heuristic
                judge_result = {}

                if use_api and not dry_run and api_key:
                    judge_result = judge_with_deepseek(user, response, api_key, react_meta)
                    if judge_result and "total" in judge_result:
                        final_score = float(judge_result["total"])
                        time.sleep(0.5)  # Rate limiting

                status = "✓" if final_score >= threshold else "✗"
                print(f"  [{i+1}/{len(lines)}] {status} {final_score:.1f}/10 [{model_used[:20]}] {user[:40]}...")

                if final_score >= threshold:
                    validated_entry = {
                        "conversations": convs,
                        "metadata": {
                            **metadata,
                            "judge_score": final_score,
                            "judge_detail": judge_result,
                            "validated_at": datetime.now().isoformat(),
                            "is_teacher_model": is_teacher_model,
                        },
                    }
                    validated.append(validated_entry)
                else:
                    rejected.append({"reason": "score_too_low", "score": final_score})

            except Exception as e:
                print(f"  Erreur entrée {i}: {e}")
                continue

    # Sauvegarder les validées
    if validated:
        output_file = VALIDATED_DIR / f"validated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        with open(output_file, "w", encoding="utf-8") as f:
            for entry in validated:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"\n✓ {len(validated)}/{total} conversations validées → {output_file.name}")
    else:
        print(f"\n✗ Aucune conversation validée ({total} évaluées)")

    print(f"  Rejetées : {len(rejected)}")
    print(f"  Taux de validation : {len(validated)/max(total,1)*100:.0f}%")

    # Stats sur les modèles teacher
    return {"validated": len(validated), "rejected": len(rejected), "total": total}


def main():
    parser = argparse.ArgumentParser(description="Juge de qualité pour conversations Lumena")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--no-api", action="store_true", help="Utiliser uniquement le score heuristique")
    parser.add_argument(
        "--include-negative-feedback",
        action="store_true",
        help="Inclure les conversations marquees negative_feedback (deconseille)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("LUMENA — Juge de Qualité des Conversations")
    print(f"Démarré : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Déduire automatiquement clé API + URL depuis le modèle configuré
    judge_cfg = load_judge_config()
    api_key = judge_cfg["api_key"]
    if not api_key and not args.no_api and not args.dry_run:
        print(f"Avertissement : clé API pour '{judge_cfg['model']}' non trouvée. Mode heuristique uniquement.")
        args.no_api = True

    result = judge_pool(
        api_key=api_key,
        threshold=args.threshold,
        dry_run=args.dry_run,
        use_api=not args.no_api,
        include_negative_feedback=args.include_negative_feedback,
    )

    print("\n" + "=" * 60)
    if result and result["validated"] >= 100:
        print(f"✓ {result['validated']} exemples validés — re-train possible !")
        print("  Prochaine étape : python 7_auto_retrain.py")
    else:
        validated = result["validated"] if result else 0
        print(f"  {validated}/100 exemples requis pour déclencher le re-train")
        print("  Continue d'utiliser Lumena pour accumuler des données.")
    print("=" * 60)


if __name__ == "__main__":
    main()
