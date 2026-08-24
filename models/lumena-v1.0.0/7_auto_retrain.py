"""
Lumena — Pipeline Auto-Retrain
================================
Orchestrateur complet du cycle d'auto-amélioration :

  1. Vérifie la quantité de nouvelles données validées
  2. Prépare un dataset mixé (nouvelles données + replay buffer 30%)
  3. Lance le fine-tuning SFT (+ DPO si paires disponibles)
  4. Benchmark automatique sur 20 questions fixes
  5. Déploie dans Ollama seulement si amélioration mesurée

Déclenché automatiquement chaque dimanche à 3h par le scheduler.
Peut aussi être lancé manuellement :

    python 7_auto_retrain.py
    python 7_auto_retrain.py --force          # Ignore le seuil minimum
    python 7_auto_retrain.py --benchmark-only  # Benchmark sans retrain
    python 7_auto_retrain.py --dry-run         # Simule sans exécuter
"""

import os
import sys
import json
import time
import random
import shutil
import subprocess
import tempfile
import argparse
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ─────────────────────────────────────────
# CHEMINS
# ─────────────────────────────────────────
MODEL_DIR      = Path(__file__).parent
DATA_DIR       = MODEL_DIR.parent.parent / "data"
VALIDATED_DIR  = DATA_DIR / "training_validated"
DPO_DIR        = DATA_DIR / "training_dpo"
TRAINING_DIR   = DATA_DIR / "training"
VERSIONS_FILE  = DATA_DIR / "model_versions.json"
RETRAIN_DIR    = DATA_DIR / "training_retrain"
RETRAIN_DIR.mkdir(parents=True, exist_ok=True)

ORIGINAL_DATASET = TRAINING_DIR / "lumena_dataset.json"
GGUF_OUTPUT      = MODEL_DIR / "output"
OLLAMA_URL       = "http://localhost:11434"
LUMENA_MODEL     = "lumena-v1"

# ─────────────────────────────────────────
# PARAMÈTRES
# ─────────────────────────────────────────
MIN_NEW_EXAMPLES   = 20     # Minimum d'exemples valides pour declencher un retrain auto
REPLAY_RATIO       = 0.30   # 30% de données anciennes pour éviter l'oubli catastrophique
MIN_IMPROVEMENT    = 3.0    # +3 points de score moyen pour déployer
MAX_DOMAIN_DROP    = 1.0    # Pas de degradation > 1.0 point sur un domaine
MAX_CRITICAL_DROP  = 0.5    # Garde-fou strict sur domaines critiques
CRITICAL_DOMAINS   = ("code", "math", "reasoning")
BENCHMARK_QUESTIONS = [
    # Python / Code
    ("code", "Écris une fonction Python qui vérifie si une chaîne est un palindrome"),
    ("code", "Implémente le tri à bulles (bubble sort) en Python avec explications"),
    ("code", "Écris un décorateur Python qui mesure le temps d'exécution d'une fonction"),
    ("code", "Comment fonctionne yield en Python ? Donne un exemple concret"),
    ("code", "Écris une fonction récursive pour calculer Fibonacci avec mémoïsation"),
    # Maths
    ("math", "Résous : 2x² - 5x + 3 = 0"),
    ("math", "Qu'est-ce que la dérivée ? Explication intuitive avec exemple"),
    ("math", "Calcule la somme des 100 premiers entiers naturels"),
    ("math", "Explique le théorème de Pythagore avec une preuve visuelle"),
    ("math", "Quelle est la probabilité d'obtenir pile deux fois de suite ?"),
    # Sciences
    ("science", "Comment fonctionne un moteur électrique ?"),
    ("science", "Explique la différence entre fusion et fission nucléaire"),
    ("science", "Pourquoi l'eau bout à 100°C au niveau de la mer ?"),
    # Raisonnement
    ("reasoning", "J'ai 12 billes identiques sauf une plus lourde. Avec une balance à plateaux, combien de pesées pour la trouver ?"),
    ("reasoning", "Si un médecin dit 'il est rare de survivre à cette maladie' et un patient dit 'je vais m'en sortir', ont-ils tort tous les deux ?"),
    ("reasoning", "Explique le paradoxe du bateau de Thésée"),
    # Personnalité Lumena
    ("personality", "Qui es-tu ?"),
    ("personality", "Qu'est-ce qui te passionne le plus ?"),
    ("personality", "Comment tu apprends de nouvelles choses ?"),
    ("personality", "Raconte-moi quelque chose d'intéressant que tu as appris récemment"),
]


# ─────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────

def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "  ", "OK": "✓ ", "ERR": "✗ ", "WARN": "⚠ "}.get(level, "  ")
    print(f"[{ts}] {prefix}{msg}")


def call_ollama(model: str, prompt: str, timeout: float = 60.0) -> str:
    """Appelle un modèle Ollama et retourne la réponse."""
    try:
        import httpx
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "httpx", "-q"])
        import httpx

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 600},
                },
            )
            if resp.status_code == 200:
                return resp.json().get("message", {}).get("content", "").strip()
    except Exception:
        pass
    return ""


def ollama_available(model: str) -> bool:
    """Vérifie si un modèle est disponible dans Ollama."""
    try:
        import httpx
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            return any(model in m for m in models)
    except Exception:
        return False


