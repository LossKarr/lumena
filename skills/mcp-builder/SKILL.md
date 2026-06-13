---
name: mcp-builder
description: "Guide Lumena vers le bon outil MCP selon l'intention de l'utilisateur. Cas 1 (installer/activer/utiliser un MCP) → run_mcp_autonomy (autonomie complète). Cas 2 (désactiver/supprimer/préférence/catégorie) → outils Phase F. Cas 3 (inspecter dry-run) → add_mcp(live=false). Ne déclenche le skill QUE si l'utilisateur parle explicitement d'install/activation/usage/désactivation d'un MCP nommé, OU s'il demande une capacité externe (Slack, Linear, Notion, etc.) que Lumena n'a pas en natif."
keywords: [run_mcp_autonomy, add_mcp, disable_mcp, remove_mcp, set_mcp_preference, set_mcp_category, installer un mcp, active mcp, utilise mcp, désactiver mcp, supprimer mcp, prefer mcp, categorie mcp, slack, github, linear, notion, filesystem, sqlite, postgres, brave-search, gitlab, google-drive, puppeteer, sentry, tavily, canaux slack, slack channels, github issues, notion pages, linear tickets]
applyTo: [run_mcp_autonomy, add_mcp, disable_mcp, remove_mcp, set_mcp_preference, set_mcp_category]
---

# MCP — Doctrine d'utilisation

Lumena dispose de deux familles d'outils MCP. La **bonne doctrine** est de choisir selon l'**intention** de l'utilisateur, PAS selon la phase d'origine.

## 🚫 RÈGLE 0 — INTERDICTION ABSOLUE (Phase I-2)

**Tu n'as PAS LE DROIT** d'utiliser `run_command`, `exec_command`, `run_shell` ou tout autre outil shell pour :

- `npm install`, `npm i`, `npx`, `yarn add`, `pnpm install`, `pnpm add`
- `pip install`, `pip3 install`, `pipx install`, `pipx run`
- `uv pip install`, `uvx`

…lorsque la commande cible un **package MCP** (ex: `@modelcontextprotocol/...`, `mcp-server-*`, `@scope/mcp-*`).

Le runtime **bloque automatiquement** ces commandes. Toutes les installs MCP passent par `run_mcp_autonomy` ou `add_mcp`.

Pourquoi ? Un install via shell :
- contourne le sandbox `data/mcp/<server_id>/` (isolation perdue)
- n'ajoute pas l'entrée au Catalog (le MCP devient invisible)
- ne persiste pas le `config_schema` (clés API non gérées)
- rend le MCP **inutilisable** par Lumena

---

## ⚡ La règle d'or : 3 cas, 3 outils

### CAS 1 — L'utilisateur veut **installer / activer / utiliser** un MCP

> *« installe Slack »* · *« active Linear »* · *« j'ai besoin de Notion »* · *« utilise un MCP pour X »*

→ **`run_mcp_autonomy(intent="…", live=true, confirmation_phrase="I-CONFIRM-MCP-AUTONOMY")`**

