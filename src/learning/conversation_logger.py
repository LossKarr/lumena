"""
📚 LUMENA - Collecteur de Conversations pour Auto-Apprentissage

Enregistre toutes les conversations (tous modèles confondus) dans un pool
de données d'entraînement. Chaque conversation devient un candidat potentiel
pour le pipeline de self-improvement.

v1.0.1 :
- Déduplication robuste (SHA256 contenu complet)
- Détection feedback négatif implicite (ne pas logguer les hallucinations)
- TTL : rotation automatique des fichiers > 90 jours
- Écriture atomique thread-safe

Usage dans core.py :
    from .learning.conversation_logger import queue_conversation
    queue_conversation(user_message, response, model_used, provider)
"""

import hashlib
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from loguru import logger

# Dossier de sortie : lumena/data/training_pool/
from src.utils.paths import TRAINING_POOL_DIR as _POOL_DIR
_lock = threading.Lock()

# Longueur minimale pour qu'une conversation soit retenue
_MIN_USER_LEN = 10
_MIN_RESPONSE_LEN = 20

# TTL des fichiers du pool (conversations trop vieilles → archivées)
_POOL_TTL_DAYS = 90

# Patterns de feedback négatif implicite : l'utilisateur proteste
# Si la réponse PRÉCÉDENTE était une fausse confirmation → ne pas logguer
_NEGATIVE_FEEDBACK_PATTERNS = [
    # Patterns existants
    "rien vu", "rien fait", "n'as rien", "na rien", "pas fait",
    "t'as pas", "tu n'as pas", "tu n'a pas", "re essaye", "réessaye",
    "ça marche pas", "ça marche toujours pas", "toujours pas",
    "j'ai rien vu", "je vois rien", "non ça", "non tu n'",
    # Contestation directe
    "pas du tout", "c'est faux", "n'importe quoi", "c'est pas ce que",
    "tu comprends rien", "relis ma question", "t'as rien compris",
    "c'est incorrect", "c'est pas ça", "c'est pas correct",
    # Frustration / rejet
    "ça sert à rien", "laisse tomber", "oublie", "arrête",
    "c'est nul", "tu te trompes", "c'est pas bon",
    "j'ai pas demandé ça", "c'est le contraire",
    # Feedback anglais courant
    "wrong", "that's not", "you didn't",
]


def _content_hash(user_msg: str, resp: str) -> str:
    """Hash SHA256 du contenu complet pour déduplication robuste."""
    combined = f"{user_msg.strip()}\n---\n{resp.strip()}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def _is_implicit_negative_feedback(user_msg: str) -> bool:
    """
    Détecte si le message utilisateur est un feedback négatif implicite.
    Dans ce cas, la réponse précédente était probablement une hallucination.
    On ne logge pas la réponse précédente.
    """
    user_lower = user_msg.lower()
    return any(pattern in user_lower for pattern in _NEGATIVE_FEEDBACK_PATTERNS)


def _rotate_old_pool_files():
    """
    Archive les fichiers JSONL du pool de plus de _POOL_TTL_DAYS jours.
    Déplacés dans data/training_pool/archive/
    Appelé de façon non-bloquante, max 1x par session.
    """
    try:
        cutoff = datetime.now() - timedelta(days=_POOL_TTL_DAYS)
        archive_dir = _POOL_DIR / "archive"

        for jsonl_file in sorted(_POOL_DIR.glob("[0-9]*.jsonl")):
            try:
                file_date = datetime.strptime(jsonl_file.stem, "%Y-%m-%d")
                if file_date < cutoff:
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    dest = archive_dir / jsonl_file.name
                    if not dest.exists():
                        jsonl_file.rename(dest)
                        logger.debug(f"📚 Pool archivé : {jsonl_file.name} → archive/")
            except ValueError:
                pass  # Fichier avec nom non-daté → ignorer
    except Exception as e:
        logger.debug(f"📚 Rotation pool silencieuse: {e}")


# Rotation lancée une seule fois au démarrage du module
_rotation_done = False


