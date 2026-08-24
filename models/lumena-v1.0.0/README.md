# Lumena v1.0.0 — Cerveau Fine-Tuné

> Modèle LLM personnalisé pour l'IA Lumena, basé sur Qwen3-8B avec QLoRA.
> Pipeline complet : dataset → fine-tuning → export GGUF → Ollama.

---

## Configuration matérielle requise

| Composant | Minimum | Recommandé |
|-----------|---------|------------|
| GPU VRAM | 10 Go | 12 Go (RTX 3060) |
| RAM | 16 Go | 32 Go |
| Stockage | 20 Go | 50 Go |
| Python | 3.10+ | 3.11 |

---

## Architecture

```
Base : Qwen3-8B (Qwen/Qwen3-8B sur HuggingFace)
Technique : QLoRA (4-bit) + Unsloth
Format export : GGUF Q4_K_M (pour Ollama)
```

### Pourquoi Qwen3-8B ?

- **Sorti mai 2025** : architecture plus récente que Qwen2.5
- **Outperforme Qwen2.5-14B** sur 15 benchmarks tout en étant 2x plus petit
- **Tool use natif** : conçu pour les agents et function calling
- **Mode thinking** : raisonnement amélioré sur tâches complexes
- **Apache 2.0** : licence libre pour usage commercial et fine-tuning
- **GGUF disponible** : export direct pour Ollama
- **Multilingue** : français excellent nativement

### Hyperparamètres QLoRA (optimisés RTX 3060 12Go)

| Paramètre | Valeur | Raison |
|-----------|--------|--------|
| `max_seq_length` | 4096 | Tient en 12Go VRAM avec QLoRA |
| `load_in_4bit` | True | QLoRA — réduit VRAM de 70% |
| `r` (rank LoRA) | 32 | Plus élevé que v0 (16) = meilleure qualité |
| `lora_alpha` | 64 | Standard : 2 × rank |
| `lora_dropout` | 0.05 | Légère régularisation |
| `learning_rate` | 2e-4 | Standard pour fine-tuning instruct |
| `num_train_epochs` | 3 | Équilibre apprentissage/overfitting |
| `batch_size` | 2 | Limite VRAM |
| `gradient_accumulation` | 4 | Effective batch = 8 |
| `warmup_ratio` | 0.1 | 10% du training en warmup |
| `lr_scheduler` | cosine | Décroissance progressive |

---

## Structure du projet

```
lumena-v1.0.0/
├── README.md                    # Ce fichier
├── requirements.txt             # Dépendances Python
├── config.yaml                  # Configuration centralisée
│
├── 1_prepare_data.py            # Génère le dataset d'entraînement
├── 2_train.py                   # Fine-tuning QLoRA avec Unsloth
├── 3_export_gguf.py             # Export GGUF + création Modelfile Ollama
├── 4_evaluate.py                # Évaluation du modèle
│
├── Modelfile                    # Ollama Modelfile pour lumena-v1
│
└── data/
    ├── lumena_personality.jsonl    # Identité, personnalité, valeurs
    ├── lumena_tool_use.jsonl       # Exemples d'utilisation d'outils
    ├── lumena_conversations.jsonl  # Conversations générales
    └── lumena_reasoning.jsonl      # Raisonnement complexe
```

---

## Pipeline complet

### Étape 1 : Installation

```bash
pip install -r requirements.txt
```

### Étape 2 : Préparer les données

```bash
python 1_prepare_data.py
```

Génère les fichiers JSONL dans `data/` au format ShareGPT (compatible Unsloth).
Optionnel : extraire les souvenirs depuis ChromaDB de Lumena.

### Étape 3 : Fine-tuning

```bash
python 2_train.py
```

Durée estimée : 2-4 heures sur RTX 3060 12Go selon la taille du dataset.
Le modèle LoRA est sauvegardé dans `output/lumena-lora/`.

### Étape 4 : Export GGUF + Ollama

```bash
python 3_export_gguf.py
```

Crée `output/lumena-v1.0.0.Q4_K_M.gguf` et le `Modelfile` Ollama.

```bash
ollama create lumena-v1 -f Modelfile
```

### Étape 5 : Évaluation

```bash
python 4_evaluate.py
```

---

## Format des données (ShareGPT)

```json
{
  "conversations": [
    {
      "from": "system",
      "value": "Tu es Lumena, une IA autonome créée par Charles..."
    },
    {
      "from": "human",
      "value": "Qui es-tu ?"
    },
    {
      "from": "gpt",
      "value": "Je suis Lumena, ton assistante IA personnelle..."
    }
  ]
}
```

---

## Comparaison v0 → v1

| Aspect | lumena-lora (v0) | lumena-v1.0.0 |
|--------|------------------|----------------|
| Base | Qwen2.5-3B | Qwen3-8B |
| Paramètres | 3B | 8B |
| LoRA rank | 16 | 32 |
| Quantization | FP16 | 4-bit QLoRA |
| Steps | 100 | 300+ |
| Dataset | ~44 exemples | 500+ exemples |
| Tool use | Basique | Avancé (natif Qwen3) |
| Thinking mode | Non | Oui (via `<think>`) |
| Export Ollama | Manuel | Automatisé |

---

## Notes techniques

- Le modèle Qwen3-8B supporte le "thinking mode" via `<think>` — activé pour les tâches complexes, désactivé pour la conversation normale via `/no_think`
- Le tokenizer Qwen3 est différent de Qwen2.5 — ne pas mélanger les checkpoints
- Pour les tool use : Qwen3 utilise nativement le format function calling JSON
- GGUF Q4_K_M = meilleur compromis qualité/taille pour 12Go VRAM

---

*Lumena v1.0.0 — Cerveau IA — Créé le 18 février 2026*
