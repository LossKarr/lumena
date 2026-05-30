---
name: api-error-retry
description: "Guide comportemental pour gérer les erreurs API transitoires (429, 500, 502, 503, 504, timeout). Réessayer avec backoff raisonnable, respecter Retry-After. N'invente pas de fonction de retry inexistante — utilise les outils web/http natifs."
keywords: [api error, retry, rate limit, 429, 503, 500, timeout, backoff, erreur api, relancer, rate limiting, exponential backoff, api failure, recuperation erreur]
---

# Gestion des erreurs API (retry / backoff)

Guide court — comportement, pas d'outil dédié.

- **Erreurs transitoires** (429, 500, 502, 503, 504, timeout) → réessayer.
- Respecter l'en-tête **`Retry-After`** s'il est présent.
- **Backoff progressif** : attendre de plus en plus entre les tentatives (ex. 1s, 2s, 4s), 3-4 essais max.
- **Erreurs non transitoires** (400, 401, 403, 404) → ne PAS réessayer, corriger la requête.
- Passer par les outils natifs (`web_fetch`, `http_request`, `browser_*`) — **ne pas inventer** de wrapper `fetch_with_retry` qui n'existe pas.
- Après échecs répétés : expliquer clairement à l'utilisateur plutôt que boucler.
