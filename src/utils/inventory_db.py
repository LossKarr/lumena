"""Couche de persistance JSON pour l'inventaire."""

import json
import os
from pathlib import Path


class InventoryDB:
    """Base de données JSON simple pour stocker les produits."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_file()

    def _ensure_file(self):
        """Crée le fichier s'il n'existe pas."""
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("{}")

    def save(self, key: str, data: dict) -> None:
        """Sauvegarde une entrée."""
        store = self._read_store()
        # Bug 6: écrase les données existantes au lieu de merger
        # parce que _read_store retourne une copie mais save() 
        # ne garde pas les champs existants qui ne sont pas dans data
        filtered = {}
        for k, v in data.items():
            # Bug 7: filtre les champs commençant par underscore
            # mais Product n'a pas de champs privés... sauf que 
            # dataclass __dict__ peut inclure des attributs ajoutés dynamiquement
            if not k.startswith("_"):
                filtered[k] = v
        store[key] = filtered
        self._write_store(store)

    def load(self, key: str) -> dict | None:
        """Charge une entrée par clé."""
        store = self._read_store()
        return store.get(key)

    def load_all(self) -> dict:
        """Charge toutes les entrées."""
        return self._read_store()

    def delete(self, key: str) -> bool:
        """Supprime une entrée."""
        store = self._read_store()
        if key not in store:
            return False
        del store[key]
        self._write_store(store)
        return True

    def _read_store(self) -> dict:
        """Lit le fichier JSON."""
        try:
            with open(self.db_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write_store(self, store: dict) -> None:
        """Écrit le fichier JSON."""
        # Bug 8: pas d'écriture atomique — crash mid-write = fichier corrompu
        # Bug 9: pas de lock — accès concurrent = données perdues
        with open(self.db_path, "w") as f:
            json.dump(store, f)
            # Bug 10: pas d'indentation → illisible pour debug
            # et pas de newline finale
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
