"""
Centralized path constants for Lumena.

ALL directory references to data/, workspace/, logs/, backups/, etc.
MUST go through this module.  Never write ``Path(...) / "data"`` elsewhere.

Every path reads an env-var with a sane default, so multi-instance
deployments just set the matching ``LUMENA_*`` variable.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

# ── Project root ────────────────────────────────────────────────────────────
ROOT_DIR: Path = Path(__file__).resolve().parent.parent.parent

# ── Instance identity ───────────────────────────────────────────────────────
INSTANCE_ID: str = os.getenv("LUMENA_INSTANCE_ID", "default")
INSTANCE_NAME: str = os.getenv("LUMENA_INSTANCE_NAME", "Lumena")

# ── Top-level directories ──────────────────────────────────────────────────
DATA_DIR: Path = Path(os.getenv("LUMENA_DATA_DIR", str(ROOT_DIR / "data")))
WORKSPACE_DIR: Path = Path(os.getenv("LUMENA_WORKSPACE_DIR", str(ROOT_DIR / "workspace")))
LOGS_DIR: Path = Path(os.getenv("LUMENA_LOGS_DIR", str(DATA_DIR / "logs")))
BACKUPS_DIR: Path = Path(os.getenv("LUMENA_BACKUPS_DIR", str(ROOT_DIR / "backups")))

# ── Data sub-directories ───────────────────────────────────────────────────
MEMORY_DIR: Path = DATA_DIR / "memory"
JOURNAL_DIR: Path = MEMORY_DIR / "journal"
VECTOR_DIR: Path = DATA_DIR / "vector"
OPS_DIR: Path = DATA_DIR / "ops"
PLANS_DIR: Path = DATA_DIR / "plans"
ALERTS_DIR: Path = DATA_DIR / "alerts"
MAIL_DIR: Path = DATA_DIR / "mail"
CRAWLER_DIR: Path = DATA_DIR / "crawler"
SCREENSHOTS_DIR: Path = DATA_DIR / "screenshots"
BROWSER_PROFILES_DIR: Path = DATA_DIR / "browser_profiles"
BROWSER_TRACES_DIR: Path = DATA_DIR / "browser_traces"
RECEIVED_IMAGES_DIR: Path = DATA_DIR / "received_images"
GENERATED_IMAGES_DIR: Path = Path(os.getenv("LUMENA_GENERATED_IMAGES_DIR", str(WORKSPACE_DIR / "images")))
RECEIVED_DOCS_DIR: Path = Path(os.getenv("LUMENA_UPLOADS_DIR", str(DATA_DIR / "received_documents")))
CUSTOM_HANDLERS_DIR: Path = DATA_DIR / "custom_handlers"
CODE_INDEX_DIR: Path = DATA_DIR / "code_index"
TRAINING_POOL_DIR: Path = DATA_DIR / "training_pool"
TRAINING_VALIDATED_DIR: Path = DATA_DIR / "training_validated"
MODELS_DIR: Path = ROOT_DIR / "models"
LUMENA_MODELS_DIR: Path = MODELS_DIR / "lumena-v1.0.0"
FINETUNED_MODELS_DIR: Path = MODELS_DIR / "finetuned"
SCHEDULER_DIR: Path = DATA_DIR / "scheduler"
LEARNING_DIR: Path = DATA_DIR / "learning"
REFLECTION_DIR: Path = DATA_DIR / "reflection"
AUTONOMY_DIR: Path = DATA_DIR / "autonomy"
INSTALLED_SKILLS_DIR: Path = DATA_DIR / "installed_skills"
CAPTURES_DIR: Path = DATA_DIR / "captures"
CHROMADB_DIR: Path = DATA_DIR / "chromadb"
TEMPLATES_DIR: Path = ROOT_DIR / "assets" / "templates"

# ── Well-known files ────────────────────────────────────────────────────────
JOURNAL_JSON: Path = DATA_DIR / "journal.json"
HEARTBEAT_STATE_JSON: Path = DATA_DIR / "heartbeat_state.json"
MEMORY_MD: Path = DATA_DIR / "MEMORY.md"
IDENTITY_JSON: Path = MEMORY_DIR / "identity.json"
FACTS_JSON: Path = MEMORY_DIR / "facts.json"
NETWORK_REGISTRY_JSON: Path = DATA_DIR / "network_registry.json"
SKILLS_MANIFEST_JSON: Path = DATA_DIR / "skills_sync_manifest.json"
LAST_TEST_RESULT_JSON: Path = DATA_DIR / "last_test_result.json"
TG_MODE_STATE_JSON: Path = DATA_DIR / "tg_mode_state.json"
OPS_STATE_JSON: Path = OPS_DIR / "ops_state.json"
APIS_REGISTRY_JSON: Path = DATA_DIR / "apis_registry.json"
FINETUNED_REGISTRY: Path = MEMORY_DIR / "finetuned_models.json"
EMOTION_STATE_FILE: Path = DATA_DIR / "emotion_state.json"
EMOTION_HISTORY_FILE: Path = DATA_DIR / "emotion_history.jsonl"


# ── Instance ID auto-generation ────────────────────────────────────────────

def ensure_instance_id(env_file: Path | None = None) -> str:
    """Retourne INSTANCE_ID, en le générant et l'écrivant dans .env si absent.

    Appelé au démarrage (lifespan / initialize_lumena).  Thread-safe via un
    verrou fichier léger (rename atomique).  Ne lève jamais d'exception.
    """
    global INSTANCE_ID
    current = os.getenv("LUMENA_INSTANCE_ID", "").strip()
    if current and current != "default":
        INSTANCE_ID = current
        return current

    new_id = str(uuid.uuid4())
    os.environ["LUMENA_INSTANCE_ID"] = new_id
    INSTANCE_ID = new_id

    # Persister dans .env (si accessible)
    target = env_file or (ROOT_DIR / ".env")
    try:
        if target.exists():
            text = target.read_text(encoding="utf-8", errors="replace")
            if "LUMENA_INSTANCE_ID" not in text:
                # Append à la fin du fichier
                sep = "" if text.endswith("\n") else "\n"
                target.write_text(
                    text + sep + f"LUMENA_INSTANCE_ID={new_id}\n",
                    encoding="utf-8",
                )
        else:
            target.write_text(f"LUMENA_INSTANCE_ID={new_id}\n", encoding="utf-8")
    except Exception:
        pass  # Échec d'écriture non bloquant

    return new_id


# ── Directory bootstrap ────────────────────────────────────────────────────

_CRITICAL_DIRS: tuple[Path, ...] = (
    DATA_DIR,
    WORKSPACE_DIR,
    LOGS_DIR,
    BACKUPS_DIR,
    OPS_DIR,
    MEMORY_DIR,
    JOURNAL_DIR,
    ALERTS_DIR,
    MAIL_DIR,
    RECEIVED_IMAGES_DIR,
    GENERATED_IMAGES_DIR,
    RECEIVED_DOCS_DIR,
    TRAINING_VALIDATED_DIR,
    FINETUNED_MODELS_DIR,
)


def validate_instance_dirs(*, create: bool = True) -> list[str]:
    """Crée (si ``create=True``) et valide les répertoires critiques.

    Retourne la liste des répertoires qui n'ont pas pu être créés/vérifiés.
    Ne lève jamais d'exception.
    """
    errors: list[str] = []
    for d in _CRITICAL_DIRS:
        try:
            if create:
                d.mkdir(parents=True, exist_ok=True)
            if not d.exists():
                errors.append(str(d))
        except Exception as exc:
            errors.append(f"{d}: {exc}")
    return errors
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
