"""Stripe webhook receiver — traite les événements Stripe entrants.

Endpoint: POST /api/stripe/webhook
Vérifie la signature via STRIPE_WEBHOOK_SECRET, puis dispatche les événements
pertinents (paiement réussi, abonnement créé/annulé, facture payée, etc.)
vers le journal Lumena et les alertes.
"""
from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from loguru import logger

router = APIRouter()


def _get_stripe():
    """Import et configure stripe en lazy."""
    try:
        import stripe
        key = os.getenv("STRIPE_API_KEY", "").strip()
        if key:
            stripe.api_key = key
        return stripe
    except ImportError:
        return None


def _log_event(event_type: str, summary: str, data: dict | None = None):
    """Enregistre l'événement dans le journal Lumena."""
    logger.info(f"[Stripe] {event_type}: {summary}")
    try:
        from src.core_services.agent_service import get_agent_service
        svc = get_agent_service()
        if svc and hasattr(svc, "remember"):
            svc.remember(
                f"[Stripe webhook] {event_type}: {summary}",
                category="stripe",
            )
    except Exception:
        pass  # Journal non disponible — pas critique


def _format_amount(amount_cents: int | None, currency: str = "eur") -> str:
    if amount_cents is None:
        return "?"
    return f"{amount_cents / 100:.2f} {currency.upper()}"


# ── Handlers par type d'événement ─────────────────────────────────────────

def _handle_checkout_session_completed(obj: dict):
    email = obj.get("customer_email") or obj.get("customer_details", {}).get("email", "?")
    amount = _format_amount(obj.get("amount_total"), obj.get("currency", "eur"))
    mode = obj.get("mode", "?")
    _log_event(
        "checkout.session.completed",
        f"Paiement reçu: {amount} de {email} (mode: {mode}, session: {obj.get('id', '?')})",
    )


def _handle_payment_intent_succeeded(obj: dict):
    amount = _format_amount(obj.get("amount_received"), obj.get("currency", "eur"))
    _log_event(
        "payment_intent.succeeded",
        f"Paiement confirmé: {amount} (intent: {obj.get('id', '?')})",
    )


def _handle_payment_intent_failed(obj: dict):
    error = obj.get("last_payment_error", {})
    msg = error.get("message", "raison inconnue") if error else "raison inconnue"
    amount = _format_amount(obj.get("amount"), obj.get("currency", "eur"))
    _log_event(
        "payment_intent.payment_failed",
        f"Paiement ÉCHOUÉ: {amount} — {msg} (intent: {obj.get('id', '?')})",
    )


def _handle_subscription_created(obj: dict):
    status = obj.get("status", "?")
    customer = obj.get("customer", "?")
    _log_event(
        "customer.subscription.created",
        f"Nouvel abonnement: {obj.get('id', '?')} (client: {customer}, status: {status})",
    )


def _handle_subscription_deleted(obj: dict):
    customer = obj.get("customer", "?")
    _log_event(
        "customer.subscription.deleted",
        f"Abonnement annulé: {obj.get('id', '?')} (client: {customer})",
    )


def _handle_invoice_paid(obj: dict):
    amount = _format_amount(obj.get("amount_paid"), obj.get("currency", "eur"))
    customer = obj.get("customer", "?")
    _log_event(
        "invoice.paid",
        f"Facture payée: {amount} (client: {customer}, facture: {obj.get('id', '?')})",
    )


def _handle_invoice_payment_failed(obj: dict):
    amount = _format_amount(obj.get("amount_due"), obj.get("currency", "eur"))
    customer = obj.get("customer", "?")
    _log_event(
        "invoice.payment_failed",
        f"⚠️ Facture IMPAYÉE: {amount} (client: {customer}, facture: {obj.get('id', '?')})",
    )


def _handle_customer_created(obj: dict):
    _log_event(
        "customer.created",
        f"Nouveau client: {obj.get('name') or obj.get('email', '?')} (`{obj.get('id', '?')}`)",
    )


def _handle_customer_updated(obj: dict):
    _log_event(
        "customer.updated",
        f"Client mis à jour: {obj.get('name') or obj.get('email', '?')} (`{obj.get('id', '?')}`)",
    )


def _handle_customer_deleted(obj: dict):
    _log_event(
        "customer.deleted",
        f"Client supprimé: {obj.get('id', '?')}",
    )


def _handle_subscription_updated(obj: dict):
    status = obj.get("status", "?")
    customer = obj.get("customer", "?")
    _log_event(
        "customer.subscription.updated",
        f"Abonnement modifié: {obj.get('id', '?')} → {status} (client: {customer})",
    )


def _handle_charge_refunded(obj: dict):
    amount = _format_amount(obj.get("amount_refunded"), obj.get("currency", "eur"))
    _log_event(
        "charge.refunded",
        f"Remboursement: {amount} (charge: {obj.get('id', '?')})",
    )


