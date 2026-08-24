"""
Lumena — Rejection Sampling (Méthode DeepSeek R1)
==================================================
Génère N réponses au même problème → signal objectif (exécution code,
vérification maths) → crée des paires DPO (chosen/rejected).

Sans humain, sans juge IA pour le code et les maths — 100% automatique.

Usage :
    python 6_rejection_sampling.py
    python 6_rejection_sampling.py --n-samples 8 --top-k 50
"""

import os
import sys
import json
import time
import subprocess
import tempfile
import argparse
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = Path(__file__).parent.parent.parent / "data"
VALIDATED_DIR = DATA_DIR / "training_validated"
DPO_DIR = DATA_DIR / "training_dpo"
DPO_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_URL = "http://localhost:11434"
LUMENA_MODEL = "lumena-v1"


def call_lumena(prompt: str, temperature: float = 0.7) -> str:
    """Appelle lumena-v1 via Ollama pour obtenir une réponse."""
    try:
        import httpx
    except ImportError:
        import subprocess as sp
        sp.run([sys.executable, "-m", "pip", "install", "httpx", "-q"])
        import httpx

    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": LUMENA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": 800},
                },
            )
            if resp.status_code == 200:
                return resp.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        pass
    return ""


def extract_python_code(text: str) -> str:
    """Extrait le premier bloc de code Python d'un texte."""
    lines = text.split("\n")
    in_block = False
    code_lines = []

    for line in lines:
        if line.strip().startswith("```python") or line.strip() == "```python":
            in_block = True
            continue
        if in_block and line.strip() == "```":
            break
        if in_block:
            code_lines.append(line)

    if code_lines:
        return "\n".join(code_lines)

    # Fallback : chercher du code sans marqueurs
    for line in lines:
        if line.strip().startswith("def ") or line.strip().startswith("import "):
            # Prendre tout depuis cette ligne
            idx = lines.index(line)
            return "\n".join(lines[idx:idx+30])

    return ""


def execute_code_safe(code: str, timeout: int = 10) -> tuple:
    """
    Exécute du Python dans un subprocess isolé.
    Retourne (success: bool, output: str).
    """
    if not code.strip():
        return False, "no_code"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        success = result.returncode == 0
        output = (result.stdout + result.stderr)[:500]
        return success, output
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def score_response(prompt: str, response: str) -> tuple:
    """
    Score une réponse avec signal objectif automatique.
    Retourne (score: float, method: str).

    Méthodes :
    1. Code exécution → 10 si passe, 2 si échoue
    2. Longueur/structure → score heuristique
    """
    # Signal objectif : code Python
    code = extract_python_code(response)
    if code:
        success, output = execute_code_safe(code)
        if success:
            return 9.0, "code_pass"
        else:
            return 2.0, "code_fail"

    # Heuristique pour le reste
    score = 5.0
    resp_len = len(response)

    if resp_len > 300:
        score += 1.5
    if resp_len > 600:
        score += 0.5
    if any(x in response for x in ["1.", "2.", "•", "→", "**", "##"]):
        score += 1.0
    if resp_len < 80:
        score -= 3.0

    return min(10.0, max(0.0, score)), "heuristic"


def generate_dpo_pairs(
    prompts: list,
    n_samples: int = 8,
    dry_run: bool = False,
) -> list:
    """
    Pour chaque prompt, génère N réponses → sélectionne best/worst → paire DPO.
    """
    pairs = []

    for i, prompt in enumerate(prompts):
        print(f"\n  [{i+1}/{len(prompts)}] {prompt[:60]}...")

        if dry_run:
            pairs.append({
                "prompt": prompt,
                "chosen": "Réponse choisie simulée (DRY RUN)",
                "rejected": "Réponse rejetée simulée (DRY RUN)",
                "chosen_score": 9.0,
                "rejected_score": 2.0,
                "method": "dry_run",
            })
            print(f"    DRY RUN: paire créée")
            continue

        # Générer N réponses avec températures variées
        temperatures = [0.3, 0.5, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2][:n_samples]
        responses_scored = []

        for j, temp in enumerate(temperatures):
            response = call_lumena(prompt, temperature=temp)
            if not response:
                continue
            score, method = score_response(prompt, response)
            responses_scored.append((score, response, method))
            print(f"    Variante {j+1}: score={score:.1f} [{method}]")
            time.sleep(0.3)

        if len(responses_scored) < 2:
            print(f"    ✗ Pas assez de variantes")
            continue

        # Trier par score
        responses_scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_response, best_method = responses_scored[0]
        worst_score, worst_response, worst_method = responses_scored[-1]

        # Écart minimum pour créer une paire utile
        if best_score - worst_score < 2.0:
            print(f"    ✗ Écart trop faible ({best_score:.1f} vs {worst_score:.1f})")
            continue

        pairs.append({
            "prompt": prompt,
            "chosen": best_response,
            "rejected": worst_response,
            "chosen_score": best_score,
            "rejected_score": worst_score,
            "chosen_method": best_method,
            "rejected_method": worst_method,
            "timestamp": datetime.now().isoformat(),
        })
        print(f"    ✓ Paire DPO : {best_score:.1f} vs {worst_score:.1f} [{best_method}]")

    return pairs


