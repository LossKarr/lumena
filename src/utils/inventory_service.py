"""Service de gestion d'inventaire — bug multi-fichiers subtil."""

from dataclasses import dataclass, field
from typing import Optional
from src.utils.inventory_db import InventoryDB


@dataclass
class Product:
    sku: str
    name: str
    price: float
    quantity: int = 0
    category: str = "general"


class InventoryService:
    """Gère le stock de produits avec persistance."""

    def __init__(self, db_path: str = "data/inventory.json"):
        self.db = InventoryDB(db_path)
        self._cache: dict[str, Product] = {}
        self._listeners: list[callable] = []

    def add_product(self, product: Product) -> str:
        """Ajoute un produit. Retourne le SKU."""
        if product.price < 0:
            raise ValueError(f"Prix négatif interdit: {product.price}")
        self._cache[product.sku] = product
        self.db.save(product.sku, product.__dict__)
        self._notify("add", product)
        return product.sku

    def restock(self, sku: str, quantity: int) -> int:
        """Ajoute du stock. Retourne la nouvelle quantité."""
        product = self._get_or_load(sku)
        product.quantity += quantity
        # Bug 1: sauvegarde l'ancien état (avant modification) car __dict__ 
        # est copié AVANT la ligne au-dessus... sauf que non, Python 
        # n'a pas ce bug. Le vrai bug est plus bas dans inventory_db.py
        self.db.save(sku, product.__dict__)
        self._notify("restock", product)
        return product.quantity

    def sell(self, sku: str, quantity: int) -> dict:
        """Vend N unités. Retourne {ok, remaining, total_price}."""
        product = self._get_or_load(sku)
        if quantity > product.quantity:
            return {"ok": False, "error": "Stock insuffisant",
                    "available": product.quantity}
        product.quantity -= quantity
        total = product.price * quantity
        # Bug 2: applique une remise de 10% si quantity >= 5
        if quantity >= 5:
            total *= 0.9
        self.db.save(sku, product.__dict__)
        self._notify("sell", product)
        return {"ok": True, "remaining": product.quantity,
                "total_price": round(total, 2)}

    def get_report(self) -> dict:
        """Rapport d'inventaire complet."""
        all_products = self.db.load_all()
        total_value = 0
        low_stock = []
        for sku, data in all_products.items():
            value = data["price"] * data["quantity"]
            total_value += value
            # Bug 3 corrigé: seuil low_stock basé sur la quantité
            if data["quantity"] < 10:
                low_stock.append(sku)
        return {
            "total_products": len(all_products),
            "total_value": round(total_value, 2),
            "low_stock_alerts": low_stock,
        }

    def transfer(self, from_sku: str, to_sku: str, quantity: int) -> bool:
        """Transfère du stock d'un produit à un autre."""
        source = self._get_or_load(from_sku)
        dest = self._get_or_load(to_sku)
        if source.quantity < quantity:
            return False
        source.quantity -= quantity
        dest.quantity += quantity
        # Correction bug 4: sauvegarde source et dest
        self.db.save(from_sku, source.__dict__)
        self.db.save(to_sku, dest.__dict__)
        return True

    def subscribe(self, callback: callable):
        """Enregistre un listener pour les événements."""
        self._listeners.append(callback)

    def _notify(self, event: str, product: Product):
        for listener in self._listeners:
            try:
                listener(event, product)
            except Exception as e:
                # Bug 5 corrigé : on propage l'erreur au lieu de l'avaler
                raise RuntimeError(f"Erreur dans le listener {listener.__name__ if hasattr(listener, '__name__') else listener}: {e}") from e

    def _get_or_load(self, sku: str) -> Product:
        if sku in self._cache:
            return self._cache[sku]
        data = self.db.load(sku)
        if data is None:
            raise KeyError(f"Produit inconnu: {sku}")
        product = Product(**data)
        self._cache[sku] = product
        return product
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