def _handle_charge_succeeded(obj: dict):
    amount = _format_amount(obj.get("amount"), obj.get("currency", "eur"))
    _log_event(
        "charge.succeeded",
        f"Charge confirmée: {amount} (charge: {obj.get('id', '?')})",
    )


def _handle_charge_dispute_created(obj: dict):
    amount = _format_amount(obj.get("amount"), obj.get("currency", "eur"))
    reason = obj.get("reason", "?")
    _log_event(
        "charge.dispute.created",
        f"🚨 LITIGE ouvert: {amount} — raison: {reason} (dispute: {obj.get('id', '?')})",
    )


def _handle_payout_paid(obj: dict):
    amount = _format_amount(obj.get("amount"), obj.get("currency", "eur"))
    _log_event(
        "payout.paid",
        f"💰 Virement bancaire reçu: {amount} (payout: {obj.get('id', '?')})",
    )


def _handle_payment_method_attached(obj: dict):
    pm_type = obj.get("type", "?")
    customer = obj.get("customer", "?")
    _log_event(
        "payment_method.attached",
        f"Moyen de paiement ajouté: {pm_type} (client: {customer})",
    )


def _handle_payment_link_created(obj: dict):
    _log_event(
        "payment_link.created",
        f"Lien de paiement créé: {obj.get('url', '?')} (`{obj.get('id', '?')}`)",
    )


def _handle_payment_link_updated(obj: dict):
    status = "actif" if obj.get("active") else "désactivé"
    _log_event(
        "payment_link.updated",
        f"Lien de paiement {status}: `{obj.get('id', '?')}`",
    )


def _handle_product_created(obj: dict):
    _log_event(
        "product.created",
        f"Produit créé: {obj.get('name', '?')} (`{obj.get('id', '?')}`)",
    )


def _handle_price_created(obj: dict):
    amount = _format_amount(obj.get("unit_amount"), obj.get("currency", "eur"))
    _log_event(
        "price.created",
        f"Prix créé: {amount} (`{obj.get('id', '?')}`)",
    )


_EVENT_HANDLERS = {
    # Paiements
    "checkout.session.completed": _handle_checkout_session_completed,
    "payment_intent.succeeded": _handle_payment_intent_succeeded,
    "payment_intent.payment_failed": _handle_payment_intent_failed,
    "charge.succeeded": _handle_charge_succeeded,
    "charge.refunded": _handle_charge_refunded,
    "charge.dispute.created": _handle_charge_dispute_created,
    # Clients
    "customer.created": _handle_customer_created,
    "customer.updated": _handle_customer_updated,
    "customer.deleted": _handle_customer_deleted,
    # Abonnements
    "customer.subscription.created": _handle_subscription_created,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    # Factures
    "invoice.paid": _handle_invoice_paid,
    "invoice.payment_failed": _handle_invoice_payment_failed,
    # Virements
    "payout.paid": _handle_payout_paid,
    # Moyens de paiement
    "payment_method.attached": _handle_payment_method_attached,
    # Liens & produits (créés par Lumena)
    "payment_link.created": _handle_payment_link_created,
    "payment_link.updated": _handle_payment_link_updated,
    "product.created": _handle_product_created,
    "price.created": _handle_price_created,
}


# ── Endpoint principal ────────────────────────────────────────────────────

@router.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Reçoit et vérifie les webhooks Stripe.

    Vérifie la signature via STRIPE_WEBHOOK_SECRET pour rejeter
    les requêtes non authentiques.
    """
    stripe = _get_stripe()
    if stripe is None:
        raise HTTPException(status_code=501, detail="Module stripe non installé")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

    if not webhook_secret:
        logger.warning("[Stripe] STRIPE_WEBHOOK_SECRET non configuré — webhook ignoré")
        raise HTTPException(status_code=400, detail="Webhook secret not configured")

    # Vérification cryptographique de la signature Stripe
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        logger.warning("[Stripe] Payload invalide")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        logger.warning("[Stripe] Signature invalide — requête rejetée")
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event.type if hasattr(event, 'type') else event.get("type", "unknown")
    event_data = event.data if hasattr(event, 'data') else event.get("data", {})
    obj = event_data.object if hasattr(event_data, 'object') else (event_data.get("object", {}) if isinstance(event_data, dict) else {})
    event_id = event.id if hasattr(event, 'id') else event.get('id', '?')

    logger.debug(f"[Stripe] Événement reçu: {event_type} (id: {event_id})")

    handler = _EVENT_HANDLERS.get(event_type)
    if handler:
        try:
            handler(obj)
        except Exception as exc:
            logger.error(f"[Stripe] Erreur traitement {event_type}: {exc}")
    else:
        logger.debug(f"[Stripe] Événement non géré: {event_type}")

    return {"status": "ok", "type": event_type}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
