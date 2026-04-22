"""
stripe_api.py - Handlers Stripe API complets pour Lumena.

Permet à Lumena de gérer de manière autonome : produits, prix, liens de paiement,
clients, abonnements, factures, coupons, remboursements, sessions checkout, solde.

Handlers (27):
  stripe_create_product, stripe_list_products, stripe_update_product, stripe_delete_product,
  stripe_create_price, stripe_list_prices,
  stripe_create_payment_link, stripe_list_payment_links, stripe_update_payment_link,
  stripe_create_customer, stripe_list_customers, stripe_search_customers, stripe_update_customer,
  stripe_create_subscription, stripe_list_subscriptions, stripe_cancel_subscription,
  stripe_create_invoice, stripe_list_invoices, stripe_send_invoice, stripe_void_invoice,
  stripe_create_checkout_session, stripe_list_checkout_sessions,
  stripe_create_coupon, stripe_list_coupons, stripe_delete_coupon,
  stripe_create_refund, stripe_list_refunds,
  stripe_get_balance.

Nécessite: pip install stripe + STRIPE_API_KEY dans l'environnement.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

# ─── Helper : init stripe ──────────────────────────────────────────────────

_stripe = None


def _get_stripe():
    """Import et configure stripe en lazy."""
    global _stripe
    if _stripe is not None:
        return _stripe
    try:
        import stripe
        key = os.getenv("STRIPE_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "STRIPE_API_KEY non configurée. "
                "Ajoute ta clé Stripe dans les variables d'environnement."
            )
        stripe.api_key = key
        _stripe = stripe
        return stripe
    except ImportError:
        raise RuntimeError(
            "Module stripe non installé. Exécute: pip install stripe"
        )


def _fmt_amount(amount_cents: int, currency: str = "eur") -> str:
    """Formate un montant en centimes vers un affichage lisible."""
    return f"{amount_cents / 100:.2f} {currency.upper()}"


def _safe_meta(metadata: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Valide les metadata Stripe (max 50 clés, 40 char key, 500 char value)."""
    if not metadata:
        return None
    clean = {}
    for k, v in list(metadata.items())[:50]:
        k_str = str(k)[:40].replace("[", "").replace("]", "")
        clean[k_str] = str(v)[:500]
    return clean


