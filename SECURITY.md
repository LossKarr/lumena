# Security Policy — Lumena

## Versions supportées

| Version | Support sécurité |
|---------|-----------------|
| 1.0.9   | ✅ Actif         |
| < 1.0   | ❌ Non supporté  |

## Signaler une vulnérabilité

**Ne pas ouvrir d'issue publique.** Envoyer un email à l'adresse indiquée dans le profil du dépôt avec :

- Description de la vulnérabilité
- Étapes de reproduction
- Impact estimé (CVSS si possible)

Délai de réponse visé : **72 heures**. Un correctif sera publié avant toute divulgation publique.

---

## Architecture de sécurité

Lumena implémente une défense en profondeur sur 14 axes. Chaque mécanisme est couvert par des tests dédiés (100+ tests de sécurité).

### 1. Authentification — Bearer Token

Toutes les routes API (sauf 14 endpoints publics documentés) exigent un header `Authorization: Bearer <token>` vérifié par `verify_admin_token()`.

- Token configuré via `LUMENA_ADMIN_TOKEN` (variable d'environnement)
- Fail-closed : après le setup initial, un token vide retourne 401
- Hot-reload : le token est relu depuis `os.environ` à chaque requête
- `/api/auth/config` accessible uniquement depuis localhost (127.0.0.1 / ::1)
- `/api/shutdown` : double garde (token + localhost)
- Test d'exhaustivité : `test_auth_coverage.py` scanne toutes les routes et vérifie la présence du guard

### 2. Protection SSRF

Module centralisé `src/utils/url_safety.py` — fonction `assert_url_safe()` appliquée sur tous les points d'entrée réseau :

- **Schemes bloqués** : `file://`, `data://`, `ftp://`, `gopher://`
- **IPs bloquées** : privées (RFC 1918), loopback, link-local, réservées
- **Anti-DNS rebinding** : résolution DNS et vérification de l'IP résolue
- **Points d'application** : Playwright (`navigate`, `open_tab`), `fetch_url()`, `web_crawler.crawl()`, `open_url()` (Computer Use)

### 3. Sandbox Docker

Exécution de commandes utilisateur dans des containers isolés (`src/utils/docker_sandbox.py`) :

- 3 modes : `auto` (défaut), `always`, `never`
- Limites : mémoire 512 Mo, 1 CPU, 256 PIDs max
- Réseau `none` par défaut (`bridge` uniquement pour `pip install` / `npm install`)
- Image : `python:3.12-slim`
- Sandbox Node.js séparé pour le rendu vidéo Remotion (`node:20-slim`)

### 4. Sanitisation de commandes

Module `src/utils/command_sanitizer.py` — whitelist + blocklist :

- **~100 exécutables autorisés** (whitelist explicite)
- **Patterns destructeurs bloqués** : `rm -rf`, `del /s`, `format`, `shutdown`, fork bomb, `reg delete`, `cipher`, `diskpart`, `bcdedit`
- **Verbes PowerShell bloqués** : `Remove`, `Stop`, `Kill`, `Set` (avec exceptions)
- **Anti-injection** : détection `$()`, backticks, parsing quote-aware
- Validation par sous-commande dans les chaînes pipées

### 5. Anti-injection de prompt

Module `src/reasoning/handlers/security.py` — 20 patterns détectés :

- Instruction override / role manipulation
- Exfiltration de variables d'environnement (`curl $(...env...)`)
- Callbacks C2 vers RFC 1918
- Base64 pipe decode, `eval`/`exec`/`__import__`
- Shellcode hexadécimal, `${IFS}`
- **Normalisation homographes Unicode** (cyrillique/grec → latin) avant analyse

### 6. Protection Path Traversal

Module `src/tools/file_guardrails.py` — 4 gardes :

| Garde | Rôle |
|-------|------|
| `check_path_boundary()` | Chemin doit rester dans `lumena_root` ou `workspace_root` |
| `check_read_blacklist()` | Bloque `.env`, `data/mail/`, `data/browser_profiles/` |
| `check_write_blacklist()` | Bloque `data/`, `models/`, `backups/` |
| `check_delete_allowed()` | Suppression autorisée uniquement dans `workspace/` |

- `resolve_user_path()` applique le boundary check automatiquement
- `sanitize_workspace_relative_path()` supprime les segments `..`
- Validation côté setup : chemins système (`C:/Windows`, `/etc`, `/usr`, `/root`) bloqués

### 7. Rate Limiting

Token bucket par IP en mémoire (3 catégories) :

| Catégorie | Routes | Limite par défaut |
|-----------|--------|-------------------|
| `expensive` | `/api/chat`, `/api/upload` | 20 req/min |
| `default` | Toutes les autres | 200 req/min |
| `health` | `/api/health`, `/api/status` | 600 req/min |

- Configurable via `LUMENA_RATE_EXPENSIVE`, `LUMENA_RATE_DEFAULT`, `LUMENA_RATE_HEALTH`
- Réponse 429 avec header `Retry-After`

### 8. CORS

Restreint à localhost par défaut (`http://localhost:<port>`, `http://127.0.0.1:<port>`). Extensible via `LUMENA_CORS_ORIGINS` (CSV).

### 9. Content Security Policy & Headers

**CSP stricte** appliquée sur toutes les réponses :

```
default-src 'self'
script-src 'self' https://unpkg.com 'unsafe-inline'
frame-ancestors 'none'
base-uri 'self'
form-action 'self'
```

**Headers de sécurité** :

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(self), geolocation=()`

### 10. Vérification de signature Webhook

- **Stripe** : `stripe.Webhook.construct_event()` — HMAC vérifié, 403 si invalide
- **WhatsApp** : `hmac.compare_digest()` avec SHA-256 sur `app_secret` (timing-safe)

### 11. Écriture atomique & File Locking

- `atomic_write_json()` / `atomic_write_text()` : écriture `tmp + rename` (atomique POSIX/NTFS)
- `safe_read_json()` : quarantaine automatique des fichiers JSON corrompus
- `ProcessFileLock` : lock basé sur `O_CREAT | O_EXCL` avec détection de stale lock via PID check

### 12. Gestion des secrets

- Toutes les clés API en variables d'environnement (jamais hardcodées)
- `.env` dans `.gitignore`
- `.env` / `.env.local` / `.env.production` bloqués en lecture par les outils IA
- Comparaisons HMAC timing-safe (`hmac.compare_digest`)

### 13. Outils offensifs — Authorization Gate

Les outils de sécurité offensive (scan de ports, capture de trafic, reconnaissance de domaine) exigent un paramètre `authorization` non vide — l'utilisateur doit explicitement justifier chaque utilisation. Durée de capture max 30s, taille max 2 Mo.

### 14. Protections additionnelles

| Mécanisme | Description |
|-----------|-------------|
| OpenAPI désactivé en prod | `docs_url`, `redoc_url`, `openapi_url` = `None` si `LUMENA_SETUP_COMPLETE=1` |
| Thread-safety | 5 `threading.Lock()` pour l'état partagé (IDE, sessions, pipeline) |
| Session memory cap | Historique limité à 500 messages, compaction extractive au-delà |
| Context overflow guard | > 85% du `max_context` → purge de l'historique ancien |
| Retry intra-provider | 2 retries (backoff 1s/3s) sur 429/500/502/503/ReadTimeout |
| Validation params outils | Params requis + empty string vérifiés avant exécution |
| SSH injection guard | Single-quote escaping pour PowerShell `Invoke-Command` |

---

## Couverture OWASP Top 10

| # | Risque OWASP | Couverture Lumena |
|---|-------------|-------------------|
| A01 | Broken Access Control | Auth token, path traversal guards, localhost-only routes |
| A02 | Cryptographic Failures | HMAC timing-safe, secrets en env vars |
| A03 | Injection | Command sanitizer, prompt injection detector, Unicode normalization |
| A04 | Insecure Design | Authorization gate sur outils offensifs, fail-closed auth |
| A05 | Security Misconfiguration | CSP, CORS restrictif, OpenAPI désactivé en prod |
| A06 | Vulnerable Components | `requirements-lock.txt` avec versions pinées |
| A07 | Auth Failures | Bearer token, rate limiting, 429/401/403 |
| A08 | Data Integrity Failures | Webhook signature verification (Stripe, WhatsApp) |
| A09 | Logging & Monitoring | Logs structurés, alertes proactives |
| A10 | SSRF | `assert_url_safe()` sur tous les points d'entrée réseau |