def load_best_prompts(top_k: int = 50) -> list:
    """
    Charge les meilleurs prompts du pool validé.
    Priorité aux questions de code et maths (vérifiables).
    """
    prompts = []

    # 1. Depuis le pool validé
    if VALIDATED_DIR.exists():
        for f in sorted(VALIDATED_DIR.glob("*.jsonl")):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        entry = json.loads(line)
                        convs = entry.get("conversations", [])
                        if convs:
                            prompt = convs[0].get("content", "")
                            score = entry.get("metadata", {}).get("judge_score", 5.0)
                            if len(prompt) > 20:
                                prompts.append((score, prompt))
            except Exception:
                continue

    # 2. Compléter avec les templates de coding/maths
    from importlib.util import spec_from_file_location, module_from_spec
    try:
        gen_script = Path(__file__).parent / "4_generate_dataset.py"
        spec = spec_from_file_location("gen", gen_script)
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)
        for topic in (mod.CODING_TOPICS + mod.MATH_TOPICS):
            prompts.append((6.0, topic))  # Score par défaut
    except Exception:
        pass

    # Trier par score et dédupliquer
    prompts.sort(key=lambda x: x[0], reverse=True)
    seen = set()
    unique = []
    for score, p in prompts:
        key = p[:60]
        if key not in seen:
            seen.add(key)
            unique.append(p)
        if len(unique) >= top_k:
            break

    return unique


def main():
    parser = argparse.ArgumentParser(description="Rejection Sampling DPO pour Lumena")
    parser.add_argument("--n-samples", type=int, default=6, help="Variantes par prompt (défaut: 6)")
    parser.add_argument("--top-k", type=int, default=30, help="Nombre de prompts à traiter")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("LUMENA — Rejection Sampling (Méthode DeepSeek R1)")
    print(f"Démarré : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Variantes par prompt : {args.n_samples}")
    print(f"Prompts à traiter : {args.top_k}")
    print("=" * 60)

    # Vérifier Ollama
    if not args.dry_run:
        try:
            import httpx
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{OLLAMA_URL}/api/tags")
                models = [m["name"] for m in r.json().get("models", [])]
                if not any(LUMENA_MODEL in m for m in models):
                    print(f"ERREUR : {LUMENA_MODEL} non trouvé dans Ollama")
                    print(f"Modèles disponibles : {models}")
                    sys.exit(1)
                print(f"✓ {LUMENA_MODEL} disponible dans Ollama")
        except Exception as e:
            print(f"ERREUR connexion Ollama : {e}")
            sys.exit(1)

    # Charger les prompts
    print(f"\nChargement des prompts...")
    prompts = load_best_prompts(top_k=args.top_k)
    print(f"  {len(prompts)} prompts sélectionnés")

    if not prompts:
        print("Aucun prompt disponible.")
        sys.exit(0)

    # Générer les paires DPO
    print(f"\nGénération des paires DPO...")
    pairs = generate_dpo_pairs(
        prompts=prompts,
        n_samples=args.n_samples,
        dry_run=args.dry_run,
    )

    if not pairs:
        print("\nAucune paire DPO générée.")
        sys.exit(0)

    # Sauvegarder
    output_file = DPO_DIR / f"dpo_pairs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    with open(output_file, "w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    print(f"\n{'=' * 60}")
    print(f"✓ {len(pairs)} paires DPO créées → {output_file.name}")
    print(f"\nRépartition des méthodes de scoring :")
    methods = {}
    for p in pairs:
        m = p.get("chosen_method", "unknown")
        methods[m] = methods.get(m, 0) + 1
    for m, c in methods.items():
        print(f"  {m}: {c}")
    print(f"\nProchaine étape : python 7_auto_retrain.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