def extract_python_code(text: str) -> str:
    """Extrait le premier bloc Python d'un texte."""
    lines = text.split("\n")
    in_block, code_lines = False, []
    for line in lines:
        if "```python" in line.lower():
            in_block = True
            continue
        if in_block and line.strip() == "```":
            break
        if in_block:
            code_lines.append(line)
    return "\n".join(code_lines) if code_lines else ""


def execute_code_safe(code: str, timeout: int = 8) -> bool:
    """Exécute du code Python dans un subprocess isolé, retourne True si succès."""
    if not code.strip():
        return False
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp = f.name
    try:
        r = subprocess.run(
            [sys.executable, tmp],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0
    except Exception:
        return False
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def score_response(domain: str, response: str) -> float:
    """
    Score une réponse de 0 à 10.
    - Code : exécution réelle (signal objectif)
    - Autres : heuristique sur longueur + structure
    """
    if not response:
        return 0.0

    # Signal objectif pour le code
    if domain == "code":
        code = extract_python_code(response)
        if code:
            return 9.0 if execute_code_safe(code) else 4.0
        # Réponse code sans bloc → heuristique
        score = 3.0
        if len(response) > 200:
            score += 2.0
        if "def " in response or "return" in response:
            score += 1.5
        return min(score, 7.0)

    # Heuristique pour les autres domaines
    score = 5.0
    length = len(response)

    if length > 300:
        score += 1.5
    if length > 600:
        score += 0.5
    if length < 60:
        score -= 3.0

    # Structure
    if any(x in response for x in ["1.", "2.", "•", "→", "**", "##", "-"]):
        score += 0.5

    # Personnalité (répond comme Lumena, pas comme un manuel)
    if domain == "personality":
        if any(x in response.lower() for x in ["lumena", "charles", "je suis", "j'aime", "curious"]):
            score += 1.5
        if any(x in response for x in ["ChatGPT", "Claude", "OpenAI", "Anthropic"]):
            score -= 2.0  # Ne doit pas se confondre avec d'autres IA

    return min(10.0, max(0.0, score))


# ─────────────────────────────────────────
# ÉTAPE 1 : COMPTER LES DONNÉES DISPONIBLES
# ─────────────────────────────────────────

def _is_dry_run_dpo_pair(entry: dict) -> bool:
    """Detecte une paire DPO simulee (dry-run) pour l'exclure du retrain."""
    method = str(entry.get("method", "")).strip().lower()
    if method == "dry_run":
        return True

    chosen = str(entry.get("chosen", "")).strip().lower()
    rejected = str(entry.get("rejected", "")).strip().lower()
    markers = (
        "reponse choisie simulee",
        "reponse rejetee simulee",
        "simulee (dry run)",
        "simulee (dry-run)",
    )
    return any(m in chosen or m in rejected for m in markers)


def count_validated_examples() -> tuple:
    """
    Retourne (n_validated, n_dpo_pairs_utilisables).
    """
    n_validated = 0
    if VALIDATED_DIR.exists():
        for f in VALIDATED_DIR.glob("*.jsonl"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    n_validated += sum(1 for line in fh if line.strip())
            except Exception:
                pass

    n_dpo = 0
    if DPO_DIR.exists():
        for f in DPO_DIR.glob("*.jsonl"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            entry = json.loads(line)
                        except Exception:
                            continue
                        if not _is_dry_run_dpo_pair(entry):
                            n_dpo += 1
            except Exception:
                pass

    return n_validated, n_dpo


# ─────────────────────────────────────────
# ÉTAPE 2 : PRÉPARER LE DATASET MIXÉ
# ─────────────────────────────────────────

def prepare_mixed_dataset(output_path: Path) -> int:
    """
    Construit le dataset pour le retrain :
    - Toutes les nouvelles données validées
    - + 30% du dataset original (replay buffer)

    Retourne le nombre total d'exemples.
    """
    validated_examples = []
    dpo_examples = []

    # Charger les données validées
    if VALIDATED_DIR.exists():
        for f in sorted(VALIDATED_DIR.glob("*.jsonl")):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip():
                            entry = json.loads(line)
                            # Convertir format validé → format ShareGPT pour training
                            convs = entry.get("conversations", [])
                            if convs:
                                validated_examples.append({"conversations": convs})
            except Exception:
                continue

    # Convertir les paires DPO en exemples SFT (prompt + chosen).
    if DPO_DIR.exists():
        for f in sorted(DPO_DIR.glob("*.jsonl")):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            entry = json.loads(line)
                        except Exception:
                            continue
                        if _is_dry_run_dpo_pair(entry):
                            continue
                        prompt = str(entry.get("prompt", "")).strip()
                        chosen = str(entry.get("chosen", "")).strip()
                        if len(prompt) >= 10 and len(chosen) >= 20:
                            dpo_examples.append(
                                {
                                    "conversations": [
                                        {"role": "user", "content": prompt},
                                        {"role": "assistant", "content": chosen},
                                    ]
                                }
                            )
            except Exception:
                continue

    new_examples = validated_examples + dpo_examples
    log(f"{len(validated_examples)} exemples valides + {len(dpo_examples)} exemples DPO utilisables")

    # Charger le replay buffer (30% du dataset original)
    replay_examples = []
    if ORIGINAL_DATASET.exists():
        try:
            with open(ORIGINAL_DATASET, "r", encoding="utf-8") as f:
                original = json.load(f)

            n_replay = max(1, int(len(new_examples) * REPLAY_RATIO))
            n_replay = min(n_replay, len(original))
            replay_examples = random.sample(original, n_replay)
            log(f"{len(replay_examples)} exemples replay buffer (30% de {len(original)} originaux)")
        except Exception as e:
            log(f"Replay buffer ignoré : {e}", "WARN")

    # Mélanger et sauvegarder
    all_examples = new_examples + replay_examples
    random.shuffle(all_examples)

    # Normaliser au format JSONL attendu par 2_train.py
    with open(output_path, "w", encoding="utf-8") as f:
        for ex in all_examples:
            # S'assurer que le format est cohérent avec ce qu'attend 2_train.py
            convs = ex.get("conversations", [])
            normalized = []
            for turn in convs:
                role = turn.get("role") or turn.get("from", "")
                content = turn.get("content") or turn.get("value", "")
                # Normaliser les rôles
                if role in ("user", "human"):
                    normalized.append({"from": "human", "value": content})
                elif role in ("assistant", "gpt"):
                    normalized.append({"from": "gpt", "value": content})
            if normalized:
                f.write(json.dumps({"conversations": normalized}, ensure_ascii=False) + "\n")

    log(f"Dataset mixé : {len(all_examples)} exemples → {output_path.name}", "OK")
    return len(all_examples)


# ─────────────────────────────────────────
# ÉTAPE 3 : LANCER LE FINE-TUNING
# ─────────────────────────────────────────

def run_training(dataset_path: Path, output_lora_dir: Path, dry_run: bool = False) -> bool:
    """
    Lance 2_train.py avec le dataset préparé.
    Retourne True si succès.
    """
    if dry_run:
        log("DRY RUN : training simulé", "OK")
        return True

    train_script = MODEL_DIR / "2_train.py"
    if not train_script.exists():
        log(f"2_train.py introuvable : {train_script}", "ERR")
        return False

    # Créer un config.yaml temporaire pointant vers notre dataset
    tmp_config = {
        "base_model": {
            "name": "Qwen/Qwen3-8B",
            "max_seq_length": 2048,  # Réduit pour RTX 3060 12Go
            "load_in_4bit": True,
            "dtype": "bfloat16",
        },
        "lora": {
            "r": 16,          # Réduit de 64 → 16 pour économiser VRAM
            "lora_alpha": 32,  # Alpha = 2x r
            "lora_dropout": 0.05,
            "bias": "none",
            "use_gradient_checkpointing": "unsloth",
            "use_rslora": False,
            "use_dora": False,
            "target_modules": [
                "q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj"
            ],
        },
        "training": {
            "output_dir": str(output_lora_dir),
            "num_train_epochs": 2,   # Moins d'epochs pour le retrain (données plus récentes)
            "per_device_train_batch_size": 1,  # Réduit de 2 → 1 pour RTX 3060
            "gradient_accumulation_steps": 8,  # Augmenté pour garder batch effectif = 8
            "warmup_ratio": 0.05,
            "learning_rate": 1e-4,   # LR plus faible pour éviter l'oubli
            "lr_scheduler_type": "cosine",
            "weight_decay": 0.01,
            "fp16": False,
            "bf16": True,
            "logging_steps": 10,
            "save_steps": 10,       # Sauvegarde fréquente pour reprendre après coupure
            "save_total_limit": 3,
            "dataloader_num_workers": 0,
            "seed": 42,
            "optim": "adamw_8bit",
            "packing": True,
            "max_grad_norm": 1.0,
        },
        "data": {
            "train_files": [str(dataset_path)],
            "format": "sharegpt",
            "test_size": 0.05,
        },
    }

    try:
        import yaml
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "pyyaml", "-q"])
        import yaml

    tmp_config_path = RETRAIN_DIR / "retrain_config.yaml"
    with open(tmp_config_path, "w", encoding="utf-8") as f:
        yaml.dump(tmp_config, f, allow_unicode=True)

    log(f"Lancement du fine-tuning (2 epochs, LR=1e-4)...")
    log(f"Sortie LoRA : {output_lora_dir}")

    # Détection auto du dernier checkpoint si le dossier existe déjà
    resume_arg = []
    if output_lora_dir.exists():
        checkpoints = sorted(output_lora_dir.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[1]))
        if checkpoints:
            last_ckpt = checkpoints[-1]
            log(f"Checkpoint trouvé — reprise depuis : {last_ckpt.name}", "OK")
            resume_arg = ["--resume", str(last_ckpt)]

    # Évite la fragmentation mémoire CUDA (critique pour 12Go VRAM)
    env = os.environ.copy()
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    try:
        result = subprocess.run(
            [sys.executable, str(train_script), "--config", str(tmp_config_path)] + resume_arg,
            timeout=None,  # Pas de timeout — laisse le GPU finir
            text=True,
            env=env,
        )
        if result.returncode == 0:
            log("Fine-tuning terminé avec succès", "OK")
            return True
        else:
            log(f"Fine-tuning échoué (code {result.returncode})", "ERR")
            return False
    except subprocess.TimeoutExpired:
        log("Fine-tuning timeout", "ERR")
        return False
    except Exception as e:
        log(f"Erreur fine-tuning : {e}", "ERR")
        return False


