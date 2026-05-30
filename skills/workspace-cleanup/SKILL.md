---
name: workspace-cleanup
description: Nettoie, archive et organise le workspace Lumena. Utilise ce skill pour supprimer les vieux projets, archiver les fichiers inutilisés, ranger le workspace, libérer de la place ou réorganiser les dossiers de travail.
keywords: [workspace, nettoyer, archiver, cleanup, vieux fichiers, old files, ranger, organiser, supprimer projet, archiver projet, trier fichiers, nettoyer dossier, menage]
---

# Workspace Cleanup

Inventorie et **propose** un nettoyage du `workspace/` — sans rien supprimer
automatiquement, et **jamais** hors du workspace.

## ⛔ Règles de sécurité (NON négociables)

- **Aucune suppression automatique.** Toute suppression exige une **confirmation
  explicite de l'utilisateur** sur une liste de candidats précise.
- **INTERDIT** : `rmdir /S /Q`, `del` récursif, `shutil.rmtree`, ou toute suppression
  récursive de dossier en action automatique.
- **Suppression UNIQUEMENT à l'intérieur de `workspace/`.** Jamais ailleurs sur le disque,
  même si l'utilisateur pointe un autre chemin, sans validation explicite et nominale.
- Toujours **conserver** les dossiers marqués `keep.txt` ou contenant `.git`.

## Workflow obligatoire

### 1. Inventaire (lecture seule)
- Lister les dossiers de `workspace/` avec `list_directory`.
- Repérer les dossiers datés (`YYYY-MM-DD`), calculer leur âge.
- Repérer `.log`/`.tmp`/`.cache`, `__pycache__`, `.pytest_cache`, `node_modules`.

### 2. Liste des candidats
- Présenter un tableau : chemin (relatif au workspace), âge, taille estimée, raison.
- Exclure d'office tout dossier avec `keep.txt` ou `.git`.

### 3. Dry-run
- Annoncer **exactement** ce qui serait supprimé/archivé et l'espace estimé libéré.
- Ne **rien** exécuter à cette étape.

### 4. Confirmation explicite
- Demander validation de la liste (ou d'un sous-ensemble).
- Sans « oui » explicite → **ne rien supprimer**.
- Si > 5 éléments : reconfirmer le périmètre.

### 5. Suppression bornée (après confirmation seulement)
- Supprimer **uniquement les éléments confirmés**, via les outils fichier natifs
  (`delete_file`), chemin par chemin, **dans `workspace/`** exclusivement.
- Préférer **archiver** (`create_zip` / déplacer vers un dossier `archive/`) plutôt que
  supprimer définitivement quand c'est possible.

### 6. Journal
- Journaliser chaque action (`write_journal` ou `data/cleanup_log.txt`) : quoi, où, quand.

## Exemples

**Input** : « Nettoie le workspace des projets de plus de 3 jours »
**Comportement attendu** :
```
[1] Inventaire (lecture seule) → 15 dossiers
[2] Candidats (> 3 jours, hors keep.txt/.git) :
    - workspace/2026-03-25/projet-test   (7 j, ~120 Mo)
    - workspace/2026-03-26/demo          (6 j, ~80 Mo)
[3] Dry-run : 2 dossiers, ~200 Mo récupérables
[4] → Demande de confirmation à l'utilisateur AVANT toute suppression
```

**Input** : « Planifie un nettoyage automatique tous les lundis »
**Comportement attendu** : planifier via `schedule_task` une **session d'inventaire +
proposition** (jamais une suppression auto sans confirmation).

## Notes
- Le nettoyage planifié **propose** ; il ne supprime pas sans validation.
- Vérifier les permissions avant toute action.
- En cas de doute sur un dossier, le **garder** et le signaler.
