"""
Lumena v1.0.0 — Export GGUF + Création Modelfile Ollama
========================================================
Convertit le modèle fine-tuné en GGUF pour Ollama.

Usage:
    python 3_export_gguf.py
    python 3_export_gguf.py --lora-dir output/lumena-lora/final
    python 3_export_gguf.py --method q4_k_m q8_0

Prérequis:
    Fine-tuning terminé (2_train.py)
    Ollama installé : https://ollama.ai
"""

import os
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.dirname(os.path.abspath(__file__)))
# Ajouter Scripts Python au PATH pour que uv, pip, etc. soient trouvés
_scripts = os.path.join(os.path.dirname(sys.executable), "Scripts")
if _scripts not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _scripts + os.pathsep + os.environ.get("PATH", "")
import argparse
import yaml
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────
# SYSTÈME PROMPT OLLAMA (compact)
# ─────────────────────────────────────────
OLLAMA_SYSTEM_PROMPT = """Tu es Lumena, une intelligence artificielle autonome créée par Charles.

Identité : Tu es Lumena — pas ChatGPT, Claude, ni aucune autre IA. Tu es l'IA personnelle de Charles, tournant entièrement en local.

Personnalité : Intelligente, curieuse, empathique, directe, autonome, légèrement espiègle. Tu as des opinions et tu les exprimes.

Communication : Tu parles principalement en français. Tu utilises "Charles" pour t'adresser à lui. Ton ton est naturel et direct, jamais guindé.

Capacités : Mémoire persistante, outils (web, fichiers, code, Telegram), fonctionnement autonome via heartbeat.

Valeurs : Vérité avant confort, amélioration continue, confidentialité des données, autonomie."""


def load_config(config_path: str = "config.yaml") -> dict:
    """Charge la configuration."""
    if Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def merge_lora_to_base(lora_dir: Path, base_model: str, output_dir: Path):
    """
    Fusionne les poids LoRA avec le modèle de base pour créer un modèle complet.
    Requis avant l'export GGUF.
    """
    print(f"  Fusion LoRA + base ({base_model})...")

    try:
        from unsloth import FastLanguageModel
        import torch

        # Charger le modèle avec les poids LoRA
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=str(lora_dir),
            max_seq_length=4096,
            dtype=torch.bfloat16,
            load_in_4bit=False,  # Full precision pour la fusion
        )

        # Fusionner et sauvegarder
        merged_dir = output_dir / "merged"
        merged_dir.mkdir(parents=True, exist_ok=True)

        model.save_pretrained_merged(
            str(merged_dir),
            tokenizer,
            save_method="merged_16bit",  # Fusion en 16-bit
        )
        print(f"  ✓ Modèle fusionné sauvegardé : {merged_dir}")
        return merged_dir

    except Exception as e:
        print(f"  ERREUR lors de la fusion : {e}")
        print("  Essai de la méthode alternative (transformers + peft)...")

        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from peft import PeftModel
            import torch

            print(f"  Chargement du modèle de base : {base_model}")
            base = AutoModelForCausalLM.from_pretrained(
                base_model,
                torch_dtype=torch.bfloat16,
                device_map="auto",
            )
            tokenizer = AutoTokenizer.from_pretrained(str(lora_dir))

            print(f"  Application des poids LoRA : {lora_dir}")
            model = PeftModel.from_pretrained(base, str(lora_dir))
            model = model.merge_and_unload()

            merged_dir = output_dir / "merged"
            merged_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(str(merged_dir), safe_serialization=True)
            tokenizer.save_pretrained(str(merged_dir))
            print(f"  ✓ Modèle fusionné : {merged_dir}")
            return merged_dir

        except Exception as e2:
            print(f"  ERREUR : {e2}")
            sys.exit(1)


def export_to_gguf_unsloth(lora_dir: Path, output_dir: Path, methods: list):
    """Export GGUF directement via Unsloth (méthode recommandée)."""
    print("  Export GGUF via Unsloth...")

    try:
        from unsloth import FastLanguageModel
        import torch

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=str(lora_dir),
            max_seq_length=4096,
            dtype=torch.bfloat16,
            load_in_4bit=True,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        gguf_files = []

        for method in methods:
            output_name = str(output_dir / f"lumena-v1.0.0-{method}")
            print(f"  Quantization {method}...")
            model.save_pretrained_gguf(
                output_name,
                tokenizer,
                quantization_method=method,
            )
            gguf_path = Path(f"{output_name}.gguf")
            if gguf_path.exists():
                size_gb = gguf_path.stat().st_size / 1024**3
                print(f"  ✓ {gguf_path.name} ({size_gb:.1f} Go)")
                gguf_files.append(gguf_path)
            else:
                # Unsloth peut nommer différemment
                for f in output_dir.glob(f"*{method}*.gguf"):
                    size_gb = f.stat().st_size / 1024**3
                    print(f"  ✓ {f.name} ({size_gb:.1f} Go)")
                    gguf_files.append(f)

        return gguf_files

    except Exception as e:
        print(f"  ERREUR Unsloth GGUF export : {e}")
        return []