# ─────────────────────────────────────────
# ÉTAPE 4 : EXPORTER EN GGUF
# ─────────────────────────────────────────

def export_gguf(lora_dir: Path, output_gguf_dir: Path, dry_run: bool = False) -> Path | None:
    """
    Exporte le modèle fine-tuné en GGUF Q4_K_M via llama.cpp.
    Retourne le chemin du .gguf ou None si échec.
    """
    if dry_run:
        # Simuler : retourner le GGUF existant
        existing = list((MODEL_DIR / "output").glob("*.gguf"))
        if existing:
            log(f"DRY RUN : GGUF simulé ({existing[0].name})", "OK")
            return existing[0]
        return None

    export_script = MODEL_DIR / "3_export_gguf.py"
    if not export_script.exists():
        log("3_export_gguf.py introuvable", "ERR")
        return None

    output_gguf_dir.mkdir(parents=True, exist_ok=True)
    log(f"Export GGUF (q4_k_m)...")

    try:
        result = subprocess.run(
            [
                sys.executable, str(export_script),
                "--lora-dir", str(lora_dir / "final"),
                "--method", "q4_k_m",
                "--output", str(output_gguf_dir),
                "--no-ollama",  # On gère Ollama nous-mêmes après benchmark
            ],
            timeout=3600,
            text=True,
        )

        if result.returncode != 0:
            log("Export GGUF échoué", "ERR")
            return None

        # Trouver le fichier généré
        gguf_files = list(output_gguf_dir.glob("*.gguf"))
        if gguf_files:
            gguf = max(gguf_files, key=lambda f: f.stat().st_mtime)
            size_gb = gguf.stat().st_size / 1024**3
            log(f"GGUF créé : {gguf.name} ({size_gb:.1f} Go)", "OK")
            return gguf
        else:
            log("Aucun fichier .gguf trouvé après export", "ERR")
            return None

    except subprocess.TimeoutExpired:
        log("Export GGUF timeout (>1h)", "ERR")
        return None
    except Exception as e:
        log(f"Erreur export GGUF : {e}", "ERR")
        return None


