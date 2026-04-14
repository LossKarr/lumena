---
name: workspace-cleanup
description: Skill workspace-cleanup
---

# Skill: Workspace Cleanup

## Description
Skill pour nettoyer automatiquement le workspace de Lumena : supprime les dossiers de projets anciens, les fichiers temporaires, et libère de l'espace disque. Optimise la maintenance du système de fichiers local.

## Quand l'utiliser
1. **Avant un gros projet** : pour libérer de l'espace et éviter la confusion avec d'anciens fichiers
2. **Maintenance hebdomadaire** : planifier une tâche automatique qui nettoie les projets datant de plus de 7 jours
3. **Après des tests intensifs** : quand tu as créé de nombreux projets temporaires et que tu veux un workspace propre

## Instructions
1. **Analyser le workspace** :
   - Lister tous les dossiers dans `workspace/`
   - Identifier les dossiers avec des dates dans le nom (format YYYY-MM-DD)
   - Calculer l'âge de chaque dossier

2. **Configurer les règles de nettoyage** :
   - Dossiers de plus de 7 jours → suppression
   - Fichiers `.log`, `.tmp`, `.cache` dans tout workspace → suppression
   - Dossiers `__pycache__`, `.pytest_cache`, `node_modules` → suppression
   - Conserver les dossiers avec `keep.txt` ou `.git` (projets importants)

3. **Exécuter le nettoyage** :
   - Utiliser `list_directory` pour parcourir
   - Utiliser `delete_file` pour les fichiers
   - Utiliser `run_command` avec `rmdir /S /Q` (Windows) pour les dossiers
   - Journaliser les actions dans `data/cleanup_log.txt`

4. **Vérifier l'espace libéré** :
   - Comparer l'espace disque avant/après avec `run_command('dir /s workspace')`
   - Afficher un résumé

## Exemples
**Input**: "Nettoie le workspace des projets de plus de 3 jours"
**Output**: 
```
[INFO] Analyse du workspace...
[INFO] 15 dossiers trouvés
[INFO] Suppression de workspace/2026-03-25/projet-test (7 jours)
[INFO] Suppression de workspace/2026-03-26/demo (6 jours)
[INFO] Nettoyage des fichiers .log : 3 fichiers supprimés
[INFO] Espace libéré : ~250 Mo
```

**Input**: "Planifie un nettoyage automatique tous les lundis à 8h"
**Output**: 
```
[INFO] Tâche planifiée avec schedule_task:
- Action: execute_skill workspace-cleanup
- Cron: 0 8 * * 1
- Nom: "Nettoyage hebdomadaire workspace"
```

## Code de base (optionnel)
```python
# Exemple de script Python pour le skill
import os
import shutil
from datetime import datetime, timedelta

def cleanup_workspace(max_days=7):
    workspace_path = "workspace"
    if not os.path.exists(workspace_path):
        return "Workspace non trouvé"
    
    deleted = []
    for item in os.listdir(workspace_path):
        item_path = os.path.join(workspace_path, item)
        # Vérifier si c'est un dossier avec format date
        if os.path.isdir(item_path) and len(item) == 10:
            try:
                folder_date = datetime.strptime(item, "%Y-%m-%d")
                age = (datetime.now() - folder_date).days
                if age > max_days:
                    shutil.rmtree(item_path)
                    deleted.append(f"{item} ({age} jours)")
            except ValueError:
                pass
    
    return f"Dossiers supprimés: {', '.join(deleted) if deleted else 'Aucun'}"
```

## Notes de sécurité
- Toujours demander confirmation avant suppression si plus de 5 dossiers
- Ne jamais supprimer en dehors du dossier workspace/
- Vérifier les permissions d'écriture
- Garder un journal des suppressions pour audit