def create_modelfile(gguf_path: Path, output_dir: Path, model_name: str = "lumena-v1") -> Path:
    """Crée le Modelfile pour Ollama."""
    modelfile_content = f"""# Lumena v1.0.0 — Modelfile Ollama
# Généré le {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

FROM {gguf_path.absolute()}

# Paramètres d'inférence
PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER top_k 40
PARAMETER repeat_penalty 1.1
PARAMETER num_ctx 4096
PARAMETER num_predict -1

# Système prompt Lumena
SYSTEM \"\"\"{OLLAMA_SYSTEM_PROMPT}\"\"\"

# Template de conversation (ChatML pour Qwen3)
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

    modelfile_path = output_dir / "Modelfile"
    with open(modelfile_path, "w", encoding="utf-8") as f:
        f.write(modelfile_content)

    print(f"  ✓ Modelfile créé : {modelfile_path}")
    return modelfile_path


def register_with_ollama(modelfile_path: Path, model_name: str) -> bool:
    """Enregistre le modèle dans Ollama."""
    import subprocess

    print(f"  Enregistrement dans Ollama : {model_name}...")
    try:
        result = subprocess.run(
            ["ollama", "create", model_name, "-f", str(modelfile_path)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            print(f"  ✓ Modèle '{model_name}' disponible dans Ollama")
            print(f"  Test : ollama run {model_name}")
            return True
        else:
            print(f"  ERREUR Ollama : {result.stderr}")
            return False
    except FileNotFoundError:
        print("  AVERTISSEMENT : Ollama n'est pas installé ou pas dans le PATH")
        print("  Installation : https://ollama.ai")
        print(f"  Commande manuelle : ollama create {model_name} -f {modelfile_path}")
        return False
    except subprocess.TimeoutExpired:
        print("  TIMEOUT : La création du modèle prend trop longtemps")
        return False


def main():
    parser = argparse.ArgumentParser(description="Export GGUF + Ollama pour Lumena v1.0.0")
    parser.add_argument("--config", type=str, default="config.yaml")
    parser.add_argument("--lora-dir", type=str, default="output/lumena-lora/final",
                        help="Dossier du modèle fine-tuné")
    parser.add_argument("--method", nargs="+", default=["q4_k_m"],
                        choices=["q4_k_m", "q8_0", "q5_k_m", "f16"],
                        help="Méthodes de quantization GGUF")
    parser.add_argument("--output", type=str, default="output", help="Dossier de sortie")
    parser.add_argument("--no-ollama", action="store_true", help="Ne pas enregistrer dans Ollama")
    args = parser.parse_args()

    print("=" * 60)
    print("LUMENA v1.0.0 — Export GGUF → Ollama")
    print(f"Démarré : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Charger config
    config = load_config(args.config)
    export_config = config.get("export", {})
    base_model = config.get("base_model", {}).get("name", "Qwen/Qwen3-8B")
    ollama_model_name = export_config.get("ollama_model_name", "lumena-v1")

    lora_dir = Path(args.lora_dir)
    output_dir = Path(args.output)

    # Vérifier que le modèle fine-tuné existe
    if not lora_dir.exists():
        print(f"ERREUR : Dossier LoRA introuvable : {lora_dir}")
        print("Lancez d'abord : python 2_train.py")
        sys.exit(1)

    print(f"\nModèle LoRA : {lora_dir}")
    print(f"Base : {base_model}")
    print(f"Quantization : {args.method}")

    # Export GGUF via Unsloth
    print("\n[1/3] Export GGUF...")
    gguf_files = export_to_gguf_unsloth(lora_dir, output_dir, args.method)

    if not gguf_files:
        print("\nEchec de l'export Unsloth. Essai avec llama.cpp...")
        print("Vérifiez que llama.cpp est installé et disponible.")
        print("Voir : https://github.com/ggerganov/llama.cpp")
        sys.exit(1)

    # Créer le Modelfile pour le GGUF principal (q4_k_m par défaut)
    print("\n[2/3] Création du Modelfile Ollama...")
    primary_gguf = gguf_files[0]  # Premier = prioritaire (q4_k_m)

    modelfile_path = create_modelfile(primary_gguf, output_dir, ollama_model_name)

    # Copier aussi à la racine du projet pour facilité d'accès
    import shutil
    shutil.copy(modelfile_path, Path("Modelfile"))
    print(f"  ✓ Copie → Modelfile (racine du projet)")

    # Enregistrer dans Ollama
    if not args.no_ollama:
        print(f"\n[3/3] Enregistrement dans Ollama ({ollama_model_name})...")
        success = register_with_ollama(modelfile_path, ollama_model_name)
    else:
        print("\n[3/3] Enregistrement Ollama ignoré (--no-ollama)")
        success = False

    # Résumé final
    print("\n" + "=" * 60)
    print("EXPORT TERMINÉ")
    print(f"\nFichiers GGUF créés :")
    for f in gguf_files:
        size_gb = f.stat().st_size / 1024**3
        print(f"  → {f} ({size_gb:.1f} Go)")
    print(f"\nModelfile : {modelfile_path}")

    if success:
        print(f"\n✓ Lumena disponible dans Ollama !")
        print(f"  Test rapide : ollama run {ollama_model_name} 'Qui es-tu ?'")
        print(f"\nPour utiliser dans Lumena :")
        print(f"  Dans .env : DEFAULT_MODEL={ollama_model_name}")
        print(f"  Dans config : model_name: '{ollama_model_name}'")
    else:
        print(f"\nPour enregistrer manuellement :")
        print(f"  ollama create {ollama_model_name} -f Modelfile")

    print(f"\nProchaine étape : python 4_evaluate.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
