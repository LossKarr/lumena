---
name: automation
description: "À utiliser pour l'automatisation via n8n : workflows, templates, exécutions, webhooks. RÈGLE LUMENA PRIORITAIRE : utilise TOUJOURS les outils natifs `n8n_*`. Commencer par lister/templates ; ne créer/activer/supprimer un workflow qu'avec confirmation."
keywords: [n8n, automatisation, workflow, automation, webhook, integration, zapier like, scenario, template workflow, declencheur, trigger]
license: Lumena - usage interne
---

# Automation (n8n) — Outils natifs

⛔ **Lister d'abord, agir ensuite.** Ne **créer/activer/supprimer** un workflow qu'après
**confirmation** de l'utilisateur. Privilégier les **templates** existants avant de créer.

## Table de routage : besoin → outil natif

| Tu veux… | Outil |
|---|---|
| Vérifier l'état de n8n | `n8n_status` |
| Lister les workflows | `n8n_list_workflows` |
| Voir un workflow | `n8n_get_workflow` |
| Lister / chercher des templates | `n8n_list_templates` · `n8n_search_online_templates` |
| Créer depuis un template | `n8n_create_from_template` *(confirmation)* |
| Créer un workflow | `n8n_create_workflow` *(confirmation)* |
| Modifier un workflow | `n8n_update_workflow` *(confirmation)* |
| Activer / désactiver | `n8n_activate_workflow` · `n8n_deactivate_workflow` *(confirmation)* |
| Déclencher / webhook | `n8n_trigger_workflow` · `n8n_trigger_webhook` |
| Voir les exécutions | `n8n_list_executions` · `n8n_get_execution` |
| Supprimer un workflow | `n8n_delete_workflow` *(confirmation explicite)* |

## Règles de sécurité
1. **Lecture d'abord** : `n8n_status` / `n8n_list_workflows` avant toute action.
2. **Confirmation** avant create / update / activate / deactivate.
3. **Confirmation explicite** avant `n8n_delete_workflow`.
4. Préférer un **template** (`n8n_list_templates`) à un workflow créé de zéro quand c'est possible.
