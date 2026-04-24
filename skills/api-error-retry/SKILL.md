---
name: api-error-retry
description: Gère les erreurs API avec retry automatique et backoff exponentiel. Utilise ce skill quand une API retourne des erreurs 429, 500, 503, timeout, ou rate limit. Implémente une stratégie de retry robuste avec délai progressif.
keywords: [api error, retry, rate limit, 429, 503, 500, timeout, backoff, erreur api, relancer, rate limiting, exponential backoff, api failure, recuperation erreur]
---

# Skill: api-error-retry

## Description
Gère automatiquement les erreurs d'API et de réseau avec des stratégies de retry intelligentes (backoff exponentiel, jitter, fallback). Optimisé pour les appels web_fetch, browser_navigate, et requêtes HTTP qui échouent temporairement.

## Quand l'utiliser
1. **Scénario 1 : Site web temporairement inaccessible** - Quand `web_fetch` retourne une erreur 429 (Too Many Requests), 502 (Bad Gateway) ou timeout réseau, ce skill applique une pause exponentielle et réessaie jusqu'à 3 fois avant de basculer sur `browser_navigate` comme fallback.
2. **Scénario 2 : API externe avec rate limiting** - Quand une requête HTTP vers une API (ex: GitHub, Notion, Spotify) échoue avec un code 429 ou 503, le skill détecte les headers `Retry-After`, respecte le délai, et réessaie automatiquement sans intervention manuelle.

## Instructions
1. **Détecter l'erreur** : Intercepter les exceptions `ConnectionError`, `TimeoutError`, et les codes HTTP 429, 502, 503, 504.
2. **Appliquer la stratégie de retry** : 
   - Backoff exponentiel : attendre 2^n secondes (1s, 2s, 4s) avec un jitter aléatoire (±0.5s).
   - Maximum 3 tentatives pour les erreurs réseau, 5 pour les rate limits.
3. **Fallback intelligent** : Si `web_fetch` échoue après retries, basculer automatiquement sur `browser_navigate` pour contourner les blocages simples.
4. **Journaliser** : Enregistrer chaque tentative et son résultat dans le journal quotidien pour analyse future.

## Exemples
### Input (simulation d'erreur) :
```python
# Appel web_fetch qui échoue
try:
    content = web_fetch("https://api.example.com/data")
except Exception as e:
    # Le skill intercepte et gère
```

### Output (après retry réussi) :
```
[api-error-retry] Tentative 1/3 échouée : 429 Too Many Requests
[api-error-retry] Attente de 2.3s (backoff + jitter)...
[api-error-retry] Tentative 2/3 réussie ! Données récupérées.
[api-error-retry] Fallback non nécessaire.
```

### Cas réel :
- **Requête** : `web_fetch("https://news.ycombinator.com")` retourne 502.
- **Action du skill** : Pause 1s → retry → réussite à la 2ème tentative.
- **Résultat** : Contenu HTML récupéré sans erreur visible pour l'utilisateur.

## Implémentation
Le skill peut être implémenté comme un wrapper autour des outils `web_fetch`, `http_request`, et `browser_navigate`. Il expose une fonction `fetch_with_retry(url, max_attempts=3, fallback=True)` utilisable dans tous les scripts.
