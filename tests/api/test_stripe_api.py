"""Tests pour les handlers Stripe API V2."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import os


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_stripe_module():
    """Reset le module stripe lazy-loaded entre les tests."""
    import src.reasoning.handlers.stripe_api as mod
    mod._stripe = None
    yield
    mod._stripe = None


@pytest.fixture
def mock_stripe():
    """Mock complet du module stripe."""
    with patch.dict(os.environ, {"STRIPE_API_KEY": "sk_test_fake123"}):
        with patch("src.reasoning.handlers.stripe_api._get_stripe") as mock_get:
            mock_mod = MagicMock()
            mock_get.return_value = mock_mod
            yield mock_mod


@pytest.fixture
def ctx():
    """HandlerContext mocké."""
    c = MagicMock()
    c.user_id = "test_user"
    return c


# ── Tests get_handler_defs ────────────────────────────────────────────────────

def test_get_stripe_api_handler_defs_returns_list():
    from src.reasoning.handlers.stripe_api import get_stripe_api_handler_defs
    defs = get_stripe_api_handler_defs()
    assert isinstance(defs, list)
    assert len(defs) == 33


def test_all_handlers_have_required_fields():
    from src.reasoning.handlers.stripe_api import get_stripe_api_handler_defs
    defs = get_stripe_api_handler_defs()
    names = set()
    for hdef in defs:
        assert hdef.name, "name manquant"
        assert hdef.description, "description manquante"
        assert callable(hdef.handler), f"{hdef.name} handler non callable"
        assert hdef.category == "stripe"
        assert hdef.name not in names, f"doublon: {hdef.name}"
        names.add(hdef.name)


def test_handler_names_are_prefixed():
    from src.reasoning.handlers.stripe_api import get_stripe_api_handler_defs
    for hdef in get_stripe_api_handler_defs():
        assert hdef.name.startswith("stripe_"), f"{hdef.name} manque le préfixe stripe_"


# ── Tests Produits ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_product(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_product_handler
    mock_stripe.Product.create.return_value = MagicMock(id="prod_123", name="Test Prod")
    result = await stripe_create_product_handler(ctx, name="Test Prod")
    assert result.success
    assert "prod_123" in result.output
    mock_stripe.Product.create.assert_called_once()


@pytest.mark.asyncio
async def test_list_products(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_list_products_handler
    mock_stripe.Product.list.return_value = MagicMock(
        data=[MagicMock(name="P1", id="prod_1", active=True)]
    )
    result = await stripe_list_products_handler(ctx, limit=5)
    assert result.success
    assert "P1" in result.output


@pytest.mark.asyncio
async def test_update_product(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_update_product_handler
    mock_stripe.Product.modify.return_value = MagicMock(id="prod_1", name="Updated")
    result = await stripe_update_product_handler(ctx, product_id="prod_1", name="Updated")
    assert result.success


@pytest.mark.asyncio
async def test_update_product_no_fields(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_update_product_handler
    result = await stripe_update_product_handler(ctx, product_id="prod_1")
    assert not result.success
    assert "Aucun champ" in result.output


@pytest.mark.asyncio
async def test_delete_product(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_delete_product_handler
    mock_stripe.Product.delete.return_value = MagicMock(deleted=True)
    result = await stripe_delete_product_handler(ctx, product_id="prod_1")
    assert result.success


# ── Tests Prix ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_price_onetime(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_price_handler
    mock_stripe.Price.create.return_value = MagicMock(
        id="price_1", unit_amount=1999, currency="eur"
    )
    result = await stripe_create_price_handler(ctx, product_id="prod_1", unit_amount=1999)
    assert result.success
    assert "19.99" in result.output
    assert "unique" in result.output


@pytest.mark.asyncio
async def test_create_price_recurring(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_price_handler
    mock_stripe.Price.create.return_value = MagicMock(
        id="price_2", unit_amount=999, currency="eur"
    )
    result = await stripe_create_price_handler(
        ctx, product_id="prod_1", unit_amount=999, recurring_interval="month"
    )
    assert result.success
    assert "récurrent" in result.output


@pytest.mark.asyncio
async def test_create_price_invalid_interval(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_price_handler
    result = await stripe_create_price_handler(
        ctx, product_id="prod_1", unit_amount=100, recurring_interval="biweekly"
    )
    assert not result.success


@pytest.mark.asyncio
async def test_list_prices(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_list_prices_handler
    mock_stripe.Price.list.return_value = MagicMock(
        data=[MagicMock(id="price_1", unit_amount=500, currency="eur", recurring=None)]
    )
    result = await stripe_list_prices_handler(ctx)
    assert result.success


# ── Tests Payment Links ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_payment_link(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_payment_link_handler
    mock_stripe.PaymentLink.create.return_value = MagicMock(
        id="plink_1", url="https://buy.stripe.com/test_xxx"
    )
    result = await stripe_create_payment_link_handler(ctx, price_id="price_1")
    assert result.success
    assert "https://buy.stripe.com" in result.output


@pytest.mark.asyncio
async def test_list_payment_links(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_list_payment_links_handler
    mock_stripe.PaymentLink.list.return_value = MagicMock(
        data=[MagicMock(id="plink_1", url="https://buy.stripe.com/x", active=True)]
    )
    result = await stripe_list_payment_links_handler(ctx)
    assert result.success


# ── Tests Clients ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_customer(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_customer_handler
    mock_stripe.Customer.create.return_value = MagicMock(
        id="cus_1", email="test@test.com", **{"get.return_value": "John"}
    )
    result = await stripe_create_customer_handler(ctx, email="test@test.com", name="John")
    assert result.success
    assert "cus_1" in result.output


@pytest.mark.asyncio
async def test_list_customers(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_list_customers_handler
    mock_stripe.Customer.list.return_value = MagicMock(
        data=[MagicMock(id="cus_1", email="a@b.com", **{"get.return_value": "A"})]
    )
    result = await stripe_list_customers_handler(ctx)
    assert result.success


@pytest.mark.asyncio
async def test_search_customers(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_search_customers_handler
    mock_stripe.Customer.search.return_value = MagicMock(
        data=[MagicMock(id="cus_1", email="a@b.com", **{"get.return_value": "A"})]
    )
    result = await stripe_search_customers_handler(ctx, query="email:'a@b.com'")
    assert result.success


# ── Tests Abonnements ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_subscription(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_subscription_handler
    mock_stripe.Subscription.create.return_value = MagicMock(id="sub_1", status="active")
    result = await stripe_create_subscription_handler(ctx, customer_id="cus_1", price_id="price_1")
    assert result.success
    assert "sub_1" in result.output


@pytest.mark.asyncio
async def test_cancel_subscription_end_period(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_cancel_subscription_handler
    mock_stripe.Subscription.modify.return_value = MagicMock(id="sub_1")
    result = await stripe_cancel_subscription_handler(ctx, subscription_id="sub_1", cancel_at_period_end=True)
    assert result.success
    assert "fin de la période" in result.output


@pytest.mark.asyncio
async def test_cancel_subscription_immediate(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_cancel_subscription_handler
    mock_stripe.Subscription.cancel.return_value = MagicMock(id="sub_1")
    result = await stripe_cancel_subscription_handler(ctx, subscription_id="sub_1", cancel_at_period_end=False)
    assert result.success
    assert "immédiatement" in result.output


# ── Tests Factures ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_invoice(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_invoice_handler
    mock_stripe.Invoice.create.return_value = MagicMock(id="inv_1", status="draft")
    result = await stripe_create_invoice_handler(ctx, customer_id="cus_1")
    assert result.success
    assert "inv_1" in result.output


@pytest.mark.asyncio
async def test_send_invoice(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_send_invoice_handler
    mock_stripe.Invoice.retrieve.return_value = MagicMock(id="inv_1", status="draft")
    mock_stripe.Invoice.finalize_invoice.return_value = MagicMock(id="inv_1", status="open")
    mock_stripe.Invoice.send_invoice.return_value = MagicMock(
        id="inv_1", amount_due=1000, currency="eur"
    )
    result = await stripe_send_invoice_handler(ctx, invoice_id="inv_1")
    assert result.success


@pytest.mark.asyncio
async def test_void_invoice(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_void_invoice_handler
    mock_stripe.Invoice.void_invoice.return_value = MagicMock(id="inv_1")
    result = await stripe_void_invoice_handler(ctx, invoice_id="inv_1")
    assert result.success


# ── Tests Checkout ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_checkout_session(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_checkout_session_handler
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        id="cs_1", url="https://checkout.stripe.com/c/pay/cs_1"
    )
    result = await stripe_create_checkout_session_handler(ctx, price_id="price_1")
    assert result.success
    assert "checkout.stripe.com" in result.output


@pytest.mark.asyncio
async def test_create_checkout_invalid_mode(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_checkout_session_handler
    result = await stripe_create_checkout_session_handler(ctx, price_id="p", mode="invalid")
    assert not result.success


# ── Tests Coupons ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_coupon_percent(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_coupon_handler
    mock_stripe.Coupon.create.return_value = MagicMock(
        id="coup_1", name="20OFF", percent_off=20, amount_off=None
    )
    result = await stripe_create_coupon_handler(ctx, name="20OFF", percent_off=20)
    assert result.success
    assert "20" in result.output


@pytest.mark.asyncio
async def test_create_coupon_no_discount(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_coupon_handler
    result = await stripe_create_coupon_handler(ctx, name="Bad")
    assert not result.success


@pytest.mark.asyncio
async def test_delete_coupon(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_delete_coupon_handler
    mock_stripe.Coupon.delete.return_value = MagicMock(deleted=True)
    result = await stripe_delete_coupon_handler(ctx, coupon_id="coup_1")
    assert result.success


# ── Tests Remboursements ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_refund(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_create_refund_handler
    mock_stripe.Refund.create.return_value = MagicMock(
        id="re_1", amount=500, currency="eur", status="succeeded"
    )
    result = await stripe_create_refund_handler(ctx, payment_intent_id="pi_1")
    assert result.success
    assert "5.00" in result.output


# ── Tests Solde ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_balance(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_get_balance_handler
    mock_stripe.Balance.retrieve.return_value = MagicMock(
        available=[MagicMock(amount=10000, currency="eur")],
        pending=[MagicMock(amount=2500, currency="eur")],
    )
    result = await stripe_get_balance_handler(ctx)
    assert result.success
    assert "100.00" in result.output
    assert "25.00" in result.output


# ── Tests Error Handling ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handler_catches_stripe_error(mock_stripe, ctx):
    from src.reasoning.handlers.stripe_api import stripe_list_products_handler
    mock_stripe.Product.list.side_effect = Exception("Stripe API rate limited")
    result = await stripe_list_products_handler(ctx)
    assert not result.success
    assert "rate limited" in result.output


# ── Tests Helper ─────────────────────────────────────────────────────────────

def test_fmt_amount():
    from src.reasoning.handlers.stripe_api import _fmt_amount
    assert _fmt_amount(1999, "eur") == "19.99 EUR"
    assert _fmt_amount(0, "usd") == "0.00 USD"


def test_safe_meta_limits():
    from src.reasoning.handlers.stripe_api import _safe_meta
    assert _safe_meta(None) is None
    assert _safe_meta({}) is None
    big = {f"key_{i}": f"val_{i}" for i in range(60)}
    result = _safe_meta(big)
    assert len(result) == 50  # Max 50 keys


def test_get_stripe_no_key():
    """Sans STRIPE_API_KEY, _get_stripe doit lever RuntimeError."""
    import src.reasoning.handlers.stripe_api as mod
    mod._stripe = None
    with patch.dict(os.environ, {"STRIPE_API_KEY": ""}):
        with pytest.raises(RuntimeError, match="STRIPE_API_KEY|stripe non installé"):
            mod._get_stripe()
