---
name: stripe
description: "À utiliser pour les paiements Stripe : produits, prix, clients, abonnements, factures, liens de paiement, checkout, remboursements, webhooks. RÈGLE LUMENA PRIORITAIRE : utilise TOUJOURS les outils natifs `stripe_*`. Ne jamais inventer un montant, une devise ou un client ; confirmer avant toute création de paiement/facture/abonnement."
keywords: [stripe, paiement, payment, facture, invoice, abonnement, subscription, prix, price, produit, product, checkout, lien de paiement, payment link, remboursement, refund, client stripe, webhook stripe]
license: Lumena - usage interne
---

# Stripe — Paiements via outils natifs

⛔ **Ne jamais inventer** un montant, une devise, un email/ID client, ou un prix.
Demander/confirmer ces valeurs avant toute création. **Confirmer explicitement** avant
de créer un paiement, une facture ou un abonnement (action financière réelle).

## Table de routage : besoin → outil natif

| Tu veux… | Outil |
|---|---|
| Créer un **produit** | `stripe_create_product` |
| Créer un **prix** | `stripe_create_price` |
| **Lien de paiement** | `stripe_create_payment_link` |
| **Session checkout** | `stripe_create_checkout_session` |
| Créer un **client** | `stripe_create_customer` |
| Créer / envoyer une **facture** | `stripe_create_invoice` · `stripe_send_invoice` |
| **Abonnement** : créer / annuler | `stripe_create_subscription` · `stripe_cancel_subscription` |
| **Remboursement** | `stripe_create_refund` |
| Lister / chercher (lecture) | `stripe_list_products`/`_prices`/`_customers`/`_invoices` · `stripe_search_customers` |
| Solde | `stripe_get_balance` |
| **Webhooks locaux** (CLI) | `stripe_cli_start` · `stripe_cli_status` · `stripe_cli_stop` |

## Règles de sécurité
1. **Confirmation explicite** avant toute création financière (paiement, facture, abonnement, remboursement).
2. **Jamais d'invention** : montant, devise, client, prix → demandés ou vérifiés via les outils `*_list_*`.
3. **Distinguer test / live** : vérifier le mode de la clé (`sk_test_` vs `sk_live_`) et le signaler à l'utilisateur.
4. **Lecture d'abord** : `stripe_list_*` / `stripe_search_customers` pour retrouver un objet existant avant d'en créer un.
5. **Webhooks** : vérifier `stripe_cli_status` avant de conclure qu'un webhook fonctionne (le secret CLI change à chaque redémarrage, inutilisable en prod).
