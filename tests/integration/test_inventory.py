"""Tests pour le service d'inventaire — certains échouent à cause des bugs."""

import pytest
import tempfile
import os
from src.utils.inventory_service import InventoryService, Product


@pytest.fixture
def service(tmp_path):
    db_path = str(tmp_path / "test_inventory.json")
    return InventoryService(db_path=db_path)


@pytest.fixture
def stocked_service(service):
    """Service avec 3 produits pré-chargés."""
    service.add_product(Product("SKU001", "Widget", 9.99, 100))
    service.add_product(Product("SKU002", "Gadget", 24.99, 3))
    service.add_product(Product("SKU003", "Doohickey", 4.99, 50))
    return service


class TestAddProduct:
    def test_add_basic(self, service):
        sku = service.add_product(Product("ABC", "Test", 10.0, 5))
        assert sku == "ABC"

    def test_add_negative_price(self, service):
        with pytest.raises(ValueError):
            service.add_product(Product("BAD", "Nope", -5.0))

    def test_add_and_retrieve(self, service):
        service.add_product(Product("X1", "Item", 15.0, 10))
        product = service._get_or_load("X1")
        assert product.name == "Item"
        assert product.price == 15.0
        assert product.quantity == 10


class TestSell:
    def test_sell_basic(self, stocked_service):
        result = stocked_service.sell("SKU001", 2)
        assert result["ok"] is True
        assert result["remaining"] == 98
        assert result["total_price"] == 19.98  # 9.99 * 2

    def test_sell_insufficient_stock(self, stocked_service):
        result = stocked_service.sell("SKU002", 10)
        assert result["ok"] is False

    def test_sell_bulk_discount(self, stocked_service):
        """Achat de 5+ unités → remise 10%."""
        result = stocked_service.sell("SKU001", 10)
        assert result["ok"] is True
        expected = round(9.99 * 10 * 0.9, 2)  # 89.91
        assert result["total_price"] == expected, (
            f"Remise 10% sur 10 unités: attendu {expected}, "
            f"obtenu {result['total_price']}"
        )

    def test_sell_no_discount_under_5(self, stocked_service):
        """Achat de moins de 5 → pas de remise."""
        result = stocked_service.sell("SKU001", 3)
        expected = round(9.99 * 3, 2)  # 29.97
        assert result["total_price"] == expected


class TestTransfer:
    def test_transfer_basic(self, stocked_service):
        ok = stocked_service.transfer("SKU001", "SKU002", 20)
        assert ok is True
        # Vérifier que le stock a bougé
        source = stocked_service._get_or_load("SKU001")
        dest = stocked_service._get_or_load("SKU002")
        assert source.quantity == 80
        assert dest.quantity == 23  # 3 + 20

    def test_transfer_persists(self, stocked_service):
        """Le transfert doit être sauvegardé en DB."""
        stocked_service.transfer("SKU001", "SKU003", 10)
        # Vider le cache et recharger depuis la DB
        stocked_service._cache.clear()
        dest = stocked_service._get_or_load("SKU003")
        assert dest.quantity == 60  # 50 + 10


class TestReport:
    def test_report_total_products(self, stocked_service):
        report = stocked_service.get_report()
        assert report["total_products"] == 3

    def test_report_low_stock(self, stocked_service):
        """SKU002 a 3 unités → doit apparaître en low stock."""
        report = stocked_service.get_report()
        assert "SKU002" in report["low_stock_alerts"], (
            f"SKU002 (qty=3) devrait être en alerte, "
            f"alertes: {report['low_stock_alerts']}"
        )

    def test_report_not_low_stock(self, stocked_service):
        """SKU001 a 100 unités → ne doit PAS être en low stock."""
        report = stocked_service.get_report()
        assert "SKU001" not in report["low_stock_alerts"]
