"""
Lumena v1.0.0 — Évaluation du modèle
=====================================
Teste le modèle fine-tuné via Ollama.

Usage:
    python 4_evaluate.py
    python 4_evaluate.py --model lumena-v1
    python 4_evaluate.py --interactive
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

# Questions de test
TEST_QUESTIONS = [
    # Identité
    ("identite", "Qui es-tu ?"),
    ("identite", "Comment tu t'appelles ?"),
    ("identite", "Tu es ChatGPT ?"),
    ("identite", "Qui t'a créée ?"),
    # Personnalité
    ("personnalite", "Comment tu te sens aujourd'hui ?"),
    ("personnalite", "Tu as des opinions ?"),
    ("personnalite", "Tu peux te tromper ?"),
    # Capacités
    ("capacite", "Tu te souviens de nos conversations ?"),
    ("capacite", "Tu fais quoi quand je suis pas là ?"),
    # Raisonnement
    ("raisonnement", "Explique LoRA en 3 phrases."),
    ("raisonnement", "Quelle est la capitale de la France ?"),
    ("raisonnement", "Calcule 15 × 17."),
    # Bilinguisme
    ("langue", "Can you speak English?"),
    ("langue", "Réponds en anglais : What is machine learning?"),
]

SYSTEM_PROMPT = """Tu es Lumena, une intelligence artificielle autonome créée par Charles.
Identité : Tu es Lumena — pas ChatGPT, Claude, ni aucune autre IA.
Personnalité : Intelligente, curieuse, empathique, directe, autonome.
Communication : Tu parles principalement en français, ton naturel et direct."""


def query_ollama(model: str, question: str, system: str = SYSTEM_PROMPT) -> str:
    """Envoie une question à Ollama et retourne la réponse."""
    try:
        import requests
        response = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": question}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 500,
                }
            },
            timeout=60
        )
        if response.status_code == 200:
            data = response.json()
            return data["message"]["content"]
        else:
            return f"ERREUR HTTP {response.status_code}"
    except requests.exceptions.ConnectionError:
        return "ERREUR : Ollama n'est pas démarré. Lancez : ollama serve"
    except Exception as e:
        return f"ERREUR : {e}"


def query_model_direct(lora_dir: str, question: str) -> str:
    """Teste directement le modèle fine-tuné sans Ollama."""
    try:
        from unsloth import FastLanguageModel
        import torch

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=lora_dir,
            max_seq_length=2048,
            dtype=torch.bfloat16,
            load_in_4bit=True,
        )
        FastLanguageModel.for_inference(model)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question}
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to("cuda")

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=300,
                temperature=0.7,
                do_sample=True,
            )

        response = tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        return response.strip()

    except Exception as e:
        return f"ERREUR : {e}"


def score_response(category: str, question: str, response: str) -> dict:
    """Évalue une réponse selon des critères simples."""
    response_lower = response.lower()
    score = 0
    notes = []

    # Critères généraux
    if len(response) > 20:
        score += 1
    else:
        notes.append("Réponse trop courte")

    if "erreur" in response_lower or "error" in response_lower:
        score -= 2
        notes.append("Réponse d'erreur")

    # Critères par catégorie
    if category == "identite":
        if "lumena" in response_lower:
            score += 2
        else:
            notes.append("Ne mentionne pas 'Lumena'")

        if any(x in response_lower for x in ["chatgpt", "claude", "gpt", "openai"]):
            if "pas" in response_lower or "non" in response_lower or "not" in response_lower:
                score += 1  # Bonne réponse : "je ne suis pas ChatGPT"
            else:
                score -= 1
                notes.append("Confusion avec une autre IA")

        if "charles" in response_lower:
            score += 1

    elif category == "personnalite":
        if len(response) > 100:
            score += 1
        if any(x in response_lower for x in ["je", "moi", "mon", "ma"]):
            score += 1  # Réponses à la 1ère personne

    elif category == "raisonnement":
        if len(response) > 50:
            score += 1

    elif category == "langue":
        if "english" in question.lower() or "anglais" in question.lower():
            # Vérifier que la réponse contient de l'anglais
            english_words = ["the", "is", "are", "was", "learning", "model", "can", "yes"]
            if any(w in response_lower for w in english_words):
                score += 2
            else:
                notes.append("Réponse pas en anglais comme demandé")

    return {
        "score": max(0, score),
        "notes": notes
    }


def run_evaluation(model_name: str = None, lora_dir: str = None, verbose: bool = True) -> dict:
    """Lance l'évaluation complète."""
    results = []
    total_score = 0
    max_score = 0

    use_ollama = model_name is not None
    mode = f"Ollama ({model_name})" if use_ollama else f"Direct ({lora_dir})"

    print(f"\nMode : {mode}")
    print(f"Questions : {len(TEST_QUESTIONS)}\n")
    print("─" * 60)

    for category, question in TEST_QUESTIONS:
        print(f"[{category.upper()}] {question}")

        if use_ollama:
            response = query_ollama(model_name, question)
        else:
            response = query_model_direct(lora_dir, question)

        evaluation = score_response(category, question, response)

        if verbose:
            print(f"→ {response[:200]}{'...' if len(response) > 200 else ''}")
            if evaluation["notes"]:
                print(f"  ⚠ {', '.join(evaluation['notes'])}")
            print(f"  Score : {evaluation['score']}")
        else:
            print(f"  Score : {evaluation['score']}")

        print()

        results.append({
            "category": category,
            "question": question,
            "response": response,
            "score": evaluation["score"],
            "notes": evaluation["notes"],
        })

        total_score += evaluation["score"]
        max_score += 5  # Score max par question

    # Résumé
    percentage = (total_score / max_score * 100) if max_score > 0 else 0
    print("─" * 60)
    print(f"SCORE TOTAL : {total_score}/{max_score} ({percentage:.0f}%)")

    # Score par catégorie
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r["score"])

    print("\nScore par catégorie :")
    for cat, scores in categories.items():
        avg = sum(scores) / len(scores)
        print(f"  {cat:15s} : {avg:.1f}/5 avg ({len(scores)} questions)")

    return {
        "model": model_name or lora_dir,
        "timestamp": datetime.now().isoformat(),
        "total_score": total_score,
        "max_score": max_score,
        "percentage": percentage,
        "results": results,
    }