# ══════════════════════════════════════════════════════════════════════════════
#  PRODUITS
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_create_product_handler(
    ctx: HandlerContext,
    name: str,
    description: str = "",
    metadata: Optional[Dict[str, str]] = None,
    active: bool = True,
) -> HandlerResult:
    """Crée un produit Stripe."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"name": name, "active": active}
        if description:
            params["description"] = description
        meta = _safe_meta(metadata)
        if meta:
            params["metadata"] = meta
        product = stripe.Product.create(**params)
        return HandlerResult.ok(
            f"✅ Produit créé: **{product.name}** (id: `{product.id}`)",
            handler_name="stripe_create_product",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_create_product")


async def stripe_list_products_handler(
    ctx: HandlerContext,
    limit: int = 10,
    active: Optional[bool] = None,
) -> HandlerResult:
    """Liste les produits Stripe."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"limit": min(max(1, limit), 100)}
        if active is not None:
            params["active"] = active
        products = stripe.Product.list(**params)
        if not products.data:
            return HandlerResult.ok("Aucun produit trouvé.", handler_name="stripe_list_products")
        lines = []
        for p in products.data:
            status = "✅" if p.active else "⏸️"
            lines.append(f"{status} **{p.name}** — `{p.id}`")
        return HandlerResult.ok(
            f"📦 {len(products.data)} produit(s):\n" + "\n".join(lines),
            handler_name="stripe_list_products",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_list_products")


async def stripe_update_product_handler(
    ctx: HandlerContext,
    product_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    active: Optional[bool] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> HandlerResult:
    """Met à jour un produit Stripe."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {}
        if name is not None:
            params["name"] = name
        if description is not None:
            params["description"] = description
        if active is not None:
            params["active"] = active
        meta = _safe_meta(metadata)
        if meta:
            params["metadata"] = meta
        if not params:
            return HandlerResult.fail("Aucun champ à modifier.", handler_name="stripe_update_product")
        product = stripe.Product.modify(product_id, **params)
        return HandlerResult.ok(
            f"✅ Produit mis à jour: **{product.name}** (`{product.id}`)",
            handler_name="stripe_update_product",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_update_product")


async def stripe_delete_product_handler(
    ctx: HandlerContext,
    product_id: str,
) -> HandlerResult:
    """Supprime (archive) un produit Stripe."""
    try:
        stripe = _get_stripe()
        product = stripe.Product.delete(product_id)
        return HandlerResult.ok(
            f"🗑️ Produit supprimé: `{product_id}`",
            handler_name="stripe_delete_product",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_delete_product")


# ══════════════════════════════════════════════════════════════════════════════
#  PRIX
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_create_price_handler(
    ctx: HandlerContext,
    product_id: str,
    unit_amount: int,
    currency: str = "eur",
    recurring_interval: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> HandlerResult:
    """Crée un prix pour un produit (one-time ou récurrent: day/week/month/year)."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {
            "product": product_id,
            "unit_amount": int(unit_amount),
            "currency": currency.lower(),
        }
        if recurring_interval:
            if recurring_interval not in ("day", "week", "month", "year"):
                return HandlerResult.fail(
                    "Intervalle invalide. Utilise: day, week, month, year",
                    handler_name="stripe_create_price",
                )
            params["recurring"] = {"interval": recurring_interval}
        meta = _safe_meta(metadata)
        if meta:
            params["metadata"] = meta
        price = stripe.Price.create(**params)
        recur = f" (récurrent: {recurring_interval})" if recurring_interval else " (unique)"
        return HandlerResult.ok(
            f"✅ Prix créé: {_fmt_amount(price.unit_amount, price.currency)}{recur} — `{price.id}`",
            handler_name="stripe_create_price",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_create_price")


async def stripe_list_prices_handler(
    ctx: HandlerContext,
    product_id: Optional[str] = None,
    limit: int = 10,
    active: Optional[bool] = None,
) -> HandlerResult:
    """Liste les prix Stripe, optionnellement filtrés par produit."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"limit": min(max(1, limit), 100)}
        if product_id:
            params["product"] = product_id
        if active is not None:
            params["active"] = active
        prices = stripe.Price.list(**params)
        if not prices.data:
            return HandlerResult.ok("Aucun prix trouvé.", handler_name="stripe_list_prices")
        lines = []
        for p in prices.data:
            recur = f"/{p.recurring.interval}" if p.recurring else " unique"
            lines.append(f"💰 {_fmt_amount(p.unit_amount, p.currency)}{recur} — `{p.id}`")
        return HandlerResult.ok(
            f"💰 {len(prices.data)} prix:\n" + "\n".join(lines),
            handler_name="stripe_list_prices",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_list_prices")


# ══════════════════════════════════════════════════════════════════════════════
#  LIENS DE PAIEMENT
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_create_payment_link_handler(
    ctx: HandlerContext,
    price_id: str,
    quantity: int = 1,
    metadata: Optional[Dict[str, str]] = None,
    after_completion_url: Optional[str] = None,
) -> HandlerResult:
    """Crée un lien de paiement Stripe partageable."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {
            "line_items": [{"price": price_id, "quantity": max(1, int(quantity))}],
        }
        meta = _safe_meta(metadata)
        if meta:
            params["metadata"] = meta
        if after_completion_url:
            params["after_completion"] = {
                "type": "redirect",
                "redirect": {"url": after_completion_url},
            }
        link = stripe.PaymentLink.create(**params)
        return HandlerResult.ok(
            f"🔗 Lien de paiement créé: {link.url}\n(id: `{link.id}`)",
            handler_name="stripe_create_payment_link",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_create_payment_link")


async def stripe_list_payment_links_handler(
    ctx: HandlerContext,
    limit: int = 10,
    active: Optional[bool] = None,
) -> HandlerResult:
    """Liste les liens de paiement Stripe."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"limit": min(max(1, limit), 100)}
        if active is not None:
            params["active"] = active
        links = stripe.PaymentLink.list(**params)
        if not links.data:
            return HandlerResult.ok("Aucun lien de paiement.", handler_name="stripe_list_payment_links")
        lines = []
        for lnk in links.data:
            status = "✅" if lnk.active else "⏸️"
            lines.append(f"{status} {lnk.url} — `{lnk.id}`")
        return HandlerResult.ok(
            f"🔗 {len(links.data)} lien(s):\n" + "\n".join(lines),
            handler_name="stripe_list_payment_links",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_list_payment_links")


async def stripe_update_payment_link_handler(
    ctx: HandlerContext,
    payment_link_id: str,
    active: Optional[bool] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> HandlerResult:
    """Active/désactive ou met à jour un lien de paiement."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {}
        if active is not None:
            params["active"] = active
        meta = _safe_meta(metadata)
        if meta:
            params["metadata"] = meta
        if not params:
            return HandlerResult.fail("Aucun champ à modifier.", handler_name="stripe_update_payment_link")
        link = stripe.PaymentLink.modify(payment_link_id, **params)
        status = "actif" if link.active else "désactivé"
        return HandlerResult.ok(
            f"✅ Lien mis à jour ({status}): `{link.id}`",
            handler_name="stripe_update_payment_link",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_update_payment_link")


# ══════════════════════════════════════════════════════════════════════════════
#  CLIENTS
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_create_customer_handler(
    ctx: HandlerContext,
    email: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> HandlerResult:
    """Crée un client Stripe."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"email": email}
        if name:
            params["name"] = name
        if description:
            params["description"] = description
        meta = _safe_meta(metadata)
        if meta:
            params["metadata"] = meta
        customer = stripe.Customer.create(**params)
        return HandlerResult.ok(
            f"👤 Client créé: **{customer.get('name') or customer.email}** — `{customer.id}`",
            handler_name="stripe_create_customer",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_create_customer")


async def stripe_list_customers_handler(
    ctx: HandlerContext,
    limit: int = 10,
    email: Optional[str] = None,
) -> HandlerResult:
    """Liste les clients Stripe."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"limit": min(max(1, limit), 100)}
        if email:
            params["email"] = email
        customers = stripe.Customer.list(**params)
        if not customers.data:
            return HandlerResult.ok("Aucun client trouvé.", handler_name="stripe_list_customers")
        lines = []
        for c in customers.data:
            lines.append(f"👤 **{c.get('name') or '—'}** ({c.email or '—'}) — `{c.id}`")
        return HandlerResult.ok(
            f"👤 {len(customers.data)} client(s):\n" + "\n".join(lines),
            handler_name="stripe_list_customers",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_list_customers")


async def stripe_search_customers_handler(
    ctx: HandlerContext,
    query: str,
    limit: int = 10,
) -> HandlerResult:
    """Recherche des clients (ex: query=\"email:'test@example.com'\" ou \"name:'John'\")."""
    try:
        stripe = _get_stripe()
        result = stripe.Customer.search(query=query, limit=min(max(1, limit), 100))
        if not result.data:
            return HandlerResult.ok(
                f"Aucun client pour: {query}", handler_name="stripe_search_customers"
            )
        lines = []
        for c in result.data:
            lines.append(f"👤 **{c.get('name') or '—'}** ({c.email or '—'}) — `{c.id}`")
        return HandlerResult.ok(
            f"🔍 {len(result.data)} résultat(s):\n" + "\n".join(lines),
            handler_name="stripe_search_customers",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_search_customers")


async def stripe_update_customer_handler(
    ctx: HandlerContext,
    customer_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> HandlerResult:
    """Met à jour un client Stripe."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {}
        if name is not None:
            params["name"] = name
        if email is not None:
            params["email"] = email
        if description is not None:
            params["description"] = description
        meta = _safe_meta(metadata)
        if meta:
            params["metadata"] = meta
        if not params:
            return HandlerResult.fail("Aucun champ à modifier.", handler_name="stripe_update_customer")
        customer = stripe.Customer.modify(customer_id, **params)
        return HandlerResult.ok(
            f"✅ Client mis à jour: **{customer.get('name') or customer.email}** (`{customer.id}`)",
            handler_name="stripe_update_customer",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_update_customer")


# ══════════════════════════════════════════════════════════════════════════════
#  ABONNEMENTS
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_create_subscription_handler(
    ctx: HandlerContext,
    customer_id: str,
    price_id: str,
    trial_period_days: Optional[int] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> HandlerResult:
    """Crée un abonnement récurrent pour un client."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {
            "customer": customer_id,
            "items": [{"price": price_id}],
        }
        if trial_period_days and trial_period_days > 0:
            params["trial_period_days"] = int(trial_period_days)
        meta = _safe_meta(metadata)
        if meta:
            params["metadata"] = meta
        sub = stripe.Subscription.create(**params)
        trial = f" (essai: {trial_period_days}j)" if trial_period_days else ""
        return HandlerResult.ok(
            f"🔄 Abonnement créé{trial}: `{sub.id}` — status: {sub.status}",
            handler_name="stripe_create_subscription",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_create_subscription")


async def stripe_list_subscriptions_handler(
    ctx: HandlerContext,
    customer_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10,
) -> HandlerResult:
    """Liste les abonnements (filtrable par client et status: active/past_due/canceled/all)."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"limit": min(max(1, limit), 100)}
        if customer_id:
            params["customer"] = customer_id
        if status:
            params["status"] = status
        subs = stripe.Subscription.list(**params)
        if not subs.data:
            return HandlerResult.ok("Aucun abonnement.", handler_name="stripe_list_subscriptions")
        lines = []
        for s in subs.data:
            lines.append(f"🔄 `{s.id}` — {s.status} (client: `{s.customer}`)")
        return HandlerResult.ok(
            f"🔄 {len(subs.data)} abonnement(s):\n" + "\n".join(lines),
            handler_name="stripe_list_subscriptions",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_list_subscriptions")


async def stripe_cancel_subscription_handler(
    ctx: HandlerContext,
    subscription_id: str,
    cancel_at_period_end: bool = True,
) -> HandlerResult:
    """Annule un abonnement (immédiat ou à la fin de la période)."""
    try:
        stripe = _get_stripe()
        if cancel_at_period_end:
            sub = stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
            return HandlerResult.ok(
                f"⏳ Abonnement annulé à la fin de la période: `{sub.id}`",
                handler_name="stripe_cancel_subscription",
            )
        else:
            sub = stripe.Subscription.cancel(subscription_id)
            return HandlerResult.ok(
                f"🗑️ Abonnement annulé immédiatement: `{sub.id}`",
                handler_name="stripe_cancel_subscription",
            )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_cancel_subscription")


# ══════════════════════════════════════════════════════════════════════════════
#  FACTURES
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_create_invoice_handler(
    ctx: HandlerContext,
    customer_id: str,
    description: Optional[str] = None,
    days_until_due: int = 30,
    metadata: Optional[Dict[str, str]] = None,
) -> HandlerResult:
    """Crée une facture brouillon pour un client."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {
            "customer": customer_id,
            "collection_method": "send_invoice",
            "days_until_due": max(1, int(days_until_due)),
        }
        if description:
            params["description"] = description
        meta = _safe_meta(metadata)
        if meta:
            params["metadata"] = meta
        invoice = stripe.Invoice.create(**params)
        return HandlerResult.ok(
            f"📄 Facture brouillon créée: `{invoice.id}` (client: `{customer_id}`)\n"
            f"⚠️ Utilise stripe_add_invoice_item pour ajouter des lignes avant d'envoyer.",
            handler_name="stripe_create_invoice",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_create_invoice")


async def stripe_add_invoice_item_handler(
    ctx: HandlerContext,
    customer_id: str,
    description: str,
    unit_amount: int,
    quantity: int = 1,
    currency: str = "eur",
    invoice_id: Optional[str] = None,
    price_id: Optional[str] = None,
) -> HandlerResult:
    """Ajoute une ligne à une facture brouillon (unit_amount en centimes, ex: 5000 = 50€)."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"customer": customer_id}
        if invoice_id:
            params["invoice"] = invoice_id
        if price_id:
            params["price"] = price_id
        else:
            params["unit_amount"] = int(unit_amount)
            params["currency"] = currency.lower()
            params["description"] = description
        params["quantity"] = max(1, int(quantity))
        item = stripe.InvoiceItem.create(**params)
        return HandlerResult.ok(
            f"✅ Ligne ajoutée: {description} — {_fmt_amount(int(unit_amount) * max(1, int(quantity)), currency)} — `{item.id}`",
            handler_name="stripe_add_invoice_item",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_add_invoice_item")


async def stripe_get_invoice_handler(
    ctx: HandlerContext,
    invoice_id: str,
) -> HandlerResult:
    """Récupère les détails d'une facture + lien PDF si finalisée."""
    try:
        stripe = _get_stripe()
        invoice = stripe.Invoice.retrieve(invoice_id)
        lines = []
        lines.append(f"📄 Facture `{invoice.id}`")
        lines.append(f"Status: {invoice.status}")
        lines.append(f"Client: `{invoice.customer}`")
        lines.append(f"Montant: {_fmt_amount(invoice.amount_due or 0, invoice.currency or 'eur')}")
        if invoice.description:
            lines.append(f"Description: {invoice.description}")
        if invoice.due_date:
            from datetime import datetime
            lines.append(f"Échéance: {datetime.fromtimestamp(invoice.due_date).strftime('%d/%m/%Y')}")
        # Lignes de facture
        if hasattr(invoice, 'lines') and invoice.lines and invoice.lines.data:
            lines.append(f"\n📋 Lignes ({len(invoice.lines.data)}):")
            for li in invoice.lines.data:
                desc = li.description or '(sans description)'
                amt = _fmt_amount(li.amount or 0, li.currency or 'eur')
                lines.append(f"  • {desc} — {amt}")
        # PDF
        pdf_url = getattr(invoice, 'invoice_pdf', None)
        if pdf_url:
            lines.append(f"\n📥 PDF: {pdf_url}")
        hosted_url = getattr(invoice, 'hosted_invoice_url', None)
        if hosted_url:
            lines.append(f"🌐 Page paiement: {hosted_url}")
        return HandlerResult.ok(
            "\n".join(lines),
            handler_name="stripe_get_invoice",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_get_invoice")


async def stripe_list_invoices_handler(
    ctx: HandlerContext,
    customer_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10,
) -> HandlerResult:
    """Liste les factures (status: draft/open/paid/void/uncollectible)."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"limit": min(max(1, limit), 100)}
        if customer_id:
            params["customer"] = customer_id
        if status:
            params["status"] = status
        invoices = stripe.Invoice.list(**params)
        if not invoices.data:
            return HandlerResult.ok("Aucune facture.", handler_name="stripe_list_invoices")
        lines = []
        for inv in invoices.data:
            total = _fmt_amount(inv.amount_due or 0, inv.currency or "eur")
            pdf = f" — [PDF]({inv.invoice_pdf})" if getattr(inv, 'invoice_pdf', None) else ""
            lines.append(f"📄 `{inv.id}` — {inv.status} — {total}{pdf}")
        return HandlerResult.ok(
            f"📄 {len(invoices.data)} facture(s):\n" + "\n".join(lines),
            handler_name="stripe_list_invoices",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_list_invoices")


async def stripe_send_invoice_handler(
    ctx: HandlerContext,
    invoice_id: str,
) -> HandlerResult:
    """Finalise et envoie une facture brouillon au client."""
    try:
        stripe = _get_stripe()
        # Finaliser d'abord si brouillon
        invoice = stripe.Invoice.retrieve(invoice_id)
        if invoice.status == "draft":
            invoice = stripe.Invoice.finalize_invoice(invoice_id)
        invoice = stripe.Invoice.send_invoice(invoice_id)
        pdf = f"\n📥 PDF: {invoice.invoice_pdf}" if getattr(invoice, 'invoice_pdf', None) else ""
        hosted = f"\n🌐 Page paiement: {invoice.hosted_invoice_url}" if getattr(invoice, 'hosted_invoice_url', None) else ""
        return HandlerResult.ok(
            f"📨 Facture envoyée: `{invoice.id}` — {_fmt_amount(invoice.amount_due, invoice.currency)}{pdf}{hosted}",
            handler_name="stripe_send_invoice",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_send_invoice")


async def stripe_void_invoice_handler(
    ctx: HandlerContext,
    invoice_id: str,
) -> HandlerResult:
    """Annule une facture ouverte."""
    try:
        stripe = _get_stripe()
        invoice = stripe.Invoice.void_invoice(invoice_id)
        return HandlerResult.ok(
            f"🚫 Facture annulée: `{invoice.id}`",
            handler_name="stripe_void_invoice",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_void_invoice")


# ══════════════════════════════════════════════════════════════════════════════
#  CHECKOUT SESSIONS
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_create_checkout_session_handler(
    ctx: HandlerContext,
    price_id: str,
    quantity: int = 1,
    mode: str = "payment",
    success_url: str = "https://example.com/success",
    cancel_url: str = "https://example.com/cancel",
    customer_email: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> HandlerResult:
    """Crée une session Checkout Stripe (mode: payment/subscription/setup)."""
    try:
        stripe = _get_stripe()
        if mode not in ("payment", "subscription", "setup"):
            return HandlerResult.fail(
                "Mode invalide. Utilise: payment, subscription, setup",
                handler_name="stripe_create_checkout_session",
            )
        params: Dict[str, Any] = {
            "line_items": [{"price": price_id, "quantity": max(1, int(quantity))}],
            "mode": mode,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if customer_email:
            params["customer_email"] = customer_email
        meta = _safe_meta(metadata)
        if meta:
            params["metadata"] = meta
        session = stripe.checkout.Session.create(**params)
        return HandlerResult.ok(
            f"🛒 Session Checkout créée:\n{session.url}\n(id: `{session.id}`, mode: {mode})",
            handler_name="stripe_create_checkout_session",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_create_checkout_session")


async def stripe_list_checkout_sessions_handler(
    ctx: HandlerContext,
    limit: int = 10,
    status: Optional[str] = None,
) -> HandlerResult:
    """Liste les sessions Checkout (status: open/complete/expired)."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"limit": min(max(1, limit), 100)}
        if status:
            params["status"] = status
        sessions = stripe.checkout.Session.list(**params)
        if not sessions.data:
            return HandlerResult.ok("Aucune session.", handler_name="stripe_list_checkout_sessions")
        lines = []
        for s in sessions.data:
            total = _fmt_amount(s.amount_total or 0, s.currency or "eur")
            lines.append(f"🛒 `{s.id}` — {s.status} — {total}")
        return HandlerResult.ok(
            f"🛒 {len(sessions.data)} session(s):\n" + "\n".join(lines),
            handler_name="stripe_list_checkout_sessions",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_list_checkout_sessions")


# ══════════════════════════════════════════════════════════════════════════════
#  COUPONS
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_create_coupon_handler(
    ctx: HandlerContext,
    name: str,
    percent_off: Optional[float] = None,
    amount_off: Optional[int] = None,
    currency: str = "eur",
    duration: str = "once",
    duration_in_months: Optional[int] = None,
    max_redemptions: Optional[int] = None,
) -> HandlerResult:
    """Crée un coupon (percent_off OU amount_off, duration: once/repeating/forever)."""
    try:
        stripe = _get_stripe()
        if not percent_off and not amount_off:
            return HandlerResult.fail(
                "Spécifie percent_off (ex: 20) ou amount_off (en centimes, ex: 500 = 5€)",
                handler_name="stripe_create_coupon",
            )
        params: Dict[str, Any] = {"name": name, "duration": duration}
        if percent_off:
            params["percent_off"] = float(percent_off)
        elif amount_off:
            params["amount_off"] = int(amount_off)
            params["currency"] = currency.lower()
        if duration == "repeating" and duration_in_months:
            params["duration_in_months"] = int(duration_in_months)
        if max_redemptions:
            params["max_redemptions"] = int(max_redemptions)
        coupon = stripe.Coupon.create(**params)
        desc = f"{coupon.percent_off}%" if coupon.percent_off else _fmt_amount(coupon.amount_off, currency)
        return HandlerResult.ok(
            f"🎫 Coupon créé: **{coupon.name}** (-{desc}) — `{coupon.id}`",
            handler_name="stripe_create_coupon",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_create_coupon")


async def stripe_list_coupons_handler(
    ctx: HandlerContext,
    limit: int = 10,
) -> HandlerResult:
    """Liste les coupons Stripe."""
    try:
        stripe = _get_stripe()
        coupons = stripe.Coupon.list(limit=min(max(1, limit), 100))
        if not coupons.data:
            return HandlerResult.ok("Aucun coupon.", handler_name="stripe_list_coupons")
        lines = []
        for c in coupons.data:
            desc = f"{c.percent_off}%" if c.percent_off else _fmt_amount(c.amount_off or 0, c.currency or "eur")
            lines.append(f"🎫 **{c.name or c.id}** (-{desc}) — `{c.id}`")
        return HandlerResult.ok(
            f"🎫 {len(coupons.data)} coupon(s):\n" + "\n".join(lines),
            handler_name="stripe_list_coupons",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_list_coupons")


async def stripe_delete_coupon_handler(
    ctx: HandlerContext,
    coupon_id: str,
) -> HandlerResult:
    """Supprime un coupon."""
    try:
        stripe = _get_stripe()
        stripe.Coupon.delete(coupon_id)
        return HandlerResult.ok(
            f"🗑️ Coupon supprimé: `{coupon_id}`",
            handler_name="stripe_delete_coupon",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_delete_coupon")


# ══════════════════════════════════════════════════════════════════════════════
#  REMBOURSEMENTS
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_create_refund_handler(
    ctx: HandlerContext,
    payment_intent_id: str,
    amount: Optional[int] = None,
    reason: Optional[str] = None,
) -> HandlerResult:
    """Rembourse un paiement (total si amount=None, partiel sinon). Reason: duplicate/fraudulent/requested_by_customer."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"payment_intent": payment_intent_id}
        if amount:
            params["amount"] = int(amount)
        if reason and reason in ("duplicate", "fraudulent", "requested_by_customer"):
            params["reason"] = reason
        refund = stripe.Refund.create(**params)
        amt = _fmt_amount(refund.amount, refund.currency)
        return HandlerResult.ok(
            f"💸 Remboursement effectué: {amt} — `{refund.id}` (status: {refund.status})",
            handler_name="stripe_create_refund",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_create_refund")


async def stripe_list_refunds_handler(
    ctx: HandlerContext,
    payment_intent_id: Optional[str] = None,
    limit: int = 10,
) -> HandlerResult:
    """Liste les remboursements."""
    try:
        stripe = _get_stripe()
        params: Dict[str, Any] = {"limit": min(max(1, limit), 100)}
        if payment_intent_id:
            params["payment_intent"] = payment_intent_id
        refunds = stripe.Refund.list(**params)
        if not refunds.data:
            return HandlerResult.ok("Aucun remboursement.", handler_name="stripe_list_refunds")
        lines = []
        for r in refunds.data:
            lines.append(f"💸 {_fmt_amount(r.amount, r.currency)} — {r.status} — `{r.id}`")
        return HandlerResult.ok(
            f"💸 {len(refunds.data)} remboursement(s):\n" + "\n".join(lines),
            handler_name="stripe_list_refunds",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_list_refunds")


# ══════════════════════════════════════════════════════════════════════════════
#  SOLDE
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_get_balance_handler(
    ctx: HandlerContext,
) -> HandlerResult:
    """Récupère le solde du compte Stripe (disponible + en attente)."""
    try:
        stripe = _get_stripe()
        balance = stripe.Balance.retrieve()
        lines = []
        for b in balance.available:
            lines.append(f"✅ Disponible: {_fmt_amount(b.amount, b.currency)}")
        for b in balance.pending:
            lines.append(f"⏳ En attente: {_fmt_amount(b.amount, b.currency)}")
        return HandlerResult.ok(
            f"💰 Solde Stripe:\n" + "\n".join(lines),
            handler_name="stripe_get_balance",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe: {e}", handler_name="stripe_get_balance")


# ══════════════════════════════════════════════════════════════════════════════
#  STRIPE CLI MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

async def stripe_cli_status_handler(
    ctx: HandlerContext,
) -> HandlerResult:
    """Vérifie le statut de la Stripe CLI."""
    try:
        from src.services.stripe_cli import get_stripe_cli_service
        svc = get_stripe_cli_service()
        info = svc.status()
        version = svc.check_version()
        lines = []
        lines.append(f"Installée: {'✅' if info['installed'] else '❌'}")
        if version:
            lines.append(f"Version: {version}")
        lines.append(f"En cours: {'✅' if info['running'] else '❌'}")
        lines.append(f"Forward: {info['forward_url']}")
        lines.append(f"Webhook secret: {'✅ configuré' if info['webhook_secret_set'] else '❌ absent'}")
        if info.get('pid'):
            lines.append(f"PID: {info['pid']}")
        return HandlerResult.ok(
            "🔧 Stripe CLI:\n" + "\n".join(lines),
            handler_name="stripe_cli_status",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe CLI: {e}", handler_name="stripe_cli_status")


async def stripe_cli_start_handler(
    ctx: HandlerContext,
) -> HandlerResult:
    """Démarre stripe listen pour recevoir les webhooks en local."""
    try:
        from src.services.stripe_cli import get_stripe_cli_service
        svc = get_stripe_cli_service()
        if svc.is_running:
            return HandlerResult.ok(
                "✅ Stripe CLI déjà en cours d'exécution",
                handler_name="stripe_cli_start",
            )
        success = await svc.start()
        if success:
            secret_msg = ""
            if svc.webhook_secret:
                secret_msg = f"\nWebhook secret: `{svc.webhook_secret[:15]}...` (injecté automatiquement)"
            return HandlerResult.ok(
                f"✅ Stripe CLI démarrée — webhooks forwardés vers {svc.forward_url}{secret_msg}",
                handler_name="stripe_cli_start",
            )
        else:
            return HandlerResult.fail(
                "❌ Impossible de démarrer la Stripe CLI. Vérifie que stripe est installé et que STRIPE_API_KEY est configurée.",
                handler_name="stripe_cli_start",
            )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe CLI: {e}", handler_name="stripe_cli_start")


async def stripe_cli_stop_handler(
    ctx: HandlerContext,
) -> HandlerResult:
    """Arrête la Stripe CLI."""
    try:
        from src.services.stripe_cli import get_stripe_cli_service
        svc = get_stripe_cli_service()
        if not svc.is_running:
            return HandlerResult.ok(
                "Stripe CLI n'est pas en cours d'exécution.",
                handler_name="stripe_cli_stop",
            )
        await svc.stop()
        return HandlerResult.ok(
            "✅ Stripe CLI arrêtée",
            handler_name="stripe_cli_stop",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Stripe CLI: {e}", handler_name="stripe_cli_stop")


# ══════════════════════════════════════════════════════════════════════════════
#  EXPORT HandlerDefs
# ══════════════════════════════════════════════════════════════════════════════

def get_stripe_api_handler_defs() -> List[HandlerDef]:
    """Retourne les 33 HandlerDef Stripe pour le registre V2."""
    return [
        # ── Produits ──
        HandlerDef(
            name="stripe_create_product",
            description="Crée un produit Stripe (name requis, description et metadata optionnels).",
            parameters={"name": {"type": "string", "required": True}, "description": {"type": "string"}, "metadata": {"type": "object"}, "active": {"type": "boolean"}},
            handler=stripe_create_product_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_list_products",
            description="Liste les produits Stripe (limit, active optionnels).",
            parameters={"limit": {"type": "integer"}, "active": {"type": "boolean"}},
            handler=stripe_list_products_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_update_product",
            description="Met à jour un produit Stripe (name, description, active, metadata).",
            parameters={"product_id": {"type": "string", "required": True}, "name": {"type": "string"}, "description": {"type": "string"}, "active": {"type": "boolean"}, "metadata": {"type": "object"}},
            handler=stripe_update_product_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_delete_product",
            description="Supprime un produit Stripe.",
            parameters={"product_id": {"type": "string", "required": True}},
            handler=stripe_delete_product_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        # ── Prix ──
        HandlerDef(
            name="stripe_create_price",
            description="Crée un prix pour un produit (unit_amount en centimes, recurring_interval: day/week/month/year ou vide pour one-time).",
            parameters={"product_id": {"type": "string", "required": True}, "unit_amount": {"type": "integer", "required": True}, "currency": {"type": "string"}, "recurring_interval": {"type": "string"}, "metadata": {"type": "object"}},
            handler=stripe_create_price_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_list_prices",
            description="Liste les prix Stripe (filtrable par product_id).",
            parameters={"product_id": {"type": "string"}, "limit": {"type": "integer"}, "active": {"type": "boolean"}},
            handler=stripe_list_prices_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        # ── Liens de paiement ──
        HandlerDef(
            name="stripe_create_payment_link",
            description="Crée un lien de paiement Stripe partageable à partir d'un price_id. Si tu n'as pas encore de price_id, commence par stripe_create_product (obtiens product_id) puis stripe_create_price (unit_amount en centimes, ex: 1400 pour 14€ → obtiens price_id) — ENSUITE appelle ce handler avec le price_id obtenu.",
            parameters={"price_id": {"type": "string", "required": True}, "quantity": {"type": "integer"}, "metadata": {"type": "object"}, "after_completion_url": {"type": "string"}},
            handler=stripe_create_payment_link_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_list_payment_links",
            description="Liste les liens de paiement Stripe.",
            parameters={"limit": {"type": "integer"}, "active": {"type": "boolean"}},
            handler=stripe_list_payment_links_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_update_payment_link",
            description="Active/désactive ou met à jour un lien de paiement.",
            parameters={"payment_link_id": {"type": "string", "required": True}, "active": {"type": "boolean"}, "metadata": {"type": "object"}},
            handler=stripe_update_payment_link_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        # ── Clients ──
        HandlerDef(
            name="stripe_create_customer",
            description="Crée un client Stripe (email requis).",
            parameters={"email": {"type": "string", "required": True}, "name": {"type": "string"}, "description": {"type": "string"}, "metadata": {"type": "object"}},
            handler=stripe_create_customer_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_list_customers",
            description="Liste les clients Stripe (filtrable par email).",
            parameters={"limit": {"type": "integer"}, "email": {"type": "string"}},
            handler=stripe_list_customers_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_search_customers",
            description="Recherche des clients Stripe (query: \"email:'x@y.com'\" ou \"name:'John'\").",
            parameters={"query": {"type": "string", "required": True}, "limit": {"type": "integer"}},
            handler=stripe_search_customers_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_update_customer",
            description="Met à jour un client Stripe (name, email, description, metadata).",
            parameters={"customer_id": {"type": "string", "required": True}, "name": {"type": "string"}, "email": {"type": "string"}, "description": {"type": "string"}, "metadata": {"type": "object"}},
            handler=stripe_update_customer_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        # ── Abonnements ──
        HandlerDef(
            name="stripe_create_subscription",
            description="Crée un abonnement pour un client (customer_id + price_id récurrent).",
            parameters={"customer_id": {"type": "string", "required": True}, "price_id": {"type": "string", "required": True}, "trial_period_days": {"type": "integer"}, "metadata": {"type": "object"}},
            handler=stripe_create_subscription_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_list_subscriptions",
            description="Liste les abonnements (filtrable par customer_id, status: active/past_due/canceled/all).",
            parameters={"customer_id": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer"}},
            handler=stripe_list_subscriptions_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_cancel_subscription",
            description="Annule un abonnement (cancel_at_period_end=true par défaut).",
            parameters={"subscription_id": {"type": "string", "required": True}, "cancel_at_period_end": {"type": "boolean"}},
            handler=stripe_cancel_subscription_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        # ── Factures ──
        HandlerDef(
            name="stripe_create_invoice",
            description="Crée une facture brouillon pour un client (days_until_due=30 par défaut).",
            parameters={"customer_id": {"type": "string", "required": True}, "description": {"type": "string"}, "days_until_due": {"type": "integer"}, "metadata": {"type": "object"}},
            handler=stripe_create_invoice_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_add_invoice_item",
            description="Ajoute une ligne à une facture brouillon (unit_amount en centimes, ex: 5000 = 50€). Utilise invoice_id pour cibler la facture, ou laisse vide pour la prochaine facture brouillon du client.",
            parameters={"customer_id": {"type": "string", "required": True}, "description": {"type": "string", "required": True}, "unit_amount": {"type": "integer", "required": True}, "quantity": {"type": "integer"}, "currency": {"type": "string"}, "invoice_id": {"type": "string"}, "price_id": {"type": "string"}},
            handler=stripe_add_invoice_item_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_get_invoice",
            description="Récupère les détails d'une facture (lignes, montant, statut, lien PDF, page de paiement).",
            parameters={"invoice_id": {"type": "string", "required": True}},
            handler=stripe_get_invoice_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_list_invoices",
            description="Liste les factures (filtrable par customer_id, status: draft/open/paid/void).",
            parameters={"customer_id": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer"}},
            handler=stripe_list_invoices_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_send_invoice",
            description="Finalise et envoie une facture brouillon au client par email.",
            parameters={"invoice_id": {"type": "string", "required": True}},
            handler=stripe_send_invoice_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_void_invoice",
            description="Annule une facture ouverte (void).",
            parameters={"invoice_id": {"type": "string", "required": True}},
            handler=stripe_void_invoice_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        # ── Checkout Sessions ──
        HandlerDef(
            name="stripe_create_checkout_session",
            description="Crée une session Checkout hébergée par Stripe (mode: payment/subscription/setup).",
            parameters={"price_id": {"type": "string", "required": True}, "quantity": {"type": "integer"}, "mode": {"type": "string"}, "success_url": {"type": "string"}, "cancel_url": {"type": "string"}, "customer_email": {"type": "string"}, "metadata": {"type": "object"}},
            handler=stripe_create_checkout_session_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_list_checkout_sessions",
            description="Liste les sessions Checkout (status: open/complete/expired).",
            parameters={"limit": {"type": "integer"}, "status": {"type": "string"}},
            handler=stripe_list_checkout_sessions_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        # ── Coupons ──
        HandlerDef(
            name="stripe_create_coupon",
            description="Crée un coupon de réduction (percent_off OU amount_off en centimes, duration: once/repeating/forever).",
            parameters={"name": {"type": "string", "required": True}, "percent_off": {"type": "number"}, "amount_off": {"type": "integer"}, "currency": {"type": "string"}, "duration": {"type": "string"}, "duration_in_months": {"type": "integer"}, "max_redemptions": {"type": "integer"}},
            handler=stripe_create_coupon_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_list_coupons",
            description="Liste les coupons Stripe.",
            parameters={"limit": {"type": "integer"}},
            handler=stripe_list_coupons_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_delete_coupon",
            description="Supprime un coupon Stripe.",
            parameters={"coupon_id": {"type": "string", "required": True}},
            handler=stripe_delete_coupon_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        # ── Remboursements ──
        HandlerDef(
            name="stripe_create_refund",
            description="Rembourse un paiement (total ou partiel si amount spécifié en centimes).",
            parameters={"payment_intent_id": {"type": "string", "required": True}, "amount": {"type": "integer"}, "reason": {"type": "string"}},
            handler=stripe_create_refund_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_list_refunds",
            description="Liste les remboursements (filtrable par payment_intent_id).",
            parameters={"payment_intent_id": {"type": "string"}, "limit": {"type": "integer"}},
            handler=stripe_list_refunds_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        # ── Solde ──
        HandlerDef(
            name="stripe_get_balance",
            description="Récupère le solde du compte Stripe (disponible + en attente).",
            parameters={},
            handler=stripe_get_balance_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        # ── CLI Management ──
        HandlerDef(
            name="stripe_cli_status",
            description="Vérifie le statut de la Stripe CLI (installée, en cours, webhook secret).",
            parameters={},
            handler=stripe_cli_status_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_cli_start",
            description="Démarre la Stripe CLI pour recevoir les webhooks en local (stripe listen).",
            parameters={},
            handler=stripe_cli_start_handler, category="stripe", source_module="handlers.stripe_api",
        ),
        HandlerDef(
            name="stripe_cli_stop",
            description="Arrête la Stripe CLI.",
            parameters={},
            handler=stripe_cli_stop_handler, category="stripe", source_module="handlers.stripe_api",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