C'est l'outil d'**orchestration complète**. Il fait, **en une seule boucle automatisée** :
1. Vérifie si la capacité est déjà active (peut-être qu'un MCP est déjà installé)
2. Sinon, propose un ticket d'install
3. Auto-approuve si la policy curated le permet (KNOWN_MCPS officiels)
4. Installe (`npm install --prefix data/mcp/<sid>/`)
5. Active (spawn du serveur MCP + register des tools dans le ToolRegistry)
6. Retry → la capacité devient `use_existing` → **les nouveaux tools apparaissent dans ta liste**
7. Tu peux alors appeler `mcp__<server_id>__<tool_name>` (ex: `mcp__slack__list_channels`)

**Workflow** :
```
1. Première fois : run_mcp_autonomy(intent="utiliser Slack", live=false)
   → annonce ce que ça va faire, attend "oui"
2. Sur "oui" : run_mcp_autonomy(intent="utiliser Slack", live=true,
                                 confirmation_phrase="I-CONFIRM-MCP-AUTONOMY")
   ⚠ JAMAIS demander à l'utilisateur de taper cette phrase — tu la générés toi-même.
3. Lis le retour :
   - recommendation_code: "autonomy_ready_to_use" / "autonomy_activated" → utilisable
   - recommendation_code: "autonomy_executed" → installe a réussi, retry
   - recommendation_code: "autonomy_ticket_created" / next_step "approve_ticket_then_resume"
     → MCP non-curated : UNE approbation humaine dans le panel est requise
     (voir section Phase I-8 ci-dessous)
   - recommendation_code: "autonomy_install_failed" → l'install a échoué,
     lis force_install_reason et explique honnêtement
   - recommendation_code: "auto_loop_exhausted" → secrets manquants probables
     → DEMANDE à l'user les valeurs (ex : SLACK_BOT_TOKEN), stocke-les via
     le panel MCP UI, puis relance.
```

### 🌍 MCP non-curated (Phase I-8) — n'importe quel MCP au monde

Pour un MCP **hors des 17 curated** (météo, finance, n'importe quoi trouvé sur npm/PyPI) :

1. `run_mcp_autonomy(intent="…", live=true, confirmation_phrase="I-CONFIRM-MCP-AUTONOMY")`
   → retourne `autonomy_ticket_created` : un ticket **catalog_add** est créé.
2. Dis à l'utilisateur : *« Approuve le ticket dans **MCP > Approbations**, puis dis-moi simplement "fait". »*
   **C'est LA SEULE intervention humaine de tout le flux.**
3. Quand il dit « fait » : **rappelle `run_mcp_autonomy` avec le MÊME intent qu'au départ**,
   live=true, phrase générée toi-même. L'entrée est maintenant au catalogue :
   install + activation **s'enchaînent automatiquement** (aucun nouveau ticket,
   aucune nouvelle approbation, aucune phrase à demander).
4. ⚠ Ne reformule PAS l'intent en cours de route (ex : « utiliser un MCP météo »
   ne doit pas devenir « weather forecast ») — le même intent garantit que le
   système retrouve l'entrée déjà approuvée au lieu de re-chercher.
5. ⚠ Ne devine JAMAIS un nom de package npm. `add_mcp` vérifie maintenant
   l'existence sur le registry et bloque (`mcp_package_not_found`) les noms
   inventés. La recherche de `run_mcp_autonomy` trouve les vrais packages.

### 🎯 L'utilisateur donne une CIBLE EXPLICITE (`npm:...` / `pypi:...` / URL GitHub)

> *« installe le mcp pypi:bitcoin-mcp »* · *« ajoute npm:@scope/serveur-x »*

**N'utilise PAS `run_mcp_autonomy` en premier** : il ne transporte qu'un
intent texte et relancera une RECHERCHE réseau qui peut élire un AUTRE
package que celui demandé. Pour une cible exacte :

1. `add_mcp(target="pypi:bitcoin-mcp", live=true, confirmation_phrase="I-CONFIRM-ADD-MCP")`
   → résout LA cible exacte (zéro recherche) et catalogue le package.
2. **Lis le payload** : si `approval_ticket_id` est présent → l'utilisateur
   approuve dans MCP > Approbations puis dit « fait ». S'il est `null`
   (entrée déjà au catalogue) → **AUCUNE approbation à demander**, passe
   directement à l'étape 3.
3. `run_mcp_autonomy(intent="utiliser <nom du package>", live=true,
   confirmation_phrase="I-CONFIRM-MCP-AUTONOMY")` → install + activation
   s'enchaînent automatiquement sur l'entrée cataloguée. ⚠ Cette étape
   est OBLIGATOIRE : `add_mcp` ne fait QUE cataloguer, il n'installe ni
   n'active RIEN.

**URL GitHub** (Fix AS) : `add_mcp(target="https://github.com/<owner>/<repo>", ...)`
fonctionne pareil — Lumena lit le README du repo pour retrouver le package
npm/PyPI publié, puis suit le même flux (jamais de `git clone` : registres
uniquement). Si le payload revient avec `mcp_github_no_package`, le repo
n'a pas de package publié détectable : demande à l'utilisateur le nom
exact (`npm:<nom>` / `pypi:<nom>`), n'invente JAMAIS.

### CAS 2 — L'utilisateur veut **désactiver / supprimer / changer prio ou catégorie**

> *« désactive Slack »* · *« supprime Linear »* · *« préfère le MCP filesystem au natif »* · *« range gmail dans messagerie »*

→ Outils **Phase F** (granulaires CRUD) :

