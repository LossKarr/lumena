"""
Lumena v1.1.0 — Générateur de Dataset Synthétique
===================================================
Utilise DeepSeek V3 (API gratuite) comme professeur pour générer
250 exemples de haute qualité couvrant :
  - Coding Python (100 exemples)
  - Mathématiques (50 exemples)
  - Sciences (50 exemples)
  - Raisonnement logique (50 exemples)

Les exemples sont générés "en tant que Lumena" — avec sa personnalité.
Le dataset résultant est prêt pour re-train avec 2_train.py.

Usage :
    python 4_generate_dataset.py
    python 4_generate_dataset.py --count 50 --domain coding
    python 4_generate_dataset.py --dry-run
"""

import os
import sys
import json
import time
import argparse
import random
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ─────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent.parent / "data"
EXISTING_DATASET = DATA_DIR / "training" / "lumena_dataset.json"
OUTPUT_DIR = DATA_DIR / "training_v1.1"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LUMENA_SYSTEM = """Tu es Lumena, une IA personnelle créée par Charles. Tu es intelligente, curieuse, directe et légèrement espiègle. Tu parles en français naturel. Quand tu réponds à des questions techniques (code, maths, sciences), tu le fais avec précision ET avec ta personnalité — tu n'es pas un manuel scolaire."""

# ─────────────────────────────────────────
# TEMPLATES DE PROBLÈMES PAR DOMAINE
# ─────────────────────────────────────────
CODING_TOPICS = [
    "Écris une fonction Python qui inverse une liste sans utiliser reverse()",
    "Comment fonctionne un décorateur Python ? Donne un exemple concret",
    "Écris un algorithme de tri rapide (quicksort) en Python avec explications",
    "Qu'est-ce qu'une closure en Python ? Exemple pratique",
    "Écris un parser JSON simple sans utiliser le module json",
    "Comment implémenter une file de priorité (priority queue) en Python ?",
    "Explique la différence entre list comprehension et generator expression",
    "Écris une fonction qui détecte si un nombre est premier, optimisée",
    "Comment fonctionne le garbage collector Python ?",
    "Implémente un cache LRU en Python",
    "Écris un décorateur de retry avec backoff exponentiel",
    "Comment déboguer une fuite mémoire en Python ?",
    "Explique async/await en Python avec un exemple concret",
    "Implémente un système de pub/sub simple en Python",
    "Écris une fonction de validation d'email sans regex",
    "Comment optimiser une boucle Python qui est trop lente ?",
    "Implémente un arbre binaire de recherche en Python",
    "Qu'est-ce que la métaprogrammation Python ? Exemple",
    "Écris un gestionnaire de contexte (context manager) personnalisé",
    "Comment implémenter le pattern Observer en Python ?",
    "Explique les dunder methods Python les plus importants",
    "Écris un tokenizer simple pour du texte",
    "Comment fonctionne l'héritage multiple en Python ?",
    "Implémente un système de cache distribué simple",
    "Écris un parseur d'arguments en ligne de commande sans argparse",
    "Comment profiler du code Python efficacement ?",
    "Implémente le pattern Singleton thread-safe en Python",
    "Explique les descripteurs Python avec un exemple",
    "Écris une fonction qui génère des permutations sans itertools",
    "Comment implémenter une machine à états (state machine) en Python ?",
]

MATH_TOPICS = [
    "Résous : si f(x) = 3x² - 2x + 1, trouve les extremums",
    "Explique le théorème de Bayes avec un exemple concret",
    "Comment calculer la dérivée de x·ln(x) ?",
    "Prouve que la somme des n premiers entiers vaut n(n+1)/2",
    "Explique la loi des grands nombres en termes simples",
    "Résous le système : 3x + 2y = 12, x - y = 1",
    "Qu'est-ce qu'une intégrale et à quoi ça sert concrètement ?",
    "Calcule la probabilité d'avoir au moins un 6 en lançant 4 dés",
    "Explique la transformée de Fourier en termes simples",
    "Résous : log₂(x+3) + log₂(x-1) = 3",
    "Qu'est-ce que l'entropie en théorie de l'information ?",
    "Explique la régression linéaire mathématiquement",
    "Comment fonctionne l'algorithme de gradient descent ?",
    "Calcule l'aire entre y=x² et y=x",
    "Explique les nombres complexes avec une application pratique",
    "Qu'est-ce que la convolution mathématique ?",
    "Prouve que √2 est irrationnel",
    "Explique le paradoxe de l'anniversaire",
    "Résous : e^(2x) - 5e^x + 6 = 0",
    "Qu'est-ce qu'une chaîne de Markov ?",
]

