---
name: precondition-failure-handler
description: Skill precondition-failure-handler
---

# Skill: Precondition Failure Handler

## Description
Ce skill fournit une procédure systématique pour diagnostiquer et résoudre les erreurs "precondition_failed" qui surviennent dans le système Lumena, particulièrement celles liées à `_handler_false_success → precondition_failed`.

## Quand l'utiliser
1. **Cas concret 1** : Quand une action échoue avec le message d'erreur "_handler_false_success → precondition_failed" dans les logs ou le contexte d'erreur.
2. **Cas concret 2** : Quand une opération qui devrait normalement fonctionner (comme l'envoi d'email, la création de fichier, ou l'exécution d'une commande) échoue silencieusement avec un statut de précondition non satisfaite.

## Instructions
### Étape 1 : Identifier la source
- Vérifier les logs récents avec `memory_search("precondition_failed")`
- Consulter le contexte d'erreur dans la session actuelle (section "Erreurs récentes")
- Noter l'heure exacte et l'action qui a déclenché l'erreur

### Étape 2 : Vérifier les préconditions système
1. **État des outils** : Vérifier si l'outil concerné est disponible avec `discover_tools("nom_outil")`
2. **Permissions** : Vérifier les permissions d'écriture/lecture avec `list_directory()` sur le chemin cible
3. **Dépendances** : Pour les actions réseau, vérifier la connectivité avec `network_scan(fast=true)`
4. **État des comptes** : Pour les actions mail, vérifier que les comptes sont "✅ prêt" dans la configuration

### Étape 3 : Actions correctives
1. **Réinitialisation douce** : Attendre 30 secondes puis réessayer l'action
2. **Vérification des chemins** : S'assurer que les chemins de fichiers existent avec `find_files()` avant d'écrire
3. **Séquence correcte** : Vérifier que les actions préalables ont été complétées (ex: créer dossier avant fichier)
4. **Fallback manuel** : Si l'action automatisée échoue, exécuter manuellement les étapes avec les outils de base

### Étape 4 : Prévention
1. **Journalisation** : Après résolution, noter la cause et la solution dans le journal avec `write_journal()`
2. **Validation proactive** : Avant les actions critiques, exécuter un check rapide des préconditions
3. **Timeout management** : Ajouter des retries avec backoff pour les actions sensibles

## Exemples
### Input réel
Erreur dans le contexte : "2026-04-09T09:40 | _handler_false_success → precondition_failed"
Action tentée : Envoi d'email avec `mail_send()`

### Output attendu
1. Diagnostic : `memory_search("precondition_failed 2026-04-09")` → identification de l'action précise
2. Vérification : `discover_tools("mail_send")` → confirmer que l'outil est disponible
3. Check compte : Vérifier que `lumena_main` est bien "✅ prêt" dans la configuration mail
4. Action corrective : Si compte non prêt, utiliser `mail_account_upsert` pour reconfigurer
5. Retry : Réessayer `mail_send` avec les paramètres validés

### Code de retry (optionnel)
```python
import time

def safe_action_with_retry(action_func, max_retries=3, delay=5):
    for attempt in range(max_retries):
        try:
            result = action_func()
            if result.get("success", False):
                return result
        except Exception as e:
            if "precondition" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(delay * (attempt + 1))
                continue
            raise
    return None
```

## Notes
- Ces erreurs surviennent souvent quand une condition préalable n'est pas satisfaite (fichier manquant, compte non configuré, réseau indisponible)
- La clé est d'identifier QUEL précondition échoue spécifiquement
- Toujours vérifier l'état réel du système avant de conclure à un bug
