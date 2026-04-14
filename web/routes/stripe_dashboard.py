"""stripe_dashboard.py — API endpoints pour le panel Stripe du dashboard."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from web.routes import deps

router = APIRouter()


def _stripe_client():
    import stripe as _stripe
    key = os.getenv("STRIPE_API_KEY", "").strip()
    if not key:
        return None, "STRIPE_API_KEY non configurée"
    _stripe.api_key = key
    return _stripe, None


def _pi_to_dict(pi) -> dict:
    """Convertit un PaymentIntent SDK v15 (attributs) en dict JSON-safe."""
    return {
        "id": pi.id,
        "amount": pi.amount,
        "currency": pi.currency.upper(),
        "status": pi.status,
        "description": pi.description or "",
        "created": pi.created,
        "customer_email": getattr(pi, "receipt_email", "") or "",
    }


@router.get("/api/stripe/dashboard/summary")
async def stripe_summary(_auth=Depends(deps.verify_admin_token)):
    """Solde, revenus du mois, stats rapides."""
    stripe, err = _stripe_client()
    if err:
        return JSONResponse({"error": err}, status_code=503)
    try:
        import asyncio
        from datetime import datetime, timezone

        def _fetch():
            now = datetime.now(timezone.utc)
            month_start = int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp())

            # Solde
            balance = stripe.Balance.retrieve()
            available = sum(b.amount for b in balance.available)
            pending = sum(b.amount for b in balance.pending)
            currency = balance.available[0].currency.upper() if balance.available else "EUR"

            # Paiements du mois via charges
            charges = stripe.Charge.list(limit=100, created={"gte": month_start})
            month_revenue = sum(c.amount for c in charges.data if c.paid and not c.refunded)
            month_count = sum(1 for c in charges.data if c.paid and not c.refunded)

            # Derniers paiements (10)
            recent = stripe.PaymentIntent.list(limit=10)
            payments = [_pi_to_dict(pi) for pi in recent.data]

            return {
                "balance": {"available": available, "pending": pending, "currency": currency},
                "month": {"revenue": month_revenue, "count": month_count, "currency": currency},
                "recent_payments": payments,
            }

        data = await asyncio.to_thread(_fetch)
        return data
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/stripe/dashboard/payments")
async def stripe_payments(limit: int = 25, starting_after: str = "", _auth=Depends(deps.verify_admin_token)):
    """Liste paginée des PaymentIntents."""
    stripe, err = _stripe_client()
    if err:
        return JSONResponse({"error": err}, status_code=503)
    try:
        import asyncio

        def _fetch():
            kwargs: dict[str, Any] = {"limit": min(limit, 100)}
            if starting_after:
                kwargs["starting_after"] = starting_after
            pi_list = stripe.PaymentIntent.list(**kwargs)
            items = [_pi_to_dict(pi) for pi in pi_list.data]
            return {"payments": items, "has_more": pi_list.has_more}

        return await asyncio.to_thread(_fetch)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/stripe/dashboard/subscriptions")
async def stripe_subscriptions(limit: int = 25, _auth=Depends(deps.verify_admin_token)):
    """Liste des abonnements."""
    stripe, err = _stripe_client()
    if err:
        return JSONResponse({"error": err}, status_code=503)
    try:
        import asyncio

        def _fetch():
            subs = stripe.Subscription.list(limit=min(limit, 100), status="all")
            items = []
            for s in subs.data:
                price_obj = s.items.data[0].price if s.items.data else None
                items.append({
                    "id": s.id,
                    "status": s.status,
                    "customer": s.customer,
                    "customer_email": "",
                    "amount": price_obj.unit_amount if price_obj else 0,
                    "currency": price_obj.currency.upper() if price_obj else "EUR",
                    "interval": price_obj.recurring.interval if (price_obj and price_obj.recurring) else "",
                    "price_id": price_obj.id if price_obj else "",
                    "current_period_end": s.current_period_end,
                    "cancel_at_period_end": s.cancel_at_period_end,
                    "created": s.created,
                })
            return {"subscriptions": items, "has_more": subs.has_more}

        return await asyncio.to_thread(_fetch)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/stripe/dashboard/products")
async def stripe_products(limit: int = 25, _auth=Depends(deps.verify_admin_token)):
    """Liste des produits avec leurs prix."""
    stripe, err = _stripe_client()
    if err:
        return JSONResponse({"error": err}, status_code=503)
    try:
        import asyncio

        def _fetch():
            products = stripe.Product.list(limit=min(limit, 100), active=True)
            items = []
            for p in products.data:
                prices_raw = stripe.Price.list(product=p.id, limit=5, active=True)
                prices = []
                for pr in prices_raw.data:
                    prices.append({
                        "id": pr.id,
                        "unit_amount": pr.unit_amount or 0,
                        "currency": pr.currency.upper(),
                        "recurring": {"interval": pr.recurring.interval} if pr.recurring else None,
                    })
                items.append({
                    "id": p.id,
                    "name": p.name,
                    "description": p.description or "",
                    "active": p.active,
                    "created": p.created,
                    "prices": prices,
                })
            return {"products": items, "has_more": products.has_more}

        return await asyncio.to_thread(_fetch)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/stripe/dashboard/payment-link")
async def create_payment_link_quick(body: dict, _auth=Depends(deps.verify_admin_token)):
    """Créer un lien de paiement rapide."""
    stripe, err = _stripe_client()
    if err:
        return JSONResponse({"error": err}, status_code=503)
    try:
        import asyncio
        amount = int(body.get("amount_cents", 0))
        description = body.get("description", "Paiement")
        currency = body.get("currency", "eur").lower()
        if amount <= 0:
            return JSONResponse({"error": "Montant invalide"}, status_code=400)

        def _create():
            price = stripe.Price.create(
                unit_amount=amount,
                currency=currency,
                product_data={"name": description},
            )
            link = stripe.PaymentLink.create(line_items=[{"price": price.id, "quantity": 1}])
            return {"url": link.url, "id": link.id}

        return await asyncio.to_thread(_create)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