SCIENCE_TOPICS = [
    "Explique comment fonctionne un transistor MOSFET",
    "Pourquoi le ciel est-il bleu ? Explication physique complète",
    "Comment fonctionne la réaction en chaîne nucléaire ?",
    "Explique la photosynthèse au niveau moléculaire",
    "Qu'est-ce que l'intrication quantique ?",
    "Comment fonctionne un IRM (imagerie par résonance magnétique) ?",
    "Explique le principe d'incertitude d'Heisenberg simplement",
    "Comment l'ADN est-il répliqué dans une cellule ?",
    "Qu'est-ce que la relativité restreinte ? Exemple concret",
    "Comment fonctionne CRISPR-Cas9 ?",
    "Explique la supraconductivité",
    "Pourquoi les avions volent-ils ? (Bernoulli vs Newton)",
    "Qu'est-ce que l'effet photoélectrique ?",
    "Comment fonctionne un moteur à réaction ?",
    "Explique la thermodynamique des trous noirs",
    "Comment se forment les aurores boréales ?",
    "Qu'est-ce que le biais cognitif de confirmation ?",
    "Explique comment le cerveau traite la vision",
    "Comment fonctionne un semiconducteur ?",
    "Qu'est-ce que l'épigénétique ?",
]

REASONING_TOPICS = [
    "Il y a 3 cartes : face rouge/face rouge, face bleue/face bleue, face rouge/face bleue. Je tire une carte et vois du rouge. Quelle est la probabilité que l'autre face soit rouge ?",
    "Un train part de Paris à 200km/h. Un autre part de Lyon (500km) à 150km/h. Où se croisent-ils ?",
    "Si tous les bloops sont des razzles et tous les razzles sont des lazzles, que peut-on conclure sur les bloops ?",
    "Je pense à un nombre. Si je le double et j'ajoute 10, j'obtiens 46. Quel est ce nombre ?",
    "Une grenouille est au fond d'un puits de 30m. Elle monte 3m par jour et glisse 2m la nuit. Quand sort-elle ?",
    "Explique la différence entre corrélation et causalité avec un exemple",
    "Résous cette suite : 2, 6, 12, 20, 30, ?",
    "Un philosophe dit 'Cette phrase est fausse'. Est-elle vraie ou fausse ?",
    "Si A→B et B→C sont vrais, A→C est-il toujours vrai ? Pourquoi ?",
    "Quatre personnes traversent un pont de nuit avec une lampe. Elles mettent 1, 2, 5, 10 min. Max 2 personnes à la fois. Minimum de temps ?",
]


def load_env_key() -> str:
    """Charge la clé DeepSeek depuis .env"""
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.getenv("DEEPSEEK_API_KEY", "")