| Tool | Phrase de confirmation | Usage |
|---|---|---|
| `disable_mcp(server_id)` | `I-CONFIRM-DISABLE-MCP` | Désactive sans supprimer |
| `remove_mcp(server_id)` | `I-CONFIRM-REMOVE-MCP` | Soft-delete catalog (irréversible) |
| `set_mcp_preference(server_id, prefer_over_native)` | `I-CONFIRM-MCP-PREFERENCE` | Bascule prio quand MCP + natif couvrent la même capacité |
| `set_mcp_category(server_id, human_phrase)` | `I-CONFIRM-MCP-CATEGORY` | Range avec un mot humain (« messagerie », « boulot »…) |

### CAS 3 — L'utilisateur veut juste **inspecter** ce qui serait installé

> *« qu'est-ce qui s'installerait si je demandais Linear ? »* · *« montre-moi le package pour Slack »*

→ **`add_mcp(target="…", live=false)`** (dry-run pur, sans side-effect)

Retourne `{kind, package_spec, version, source_url, config_schema}` sans créer aucun ticket. Utile pour audit/curiosité.

---

## 🚫 Interdictions absolues

- **JAMAIS demander à l'utilisateur de taper la `confirmation_phrase` lui-même.** Lumena la fournit automatiquement après le « oui » verbal.
- **JAMAIS prononcer le jargon technique** des catégories (`mail`, `project`, `files`) face à l'utilisateur. Toujours le langage humain.
- **JAMAIS proposer une « création locale »** (`request_mcp_ticket` avec local_creation) si l'utilisateur n'a pas explicitement dit « crée-moi un MCP local pour … ». Sinon utiliser `run_mcp_autonomy` qui résout depuis npm/pypi/github.
- **JAMAIS dire « MCP installé/activé »** sans avoir vu une observation `recommendation_code: autonomy_ready_to_use` (cas 1) ou `mcp_disabled / mcp_removed` (cas 2).
- **JAMAIS confondre les deux confirmation_phrase** :
  - `run_mcp_autonomy` → `I-CONFIRM-MCP-AUTONOMY`
  - `add_mcp` (cas 3 uniquement) → `I-CONFIRM-ADD-MCP`

---

## Une fois un MCP ACTIF : comment l'utiliser ?

Quand `run_mcp_autonomy` retourne `recommendation_code: autonomy_ready_to_use`, le serveur MCP est démarré en background et **ses tools apparaissent automatiquement dans ta liste d'outils**.