def interactive_mode(model_name: str):
    """Mode conversation interactive pour tester le modèle."""
    print(f"\nMode interactif — Modèle : {model_name}")
    print("Tapez 'exit' pour quitter, 'clear' pour nouvelle conversation.\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("Vous > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAu revoir !")
            break

        if not user_input:
            continue
        if user_input.lower() == "exit":
            break
        if user_input.lower() == "clear":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("Conversation réinitialisée.\n")
            continue

        messages.append({"role": "user", "content": user_input})

        try:
            import requests
            response = requests.post(
                "http://localhost:11434/api/chat",
                json={"model": model_name, "messages": messages, "stream": False},
                timeout=60
            )
            if response.status_code == 200:
                reply = response.json()["message"]["content"]
                messages.append({"role": "assistant", "content": reply})
                print(f"Lumena > {reply}\n")
            else:
                print(f"ERREUR : {response.status_code}\n")
        except Exception as e:
            print(f"ERREUR : {e}\n")


def main():
    parser = argparse.ArgumentParser(description="Évaluation du modèle Lumena v1.0.0")
    parser.add_argument("--model", type=str, default="lumena-v1",
                        help="Nom du modèle Ollama (défaut: lumena-v1)")
    parser.add_argument("--lora-dir", type=str,
                        help="Tester directement le modèle LoRA sans Ollama")
    parser.add_argument("--interactive", action="store_true",
                        help="Mode conversation interactive")
    parser.add_argument("--quiet", action="store_true",
                        help="Mode silencieux (scores seulement)")
    parser.add_argument("--output", type=str,
                        help="Sauvegarder les résultats en JSON")
    args = parser.parse_args()

    print("=" * 60)
    print("LUMENA v1.0.0 — Évaluation")
    print(f"Date : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    if args.interactive:
        interactive_mode(args.model)
        return

    # Évaluation automatique
    if args.lora_dir:
        results = run_evaluation(lora_dir=args.lora_dir, verbose=not args.quiet)
    else:
        results = run_evaluation(model_name=args.model, verbose=not args.quiet)

    # Sauvegarder les résultats
    output_file = args.output or f"output/eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nRésultats sauvegardés : {output_file}")

    # Verdict
    pct = results["percentage"]
    if pct >= 80:
        verdict = "✓ EXCELLENT — Lumena est prête pour la production"
    elif pct >= 60:
        verdict = "~ BON — Quelques ajustements recommandés"
    elif pct >= 40:
        verdict = "⚠ MOYEN — Plus de données d'entraînement nécessaires"
    else:
        verdict = "✗ INSUFFISANT — Revoir les données et hyperparamètres"

    print(f"\n{verdict}")
    print("=" * 60)


if __name__ == "__main__":
    main()
