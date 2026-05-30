---
name: webapp-testing
description: "À utiliser pour tester/vérifier une application web locale : ouvrir une page, cliquer, remplir, screenshot, lire les logs/erreurs. RÈGLE LUMENA PRIORITAIRE : Lumena possède 68 outils natifs `browser_*` (Playwright intégré). Utilise TOUJOURS ces outils. N'écris JAMAIS de script Python Playwright sauf si l'utilisateur demande explicitement un test automatisé durable/réutilisable."
keywords: [test, playwright, tester application, tester site, verifier, navigateur, browser, screenshot, debug ui, test fonctionnel, test frontend, test automatise, e2e, end to end]
license: Lumena - usage interne
---

# Tester une web app — Utilise les outils natifs `browser_*`

⛔ **NE CODE PAS de script Python Playwright** pour tester une page. Lumena pilote
déjà un navigateur via 68 outils `browser_*`. Le script Python n'est justifié QUE si
l'utilisateur demande explicitement un **test automatisé durable** (suite e2e réutilisable).

## Table de routage : tâche → outil natif

| Tu veux… | Outil natif |
|---|---|
| Démarrer le navigateur | `browser_start` |
| Lancer le serveur du site local | `serve_website` (puis `stop_website_server`) |
| Ouvrir une page / URL | `browser_navigate` |
| Attendre le chargement | `browser_wait_for` |
| Lire le contenu / texte | `browser_get_content`, `browser_get_text` |
| Voir la structure (sélecteurs) | `browser_dom_state` |
| Cliquer | `browser_click`, `browser_click_smart`, `browser_click_index` |
| Saisir du texte | `browser_type`, `browser_type_index` |
| Screenshot (preuve visuelle) | `browser_screenshot`, `browser_screenshot_labels` |
| Voir les requêtes réseau | `browser_network_requests` |
| Voir les dialogues / erreurs JS | `browser_dialog_log`, `browser_handle_dialog` |
| Gérer les onglets | `browser_new_tab`, `browser_switch_tab`, `browser_tabs` |

## Workflow recommandé

```
1. serve_website            → lancer le site local (si pas déjà servi)
2. browser_start            → ouvrir le navigateur
3. browser_navigate         → aller sur la page
4. browser_dom_state        → repérer les éléments
5. browser_click / type     → reproduire le parcours
6. browser_screenshot       → capturer le résultat (preuve)
7. browser_network_requests / browser_dialog_log → vérifier erreurs
```

## Règles

1. Reconnaissance d'abord (`browser_dom_state`/`browser_get_content`) puis action.
2. Toujours un `browser_screenshot` à la fin = preuve visuelle du test.
3. Script Python Playwright **uniquement** si l'utilisateur veut une suite de tests
   automatisée pérenne — sinon, les outils natifs suffisent et sont plus rapides.