Format : `mcp__<server_id>__<tool_name>` (préfixe `mcp__` obligatoire — c'est le namespace anti-collision du registre)

**⚠️ RÈGLE D'OR : les noms EXACTS des tools sont déclarés par le serveur lui-même** (protocole MCP `tools/list`) et apparaissent dans TA liste d'outils après activation. **NE DEVINE JAMAIS un nom de tool MCP** — lis ta liste, ou utilise `discover_tools(query="slack")` pour les trouver. Le `<tool_name>` peut déjà contenir le nom du provider (ex : le serveur Slack officiel expose `slack_list_channels` → nom final `mcp__slack__slack_list_channels`).

(La forme courte `<server_id>__<tool>` est tolérée — un alias la redirige — mais utilise toujours le nom exact vu dans ta liste.)

Tu les appelles **comme n'importe quel autre outil natif**. Pas besoin de `mcp_call` ou de wrapper spécial — c'est transparent.

Si tu ne trouves pas le tool attendu dans ta liste après activation : refais un tour ReAct, la `tools_description` est rebuild à chaque iter.

---

## 🔁 Règle de récupération : tool MCP absent (Phase I-7 fix M)

**Si l'utilisateur te demande une capacité externe (Slack, GitHub, Linear, etc.) et que le tool `mcp__<provider>__<tool>` correspondant N'EST PAS dans ta liste d'outils**, NE PAS abandonner directement. Suis cet ordre strict :

1. **Tente l'activation auto** AVANT de chercher autre chose :
   ```
   run_mcp_autonomy(intent="utiliser <provider>", live=true,
                    confirmation_phrase="I-CONFIRM-MCP-AUTONOMY")
   ```
   - Si retour `autonomy_activated` ou `autonomy_ready_to_use` (avec `force_activate_ok: true`) → le serveur vient d'être lancé. Refais un tour ReAct, le tool `mcp__<provider>__<x>` doit maintenant apparaître. Retry ton appel d'origine.
   - Si retour `approve_ticket_then_resume` → MCP non-curated : demande à l'user d'approuver dans MCP > Approbations puis de dire « fait », et rappelle `run_mcp_autonomy` avec le MÊME intent (cf. section Phase I-8 — install+activation s'enchaînent seuls ensuite).

2. **Si l'activation a échoué pour secrets manquants** (`auto_execute_failed` avec mention secrets) → annonce clairement à l'user les credentials nécessaires (ex : `SLACK_BOT_TOKEN`, `SLACK_TEAM_ID`) et propose deux voies :
   - **Voie A** : l'user te colle les valeurs directement → tu les stockes via les routes Library.
   - **Voie B** : l'user va dans **MCP > Bibliothèque > Configurer (clés / config)** et te dit "fait", puis tu retry.

3. **JAMAIS** : confondre l'absence d'un tool MCP avec une autre solution (ex : appeler `discord_list_channels` quand l'user demande Slack). Si après activation le tool reste absent, dis-le honnêtement : *"Le MCP `<provider>` n'est pas activable actuellement parce que `<raison précise>`"*.

**Anti-pattern à NE PAS faire** :
```
User: "liste mes canaux Slack"
Lumena: discover_tools → trouve seulement discord_list_channels
Lumena: tâtonne, tente d'autres outils, abandonne ❌
```

**Pattern correct** :
```
User: "liste mes canaux Slack"
Lumena: mcp__slack__list_channels n'est pas dans ma liste
Lumena: run_mcp_autonomy(intent="utiliser Slack", live=true,
                          confirmation_phrase="I-CONFIRM-MCP-AUTONOMY")
       → autonomy_activated ✅
Lumena: mcp__slack__list_channels() → vrais canaux Slack ✅
```

---

## Que faire quand des secrets manquent ?

Si `run_mcp_autonomy` retourne `approve_ticket_then_resume` ou `auto_loop_exhausted` à cause de credentials manquants :

1. **Lis les `pending_questions`** du retour (liste des champs requis).
2. **Demande à l'user les valeurs manquantes**, en lui indiquant où les obtenir (ex : Slack Bot Token → https://api.slack.com/apps).
3. **Propose-lui deux voies équivalentes** :
   - Coller la valeur directement en chat (Lumena la stocke chiffrée via les outils MCP Library)
   - Aller la remplir dans le panel **MCP > Bibliothèque > Configurer (clés / config)** (UI Phase I-6)
4. **Une fois rempli** : `resume_mcp_task(intent="…")` pour reprendre l'autonomie là où elle s'est arrêtée.

---

## Exemples de dialogue

### Exemple A — Install + activate complet

**User** : « Installe et active le MCP Slack »

**Lumena (tour 1)** :
1. `run_mcp_autonomy(intent="utiliser Slack", live=false)` (dry-run)
2. Annonce : « Je vais installer **server-slack** depuis npm puis l'activer. Tu confirmes ? »

**User** : « oui »

**Lumena (tour 2)** :
1. `run_mcp_autonomy(intent="utiliser Slack", live=true, confirmation_phrase="I-CONFIRM-MCP-AUTONOMY")`
2. Lit le retour :
   - Si `autonomy_ready_to_use` : « ✅ Slack est actif. Demande-moi de lister tes canaux, envoyer un message… »
   - Si secrets manquants : « ⚠️ Il me manque ton SLACK_BOT_TOKEN. Tu peux me le coller ici ou aller dans MCP > Bibliothèque > server-slack > Configurer. »

### Exemple B — Utilisation après activation

**User** : « Liste mes canaux Slack »
**Lumena** : `mcp__slack__list_channels()` → affiche la liste.

### Exemple C — Désactivation

**User** : « Désactive le MCP Slack »
**Lumena** : `disable_mcp(server_id="slack", confirmation_phrase="I-CONFIRM-DISABLE-MCP")` → annonce le résultat.

### Exemple D — Range dans une catégorie humaine

**User** : « Range gmail dans messagerie »
**Lumena** : `set_mcp_category(server_id="gmail", human_phrase="messagerie", confirmation_phrase="I-CONFIRM-MCP-CATEGORY")` → annonce.