def queue_conversation(
    user_message: str,
    response: str,
    model_used: str = "unknown",
    provider: str = "unknown",
    skip_if_negative_feedback: bool = True,
    react_meta: Optional[dict] = None,
) -> bool:
    """
    Ajoute une conversation au pool de données d'entraînement.

    Filtres appliqués (v1.0.1) :
    - Messages trop courts ignorés
    - Commandes système ignorées (commence par /)
    - Erreurs système ignorées
    - Feedback négatif implicite → réponse marquée comme douteuse
    - Déduplication par hash SHA256 du contenu complet

    Retourne True si la conversation a été enregistrée.
    """
    global _rotation_done

    # Rotation TTL au premier appel (non-bloquant)
    if not _rotation_done:
        _rotation_done = True
        _rotate_old_pool_files()

    try:
        user_msg = (user_message or "").strip()
        resp = (response or "").strip()

        # Filtres de base
        if len(user_msg) < _MIN_USER_LEN:
            return False
        if len(resp) < _MIN_RESPONSE_LEN:
            return False
        if user_msg.startswith("/"):
            return False
        if resp.startswith("❌") and len(resp) < 100:
            return False

        # v1.0.3 : Exclure les réponses d'abort de boucle (bug outil, pas une vraie sortie)
        # Ces réponses reflètent un bug de code, pas un comportement à apprendre
        _resp_lower = resp.lower()
        if resp.startswith("⚠️") and ("boucle" in _resp_lower or "interrompue" in _resp_lower):
            return False

        # v1.0.2 : Exclure les prompts internes (probes, evals)
        # Ces messages sont tagués [INTERNAL_PROBE] ou [INTERNAL_EVAL] par ops_handlers
        if user_msg.startswith("[INTERNAL_"):
            return False

        # v1.0.1 : Détecter feedback négatif implicite
        # Si l'utilisateur proteste → la réponse actuelle est suspecte
        quality_flag = "ok"
        if skip_if_negative_feedback and _is_implicit_negative_feedback(user_msg):
            # Le message de l'utilisateur EST un feedback négatif sur la réponse précédente
            # On logge quand même mais on marque comme douteux pour le judge
            quality_flag = "negative_feedback"

        # Structurer l'entrée au format ShareGPT (compatible Unsloth)
        content_hash = _content_hash(user_msg, resp)

        # Extraire les champs pertinents de la trace ReAct (si fournie)
        # Seuls les champs utiles pour le juge sont conservés (pas la trace brute entière)
        _react_summary: Optional[dict] = None
        if react_meta and isinstance(react_meta, dict):
            _react_summary = {}
            if react_meta.get("agent_output_warning"):
                _react_summary["warning"] = str(react_meta["agent_output_warning"])[:120]
            if react_meta.get("agent_output_incomplete"):
                _react_summary["incomplete"] = True
            plan = react_meta.get("plan")
            if plan and isinstance(plan, dict):
                _react_summary["plan_total"] = plan.get("total_tasks", 0)
                _react_summary["plan_done"] = plan.get("completed_tasks", 0)
            if react_meta.get("agent_repair_attempts"):
                _react_summary["repair_attempts"] = int(react_meta["agent_repair_attempts"])

        entry = {
            "conversations": [
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": resp},
            ],
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "model_used": model_used,
                "provider": provider,
                "user_len": len(user_msg),
                "response_len": len(resp),
                "content_hash": content_hash,       # Pour déduplication downstream
                "quality_flag": quality_flag,        # "ok" | "negative_feedback"
                **({"react_meta": _react_summary} if _react_summary else {}),
            },
        }

        # Fichier quotidien : training_pool/YYYY-MM-DD.jsonl
        _POOL_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        pool_file = _POOL_DIR / f"{today}.jsonl"

        with _lock:
            # Déduplication robuste : vérifier si ce hash existe déjà dans le fichier du jour
            if pool_file.exists():
                try:
                    existing_hashes = set()
                    with open(pool_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                existing = json.loads(line)
                                h = existing.get("metadata", {}).get("content_hash", "")
                                if h:
                                    existing_hashes.add(h)
                            except Exception:
                                pass  # JSON invalide ignoré
                    if content_hash in existing_hashes:
                        logger.debug(f"📚 Doublon ignoré [{content_hash}]")
                        return False
                except Exception:
                    pass  # En cas d'erreur lecture, on logge quand même

            with open(pool_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        status = "⚠️" if quality_flag != "ok" else "✓"
        logger.debug(f"📚 {status} Conversation loguée [{model_used}] [{quality_flag}] → {pool_file.name}")
        return True

    except Exception as e:
        logger.debug(f"conversation_logger silencieux: {e}")
        return False


def get_pool_stats() -> dict:
    """Retourne les statistiques du pool de données."""
    try:
        if not _POOL_DIR.exists():
            return {"total_files": 0, "total_conversations": 0, "files": []}

        files_info = []
        total = 0
        for jsonl_file in sorted(_POOL_DIR.glob("*.jsonl")):
            count = 0
            try:
                with open(jsonl_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            count += 1
            except Exception:
                pass  # fichier illisible
            total += count
            files_info.append({"date": jsonl_file.stem, "count": count})

        return {
            "total_files": len(files_info),
            "total_conversations": total,
            "pool_dir": str(_POOL_DIR),
            "files": files_info,
        }
    except Exception as e:
        return {"error": str(e)}


def export_pool_as_jsonl(output_path: Optional[Path] = None) -> Path:
    """
    Exporte tout le pool en un seul fichier JSONL pour le training.
    Retourne le chemin du fichier exporté.
    """
    output_path = output_path or _POOL_DIR / "all_conversations.jsonl"
    count = 0
    seen = set()  # Déduplication simple par hash contenu

    try:
        with open(output_path, "w", encoding="utf-8") as out:
            for jsonl_file in sorted(_POOL_DIR.glob("[0-9]*.jsonl")):
                try:
                    with open(jsonl_file, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                entry = json.loads(line)
                                meta = entry.get("metadata", {})

                                # v1.0.1 : exclure les conversations avec feedback négatif
                                if meta.get("quality_flag") == "negative_feedback":
                                    continue

                                # Déduplication par content_hash (v1.0.1) ou fallback ancien hash
                                content_hash = meta.get("content_hash")
                                if content_hash:
                                    key = content_hash
                                else:
                                    convs = entry.get("conversations", [])
                                    key = hash(
                                        str(convs[0].get("content", ""))
                                        + str(convs[-1].get("content", ""))
                                    )

                                if key in seen:
                                    continue
                                seen.add(key)
                                out.write(line + "\n")
                                count += 1
                            except Exception:
                                continue  # entrée invalide
                except Exception:
                    continue  # fichier illisible

        logger.info(f"📚 Pool exporté : {count} conversations → {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"Erreur export pool: {e}")
        return output_path
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