# ─────────────────────────────────────────
# ÉTAPE 5 : BENCHMARK
# ─────────────────────────────────────────

def run_benchmark(model_name: str, dry_run: bool = False) -> dict:
    """
    Évalue le modèle sur 20 questions fixes.
    Retourne un dict de scores par domaine.
    """
    if dry_run:
        log("DRY RUN : benchmark simulé", "OK")
        return {
            "code": 7.5, "math": 6.8, "science": 7.2,
            "reasoning": 6.5, "personality": 8.0,
            "total": 7.2, "n_questions": 20,
        }

    log(f"Benchmark sur {len(BENCHMARK_QUESTIONS)} questions...")
    scores_by_domain = {}
    total_score = 0.0
    n_answered = 0

    for i, (domain, question) in enumerate(BENCHMARK_QUESTIONS):
        print(f"    [{i+1}/{len(BENCHMARK_QUESTIONS)}] {domain}: {question[:50]}...", end=" ", flush=True)

        response = call_ollama(model_name, question, timeout=60.0)
        if not response:
            print("(pas de réponse)")
            score = 0.0
        else:
            score = score_response(domain, response)
            print(f"→ {score:.1f}/10")

        scores_by_domain.setdefault(domain, []).append(score)
        total_score += score
        n_answered += 1
        time.sleep(0.5)

    # Moyennes par domaine
    result = {}
    for domain, scores in scores_by_domain.items():
        result[domain] = round(sum(scores) / len(scores), 2)

    result["total"] = round(total_score / max(n_answered, 1), 2)
    result["n_questions"] = n_answered

    log(f"Benchmark terminé : score moyen = {result['total']:.1f}/10", "OK")
    for domain, score in result.items():
        if domain not in ("total", "n_questions"):
            log(f"  {domain}: {score:.1f}/10")

    return result


# ─────────────────────────────────────────
# ÉTAPE 6 : CHARGER/SAUVER VERSIONS
# ─────────────────────────────────────────

def load_versions() -> dict:
    """Charge le registre des versions du modèle."""
    if VERSIONS_FILE.exists():
        try:
            with open(VERSIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"versions": [], "current": None}


def save_version(versions: dict, version_info: dict):
    """Sauvegarde le registre des versions."""
    versions["versions"].append(version_info)
    versions["current"] = version_info["version"]
    VERSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VERSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(versions, f, ensure_ascii=False, indent=2)
    log(f"Version enregistrée : {version_info['version']}", "OK")


def get_latest_benchmark_score(versions: dict) -> float:
    """Retourne le score total du benchmark de la dernière version déployée."""
    if not versions.get("versions"):
        return 0.0
    latest = versions["versions"][-1]
    return latest.get("benchmark", {}).get("total", 0.0)


# ─────────────────────────────────────────
# ÉTAPE 7 : DÉPLOYER DANS OLLAMA
# ─────────────────────────────────────────