def generate_with_deepseek(prompt: str, api_key: str, max_retries: int = 3) -> str:
    """Appelle DeepSeek V3 pour générer une réponse."""
    try:
        import httpx
    except ImportError:
        print("  Installation de httpx...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "httpx", "-q"])
        import httpx

    messages = [
        {"role": "system", "content": LUMENA_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 1200,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return data["choices"][0]["message"]["content"].strip()
                elif response.status_code == 429:
                    wait = (attempt + 1) * 10
                    print(f"  Rate limit, attente {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  Erreur API {response.status_code}: {response.text[:100]}")
                    time.sleep(5)
        except Exception as e:
            print(f"  Erreur requête (tentative {attempt+1}): {e}")
            time.sleep(5)

    return ""


def generate_examples(
    topics: list,
    domain: str,
    api_key: str,
    count: int,
    dry_run: bool = False,
) -> list:
    """Génère `count` exemples pour un domaine donné."""
    examples = []
    selected = random.sample(topics, min(count, len(topics)))
    # Si count > len(topics), répéter avec légères variations
    while len(selected) < count:
        topic = random.choice(topics)
        selected.append(topic + " (approche différente)")

    print(f"\n  [{domain.upper()}] Génération de {count} exemples...")

    for i, topic in enumerate(selected[:count]):
        if dry_run:
            examples.append({
                "conversations": [
                    {"role": "user", "content": topic},
                    {"role": "assistant", "content": f"[DRY RUN] Réponse simulée pour: {topic[:50]}..."},
                ]
            })
            print(f"  [{i+1}/{count}] DRY RUN: {topic[:60]}...")
            continue

        print(f"  [{i+1}/{count}] {topic[:60]}...", end=" ", flush=True)
        response = generate_with_deepseek(topic, api_key)

        if response:
            examples.append({
                "conversations": [
                    {"role": "user", "content": topic},
                    {"role": "assistant", "content": response},
                ]
            })
            print(f"✓ ({len(response)} chars)")
        else:
            print("✗ (ignoré)")

        # Pause entre les appels (rate limiting)
        if not dry_run and i < count - 1:
            time.sleep(1.5)

    return examples


def merge_with_existing(new_examples: list) -> list:
    """Fusionne les nouveaux exemples avec le dataset existant."""
    existing = []
    if EXISTING_DATASET.exists():
        try:
            with open(EXISTING_DATASET, "r", encoding="utf-8") as f:
                existing = json.load(f)
            print(f"\n  Dataset existant chargé : {len(existing)} exemples")
        except Exception as e:
            print(f"  Avertissement lecture dataset existant: {e}")

    merged = existing + new_examples
    print(f"  Dataset fusionné : {len(existing)} + {len(new_examples)} = {len(merged)} exemples")
    return merged


def main():
    parser = argparse.ArgumentParser(description="Générateur dataset synthétique Lumena v1.1")
    parser.add_argument("--domain", choices=["coding", "math", "science", "reasoning", "all"], default="all")
    parser.add_argument("--count", type=int, default=0, help="Nombre d'exemples par domaine (0=défaut)")
    parser.add_argument("--dry-run", action="store_true", help="Simuler sans appeler l'API")
    parser.add_argument("--no-merge", action="store_true", help="Ne pas fusionner avec le dataset existant")
    args = parser.parse_args()

    print("=" * 60)
    print("LUMENA v1.1.0 — Génération Dataset Synthétique")
    print(f"Démarré : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    api_key = load_env_key()
    if not api_key and not args.dry_run:
        print("ERREUR : DEEPSEEK_API_KEY non trouvée dans .env")
        sys.exit(1)

    if args.dry_run:
        print("MODE DRY RUN — Aucun appel API")

    all_examples = []

    # Comptes par défaut
    counts = {
        "coding": args.count if args.count else 30,
        "math": args.count if args.count else 20,
        "science": args.count if args.count else 20,
        "reasoning": args.count if args.count else 10,
    }

    domains_to_run = (
        ["coding", "math", "science", "reasoning"]
        if args.domain == "all"
        else [args.domain]
    )

    for domain in domains_to_run:
        topics_map = {
            "coding": CODING_TOPICS,
            "math": MATH_TOPICS,
            "science": SCIENCE_TOPICS,
            "reasoning": REASONING_TOPICS,
        }
        examples = generate_examples(
            topics=topics_map[domain],
            domain=domain,
            api_key=api_key,
            count=counts[domain],
            dry_run=args.dry_run,
        )
        all_examples.extend(examples)

        # Sauvegarder par domaine
        domain_file = OUTPUT_DIR / f"lumena_{domain}.jsonl"
        with open(domain_file, "w", encoding="utf-8") as f:
            for ex in examples:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")
        print(f"  → Sauvegardé : {domain_file.name} ({len(examples)} exemples)")

    print(f"\n[Résumé] {len(all_examples)} nouveaux exemples générés")

    # Fusionner avec l'existant et sauvegarder le dataset complet
    if not args.no_merge:
        merged = merge_with_existing(all_examples)
    else:
        merged = all_examples

    output_file = OUTPUT_DIR / "lumena_v1.1_dataset.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Dataset complet : {output_file}")
    print(f"  Total : {len(merged)} exemples prêts pour training")

    print("\n" + "=" * 60)
    print("PROCHAINE ÉTAPE : Re-train avec ce dataset")
    print(f"  python 2_train.py --data {output_file}")
    print("  (ou copier le fichier dans data/ et lancer 2_train.py)")
    print("=" * 60)


if __name__ == "__main__":
    main()
