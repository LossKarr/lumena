"""
Utilitaires de persistance JSON atomique pour Lumena.

API publique:
    atomic_write_json(path, data, *, update_catalogue=False)
    atomic_write_text(path, content)
    safe_read_json(path, default=None, *, quarantine=True)

Principe:
    - Ecriture toujours via tmp + rename (atomique sur POSIX et Windows NTFS)
    - Lecture avec fallback: JSON corrompu -> quarantaine + retour default
    - Crochet update_catalogue reserve pour P1.3.4 (no-op aujourd'hui)

P1.3.1 - session 17/03/2026
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Lock global protecting the catalogue stub (no-op until P1.3.4)
_catalogue_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Ecriture atomique
# ---------------------------------------------------------------------------

def atomic_write_json(
    path: Path,
    data: Dict[str, Any],
    *,
    update_catalogue: bool = False,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """Ecrit ``data`` dans ``path`` de maniere atomique (tmp -> rename).

    Garantit qu'un crash pendant l'ecriture ne laisse pas un fichier
    partiellement ecrit: l'ancien contenu reste intact jusqu'au rename final.

    Args:
        path: Chemin cible (Path). Le repertoire parent est cree si absent.
        data: Dict serialisable en JSON.
        update_catalogue: Reserved pour P1.3.4. No-op aujourd'hui.
        indent: Indentation JSON (defaut 2).
        ensure_ascii: Si True, echappe les caracteres non-ASCII.

    Raises:
        ValueError: Si ``data`` n'est pas serialisable en JSON.
        OSError: Si l'ecriture ou le rename echoue (permissions, espace disque).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    serialized = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)

    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(serialized, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        # Nettoyer le fichier tmp si le rename a echoue
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # tmp cleanup best-effort
        raise

    if update_catalogue:
        _update_catalogue(path, data)


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Ecrit ``content`` dans ``path`` de maniere atomique (tmp -> rename).

    Meme garantie qu'``atomic_write_json``: un crash pendant l'ecriture
    ne laisse pas un fichier partiellement ecrit.

    Args:
        path: Chemin cible (Path). Le repertoire parent est cree si absent.
        content: Contenu texte a ecrire.
        encoding: Encodage (defaut utf-8).

    Raises:
        OSError: Si l'ecriture ou le rename echoue.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    suffix = path.suffix + ".tmp" if path.suffix else ".tmp"
    tmp = path.with_suffix(suffix)
    try:
        tmp.write_text(content, encoding=encoding)
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # tmp cleanup best-effort
        raise


# ---------------------------------------------------------------------------
# Lecture avec quarantaine
# ---------------------------------------------------------------------------

def safe_read_json(
    path: Path,
    default: Optional[Dict[str, Any]] = None,
    *,
    quarantine: bool = True,
) -> Dict[str, Any]:
    """Lit un fichier JSON avec fallback propre en cas de corruption.

    En cas de JSON invalide:
    - Si ``quarantine=True``: deplace le fichier corrompu dans un sous-dossier
      ``.quarantine/`` avec horodatage, pour diagnostic ulterieur.
    - Log un WARNING dans tous les cas.
    - Retourne ``default`` (dict vide par defaut).

    Args:
        path: Chemin du fichier JSON a lire.
        default: Valeur retournee si le fichier est absent ou corrompu.
        quarantine: Si True, archive le fichier corrompu avant de retourner default.

    Returns:
        Le contenu parse, ou ``default`` si absent/corrompu.
    """
    path = Path(path)
    if default is None:
        default = {}

    if not path.exists():
        return default

    raw: str = ""
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "[persistence] JSON corrompu dans %s: %s — retour du defaut%s",
            path,
            exc,
            " + quarantaine" if quarantine else "",
        )
        if quarantine and raw:
            _quarantine_file(path, raw)
        return default
    except OSError as exc:
        logger.warning("[persistence] Impossible de lire %s: %s", path, exc)
        return default


# ---------------------------------------------------------------------------
# Quarantaine interne
# ---------------------------------------------------------------------------

def _quarantine_file(path: Path, raw_content: str) -> None:
    """Deplace un fichier JSON corrompu dans .quarantine/ avec horodatage.

    Copie le contenu corrompu dans .quarantine/<stem>_<ts>.json,
    puis supprime l'original. Si la suppression echoue, le fichier reste
    (comportement safe: on ne perd pas de donnees) mais une warning est loggee.
    """
    try:
        quarantine_dir = path.parent / ".quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = quarantine_dir / f"{path.stem}_{ts}{path.suffix}"
        dest.write_text(raw_content, encoding="utf-8")
        logger.info("[persistence] Fichier corrompu archive: %s", dest)
    except Exception as exc:
        logger.warning("[persistence] Echec quarantaine pour %s: %s", path, exc)
        return
    # Supprimer l'original apres avoir sauvegarde la copie quarantaine.
    # Sans ca, chaque appel suivant a safe_read_json() re-echouerait
    # et genererait une nouvelle copie en boucle.
    try:
        path.unlink()
    except OSError as exc:
        logger.warning("[persistence] Impossible de supprimer l'original %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Crochet catalogue — reserve P1.3.4 (no-op)
# ---------------------------------------------------------------------------

def _update_catalogue(path: Path, data: Dict[str, Any]) -> None:  # noqa: ARG001
    """No-op jusqu'a P1.3.4.

    Sera active pour mettre a jour l'index central des fichiers d'etat
    (checksum, updated_at, version, statut recovery) sans bloquer le
    chemin critique d'ecriture.
    """
    pass
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