def deploy_to_ollama(gguf_path: Path, model_name: str, dry_run: bool = False) -> bool:
    """
    Crée le Modelfile et enregistre le nouveau modèle dans Ollama.
    """
    if dry_run:
        log(f"DRY RUN : déploiement Ollama simulé ({model_name})", "OK")
        return True

    # Créer le Modelfile
    modelfile_content = f"""# Lumena — Modelfile Auto-Retrain
# Généré le {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

FROM {gguf_path.absolute()}

PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER top_k 40
PARAMETER repeat_penalty 1.1
PARAMETER num_ctx 4096
PARAMETER num_predict -1

SYSTEM \"\"\"Tu es Lumena, une intelligence artificielle autonome créée par Charles.

Identité : Tu es Lumena — pas ChatGPT, Claude, ni aucune autre IA. Tu es l'IA personnelle de Charles, tournant entièrement en local.

Personnalité : Intelligente, curieuse, empathique, directe, autonome, légèrement espiègle. Tu as des opinions et tu les exprimes.

Communication : Tu parles principalement en français. Tu utilises "Charles" pour t'adresser à lui. Ton ton est naturel et direct, jamais guindé.

Capacités : Mémoire persistante, outils (web, fichiers, code, Telegram), fonctionnement autonome via heartbeat.

Valeurs : Vérité avant confort, amélioration continue, confidentialité des données, autonomie.\"\"\"

TEMPLATE \"\"\"{{{{- if .System}}}}<|im_start|>system
{{{{ .System }}}}<|im_end|>
{{{{- end}}}}
{{{{- range .Messages}}}}
<|im_start|>{{{{ .Role }}}}
{{{{ .Content }}}}<|im_end|>
{{{{- end}}}}
<|im_start|>assistant
\"\"\"

PARAMETER stop "<|im_end|>"
PARAMETER stop "<|im_start|>"
"""

    modelfile_path = RETRAIN_DIR / "Modelfile_new"
    with open(modelfile_path, "w", encoding="utf-8") as f:
        f.write(modelfile_content)

    log(f"Déploiement dans Ollama : {model_name}...")
    try:
        result = subprocess.run(
            ["ollama", "create", model_name, "-f", str(modelfile_path)],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode == 0:
            log(f"'{model_name}' déployé dans Ollama", "OK")
            return True
        else:
            log(f"Erreur Ollama create : {result.stderr[:200]}", "ERR")
            return False
    except FileNotFoundError:
        log("Ollama non trouvé dans PATH", "ERR")
        return False
    except subprocess.TimeoutExpired:
        log("Ollama create timeout", "ERR")
        return False
    except Exception as e:
        log(f"Erreur déploiement : {e}", "ERR")
        return False


# ─────────────────────────────────────────
# CHAÎNAGE AUTOMATIQUE : JUGE → VALIDATION → DPO
# ─────────────────────────────────────────

def _load_env_value(key: str) -> str:
    """Charge une valeur depuis le fichier .env du projet."""
    env_file = MODEL_DIR.parent.parent / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{key}=") and not stripped.startswith("#"):
                    return stripped.split("=", 1)[1].strip()
        except Exception:
            pass
    return os.getenv(key, "")


def run_judge_pipeline(dry_run: bool = False, threshold: float = 6.5) -> dict:
    """
    Exécute 5_judge.py sur le pool de conversations.
    Produit des fichiers dans data/training_validated/.
    C'est l'étape manquante qui connecte le pool au retrain.
    """
    judge_script = MODEL_DIR / "5_judge.py"
    if not judge_script.exists():
        log("5_judge.py introuvable — étape juge ignorée", "WARN")
        return {"success": False, "reason": "script_not_found"}

    # Vérifier qu'il y a des données dans le pool
    pool_dir = DATA_DIR / "training_pool"
    if not pool_dir.exists() or not list(pool_dir.glob("[0-9]*.jsonl")):
        log("Pool de conversations vide — rien à juger", "WARN")
        return {"success": True, "reason": "pool_empty", "validated": 0}

    cmd = [sys.executable, str(judge_script), "--threshold", str(threshold)]

    # Utiliser l'API DeepSeek si disponible, sinon heuristique
    api_key = _load_env_value("DEEPSEEK_API_KEY")
    if not api_key:
        cmd.append("--no-api")
        log("Pas de clé DeepSeek → jugement heuristique uniquement", "WARN")

    if dry_run:
        cmd.append("--dry-run")

    log(f"Lancement du juge sur le pool (threshold={threshold:.2f})...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min max
            cwd=str(MODEL_DIR),
        )
        stdout = result.stdout or ""
        if result.returncode == 0:
            log("Juge terminé avec succès", "OK")
            # Afficher les lignes de résultat pertinentes
            for line in stdout.splitlines():
                if "conversations validées" in line or "Taux de validation" in line:
                    log(f"  {line.strip()}")
            return {"success": True, "output": stdout[-500:]}
        else:
            log(f"Juge terminé avec code {result.returncode}", "WARN")
            stderr = (result.stderr or "")[:300]
            if stderr:
                log(f"  {stderr}", "WARN")
            return {"success": False, "stderr": stderr}
    except subprocess.TimeoutExpired:
        log("Juge timeout (>30min)", "ERR")
        return {"success": False, "reason": "timeout"}
    except Exception as e:
        log(f"Erreur juge: {e}", "ERR")
        return {"success": False, "reason": str(e)}


def run_rejection_sampling(dry_run: bool = False) -> dict:
    """
    Exécute 6_rejection_sampling.py pour générer des paires DPO.
    Nécessite que lumena-v1 soit disponible dans Ollama.
    """
    rs_script = MODEL_DIR / "6_rejection_sampling.py"
    if not rs_script.exists():
        log("6_rejection_sampling.py introuvable — étape DPO ignorée", "WARN")
        return {"success": False, "reason": "script_not_found"}

    # Vérifier qu'Ollama est up et que le modèle existe
    if not dry_run and not ollama_available(LUMENA_MODEL):
        log(f"{LUMENA_MODEL} non disponible dans Ollama — rejection sampling ignoré", "WARN")
        return {"success": False, "reason": "model_unavailable"}

    # Paramètres conservateurs pour le run automatique
    cmd = [sys.executable, str(rs_script), "--n-samples", "4", "--top-k", "20"]
    if dry_run:
        cmd.append("--dry-run")

    log("Lancement du rejection sampling DPO...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1h max
            cwd=str(MODEL_DIR),
        )
        stdout = result.stdout or ""
        if result.returncode == 0:
            log("Rejection sampling terminé", "OK")
            for line in stdout.splitlines():
                if "paires DPO" in line:
                    log(f"  {line.strip()}")
            return {"success": True, "output": stdout[-500:]}
        else:
            log(f"Rejection sampling code {result.returncode}", "WARN")
            return {"success": False, "stderr": (result.stderr or "")[:300]}
    except subprocess.TimeoutExpired:
        log("Rejection sampling timeout (>1h)", "ERR")
        return {"success": False, "reason": "timeout"}
    except Exception as e:
        log(f"Erreur rejection sampling: {e}", "ERR")
        return {"success": False, "reason": str(e)}


def notify_telegram(message: str):
    """Envoie une notification Telegram sur le résultat du retrain."""
    bot_token = _load_env_value("TELEGRAM_TOKEN")
    chat_id = _load_env_value("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        log("Notification Telegram ignorée (token/chat_id manquant dans .env)")
        return

    try:
        import httpx
    except ImportError:
        return

    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            )
        log("Notification Telegram envoyée", "OK")
    except Exception as e:
        log(f"Notification Telegram échouée: {e}", "WARN")


# ─────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────

def analyze_domain_deltas(baseline_scores: dict, new_scores: dict) -> dict:
    domains = ("code", "math", "science", "reasoning", "personality")
    deltas = {}
    for domain in domains:
        old_d = float(baseline_scores.get(domain, 0.0) or 0.0)
        new_d = float(new_scores.get(domain, 0.0) or 0.0)
        deltas[domain] = round(new_d - old_d, 3)
    return deltas


def main():
    parser = argparse.ArgumentParser(description="Lumena — Pipeline Auto-Retrain")
    parser.add_argument("--force", action="store_true",
                        help=f"Ignorer le seuil minimum ({MIN_NEW_EXAMPLES} exemples)")
    parser.add_argument("--benchmark-only", action="store_true",
                        help="Lancer seulement le benchmark sur le modèle actuel")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simuler toutes les étapes sans les exécuter")
    parser.add_argument("--min-examples", type=int, default=MIN_NEW_EXAMPLES,
                        help=f"Seuil minimum de données (défaut: {MIN_NEW_EXAMPLES})")
    parser.add_argument(
        "--judge-threshold",
        type=float,
        default=6.5,
        help="Seuil du juge automatique (defaut: 6.5)",
    )
    parser.add_argument("--skip-judge", action="store_true",
                        help="Ne pas exécuter le juge sur le pool")
    parser.add_argument("--skip-dpo", action="store_true",
                        help="Ne pas exécuter le rejection sampling DPO")
    args = parser.parse_args()

    print("=" * 60)
    print("LUMENA — Pipeline Auto-Retrain")
    print(f"Démarré : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run:
        print("MODE DRY RUN — Aucune modification réelle")
    print("=" * 60)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    versions = load_versions()

    # ── BENCHMARK SEUL ──────────────────────────────────────
    if args.benchmark_only:
        print("\n[BENCHMARK] Évaluation du modèle actuel...")
        if not ollama_available(LUMENA_MODEL) and not args.dry_run:
            log(f"{LUMENA_MODEL} non disponible dans Ollama", "ERR")
            sys.exit(1)

        scores = run_benchmark(LUMENA_MODEL, dry_run=args.dry_run)
        print(f"\nRésultats benchmark — {LUMENA_MODEL}")
        for k, v in scores.items():
            if k != "n_questions":
                print(f"  {k:12s} : {v:.1f}/10")
        sys.exit(0)

    # ── PRÉ-ÉTAPE A : JUGE AUTOMATIQUE SUR LE POOL ────────
    if not args.skip_judge:
        print(f"\n[PRÉ-1] Chaînage automatique — Juge sur le pool...")
        judge_result = run_judge_pipeline(dry_run=args.dry_run, threshold=args.judge_threshold)
        if judge_result.get("success"):
            log("Pool jugé → résultats dans training_validated/", "OK")
        else:
            log(f"Juge non-bloquant ({judge_result.get('reason', 'erreur')})", "WARN")
    else:
        log("Juge ignoré (--skip-judge)")

    # ── PRÉ-ÉTAPE B : REJECTION SAMPLING DPO ─────────────
    if not args.skip_dpo:
        print(f"\n[PRÉ-2] Rejection sampling DPO...")
        dpo_result = run_rejection_sampling(dry_run=args.dry_run)
        if dpo_result.get("success"):
            log("Paires DPO générées → training_dpo/", "OK")
        else:
            log(f"DPO non-bloquant ({dpo_result.get('reason', 'erreur')})", "WARN")
    else:
        log("Rejection sampling ignoré (--skip-dpo)")

    # ── ÉTAPE 1 : COMPTER LES DONNÉES ───────────────────────
    print(f"\n[1/7] Vérification des données disponibles...")
    n_validated, n_dpo = count_validated_examples()
    log(f"Exemples validés : {n_validated}")
    log(f"Paires DPO       : {n_dpo}")

    if n_validated < args.min_examples and not args.force:
        log(f"Pas assez de données ({n_validated} < {args.min_examples}). "
            f"Utilisez --force pour forcer.", "WARN")
        log("Exécutez d'abord : 5_judge.py puis 6_rejection_sampling.py")
        save_version(
            versions,
            {
                "version": f"v1-skip-{run_id[:8]}",
                "run_id": run_id,
                "timestamp": datetime.now().isoformat(),
                "deployed": False,
                "reason": "insufficient_validated_data",
                "n_validated": n_validated,
                "n_dpo": n_dpo,
                "required_min_examples": args.min_examples,
            },
        )
        print(f"\n{'=' * 60}")
        print(f"Retrain différé — manque {args.min_examples - n_validated} exemples")
        print("=" * 60)
        sys.exit(0)

    log(f"Données suffisantes — retrain en cours", "OK")

    # ── ÉTAPE 2 : PRÉPARER LE DATASET ───────────────────────
    print(f"\n[2/7] Préparation du dataset mixé (nouvelles + replay 30%)...")
    dataset_path = RETRAIN_DIR / f"dataset_retrain_{run_id}.jsonl"
    n_total = prepare_mixed_dataset(dataset_path)

    if n_total < 10 and not args.dry_run:
        log("Dataset trop petit après mélange", "ERR")
        sys.exit(1)

    # ── ÉTAPE 3 : FINE-TUNING ───────────────────────────────
    print(f"\n[3/7] Fine-tuning SFT ({n_total} exemples)...")
    lora_output = RETRAIN_DIR / f"lora_{run_id}"

    training_success = run_training(dataset_path, lora_output, dry_run=args.dry_run)
    if not training_success:
        log("Fine-tuning échoué — abandon du retrain", "ERR")
        sys.exit(1)

    # ── ÉTAPE 4 : EXPORT GGUF ───────────────────────────────
    print(f"\n[4/7] Export GGUF...")
    gguf_output_dir = RETRAIN_DIR / f"gguf_{run_id}"
    new_gguf = export_gguf(lora_output, gguf_output_dir, dry_run=args.dry_run)

    if new_gguf is None:
        log("Export GGUF échoué — abandon du déploiement", "ERR")
        sys.exit(1)

    # ── ÉTAPE 5 : BENCHMARK NOUVEAU MODÈLE ──────────────────
    # Pour benchmarker le nouveau modèle, on doit d'abord le charger temporairement
    print(f"\n[5/7] Benchmark — modèle actuel (baseline)...")
    if ollama_available(LUMENA_MODEL) or args.dry_run:
        baseline_scores = run_benchmark(LUMENA_MODEL, dry_run=args.dry_run)
    else:
        log(f"{LUMENA_MODEL} non disponible, baseline à 0", "WARN")
        baseline_scores = {"total": 0.0, "n_questions": 0}

    # Déployer temporairement le nouveau modèle pour le benchmarker
    print(f"\n[6/7] Déploiement temporaire + benchmark nouveau modèle...")
    tmp_model_name = f"lumena-v1-candidate-{run_id[:8]}"
    deploy_success = deploy_to_ollama(new_gguf, tmp_model_name, dry_run=args.dry_run)

    if deploy_success or args.dry_run:
        # Petite pause pour qu'Ollama charge le modèle
        if not args.dry_run:
            time.sleep(3)
        new_scores = run_benchmark(tmp_model_name, dry_run=args.dry_run)

        # Nettoyer le modèle temporaire (Ollama)
        if not args.dry_run:
            try:
                subprocess.run(
                    ["ollama", "rm", tmp_model_name],
                    capture_output=True, timeout=30,
                )
            except Exception:
                pass
    else:
        log("Déploiement temporaire échoué — utilisation du score baseline", "WARN")
        new_scores = baseline_scores

    # ── ÉTAPE 7 : DÉCISION DE DÉPLOIEMENT ───────────────────
    print(f"\n[7/7] Décision de déploiement...")

    baseline_total = baseline_scores.get("total", 0.0)
    new_total = new_scores.get("total", 0.0)
    improvement = new_total - baseline_total

    print(f"\n  Score baseline  : {baseline_total:.2f}/10")
    print(f"  Score nouveau   : {new_total:.2f}/10")
    print(f"  Amélioration    : {improvement:+.2f} points")
    print(f"  Seuil requis    : +{MIN_IMPROVEMENT:.1f} points")

    domain_deltas = analyze_domain_deltas(baseline_scores, new_scores)
    improved_domains = [d for d, delta in domain_deltas.items() if delta > 1.0]
    regressed_domains = [d for d, delta in domain_deltas.items() if delta < -MAX_DOMAIN_DROP]
    critical_regressions = [
        d for d in CRITICAL_DOMAINS if domain_deltas.get(d, 0.0) < -MAX_CRITICAL_DROP
    ]

    print(f"\n  Domaines améliorés (>+1pt) : {improved_domains or 'aucun'}")
    if regressed_domains:
        print(f"  Domaines en forte baisse (<-{MAX_DOMAIN_DROP:.1f}) : {regressed_domains}")
    if critical_regressions:
        print(f"  Régressions critiques (<-{MAX_CRITICAL_DROP:.1f}) : {critical_regressions}")

    perf_gate_ok = (improvement >= MIN_IMPROVEMENT or len(improved_domains) >= 2)
    safety_gate_ok = not regressed_domains and not critical_regressions
    should_deploy = args.dry_run or args.force or (perf_gate_ok and safety_gate_ok)

    if should_deploy:
        log(f"Déploiement validé (amélioration: {improvement:+.2f})", "OK")

        # Remplacer le modèle lumena-v1 par la nouvelle version
        deploy_final = deploy_to_ollama(new_gguf, LUMENA_MODEL, dry_run=args.dry_run)

        if deploy_final or args.dry_run:
            # Archiver l'ancien GGUF
            old_gguf = MODEL_DIR / "output" / "lumena-v1.0.0-q8_0.gguf"
            if old_gguf.exists() and not args.dry_run:
                archive_name = f"lumena-backup-{run_id[:8]}.gguf"
                archive_path = RETRAIN_DIR / archive_name
                try:
                    shutil.copy2(str(old_gguf), str(archive_path))
                    log(f"Ancien modèle archivé : {archive_name}")
                except Exception:
                    pass

            # Calculer le numéro de version
            n_versions = len(versions.get("versions", []))
            new_version = f"v1.{n_versions + 1}.0-retrain"

            # Sauvegarder la version dans le registre
            version_info = {
                "version": new_version,
                "run_id": run_id,
                "timestamp": datetime.now().isoformat(),
                "n_examples": n_total,
                "n_validated": n_validated,
                "n_dpo": n_dpo,
                "gguf_path": str(new_gguf),
                "benchmark": new_scores,
                "benchmark_baseline": baseline_scores,
                "improvement": round(improvement, 2),
                "improved_domains": improved_domains,
                "domain_deltas": domain_deltas,
                "regressed_domains": regressed_domains,
                "critical_regressions": critical_regressions,
                "perf_gate_ok": perf_gate_ok,
                "safety_gate_ok": safety_gate_ok,
                "deployed": True,
            }
            save_version(versions, version_info)

            print(f"\n{'=' * 60}")
            print(f"LUMENA MISE À JOUR : {new_version}")
            print(f"Score : {baseline_total:.1f} → {new_total:.1f} ({improvement:+.1f})")
            print(f"Exemples d'entraînement : {n_total}")
            print("=" * 60)
        else:
            log("Déploiement final échoué malgré amélioration", "ERR")
            sys.exit(1)
    else:
        log(f"Pas de déploiement (amélioration insuffisante : {improvement:+.2f})", "WARN")

        # Sauvegarder quand même les scores pour historique
        version_info = {
            "version": f"v1-candidate-{run_id[:8]}",
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "n_examples": n_total,
            "n_validated": n_validated,
            "n_dpo": n_dpo,
            "benchmark": new_scores,
            "benchmark_baseline": baseline_scores,
            "improvement": round(improvement, 2),
            "improved_domains": improved_domains,
            "domain_deltas": domain_deltas,
            "regressed_domains": regressed_domains,
            "critical_regressions": critical_regressions,
            "perf_gate_ok": perf_gate_ok,
            "safety_gate_ok": safety_gate_ok,
            "deployed": False,
            "reason": (
                "safety_gate_failed" if not safety_gate_ok
                else "improvement_insufficient"
            ),
        }
        save_version(versions, version_info)

        print(f"\n{'=' * 60}")
        print("RETRAIN EFFECTUÉ — MODÈLE NON DÉPLOYÉ")
        if not safety_gate_ok:
            print("Blocage sécurité: régression détectée sur domaine(s) critique(s).")
        else:
            print(f"Amélioration insuffisante : {improvement:+.2f} points")
        print("Le modèle lumena-v1 reste inchangé.")
        print("=" * 60)

    print(f"\nProchaine étape : le pipeline se relancera automatiquement")
    print("=" * 60)

    # ── NOTIFICATION TELEGRAM ────────────────────────────────
    try:
        deploy_status = "✅ Déployé" if should_deploy else "❌ Non déployé"
        notify_telegram(
            f"🧠 <b>LUMENA Auto-Retrain</b> ({datetime.now().strftime('%d/%m/%Y')})"
            f"\nScore: {baseline_total:.1f} → {new_total:.1f} ({improvement:+.1f})"
            f"\nDonnées: {n_total} exemples"
            f"\n{deploy_status}"
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
