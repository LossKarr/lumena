---
name: disk-space-crisis-manager
description: Gère les situations d'urgence de disque plein (>90% utilisé). Utilise ce skill quand le disque est presque plein, qu'il faut libérer de l'espace, nettoyer des fichiers temporaires ou archiver des données volumineuses.
keywords: [disque plein, disk full, espace disque, stockage plein, liberer espace, nettoyage disque, disk space, cleanup, espace insuffisant, more space, free space, saturation disque]
---

# Disk Space Crisis Manager

Diagnostique un disque presque plein et **propose** un plan de libération — sans rien
supprimer automatiquement.

## ⛔ Règles de sécurité (NON négociables)

- **Aucune suppression automatique.** Toute suppression exige une **confirmation
  explicite de l'utilisateur** sur une liste de candidats précise.
- **INTERDIT en action automatique** : `del /q/f/s`, `rmdir /S /Q`,
  `Clear-RecycleBin -Force`, `docker system prune -f`, `cleanmgr /sagerun`, ou toute
  suppression récursive globale (`C:\`, `%TEMP%`…).
- **Par défaut, uniquement des commandes de DIAGNOSTIC en lecture seule.**
- Jamais de suppression **hors workspace** sans validation explicite, nominale, par
  l'utilisateur.

## Workflow obligatoire

### 1. Diagnostic (lecture seule)
- Espace disque : `run_command("wmic logicaldisk get size,freespace,caption")`
- Gros dossiers (LISTER, ne pas toucher) :
  `run_command("powershell Get-ChildItem -Path C:\\ -Recurse -ErrorAction SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 15 FullName, Length | Format-Table -AutoSize")`
- Taille du TEMP (mesure seule) :
  `run_command("powershell (Get-ChildItem -Path $env:TEMP -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1GB")`

### 2. Rapport des candidats
- Présenter une **liste claire** : chemin, taille, âge, type (temp / cache / rendu / backup).
- Trier par espace récupérable décroissant.
- Marquer ce qui est **sûr** (temp, cache, rendus intermédiaires) vs **sensible** (données utilisateur).

### 3. Dry-run
- Annoncer **exactement** ce qui serait supprimé et **combien** d'espace serait libéré.
- Ne **rien** exécuter à cette étape.

### 4. Confirmation explicite
- Demander à l'utilisateur de valider la liste (ou un sous-ensemble).
- Sans « oui » explicite → **ne rien supprimer**.

### 5. Suppression bornée (après confirmation seulement)
- Supprimer **uniquement les éléments confirmés**, un par un, via les outils fichier natifs
  (`delete_file`) ou une commande **ciblée sur un chemin précis** — jamais un wildcard récursif global.
- Préférer **vider la corbeille / déplacer vers archive** plutôt que suppression définitive
  quand c'est possible.

### 6. Vérification & journal
- Re-mesurer l'espace libre.
- Journaliser les actions (`write_journal`) : quoi, où, combien libéré.

## Priorités de libération (du plus sûr au plus sensible)
1. Fichiers temporaires / caches (`%TEMP%`, `__pycache__`, `.pytest_cache`)
2. Rendus vidéo/images intermédiaires dans `workspace/`
3. Téléchargements volumineux inutiles (après confirmation)
4. Backups anciens (après confirmation — vérifier qu'une copie existe)

## Notes
- Toujours vérifier qu'un fichier n'est pas critique avant de le proposer.
- En cas de doute, **proposer** et laisser l'utilisateur trancher.
- Si le problème persiste, suggérer extension de stockage ou archivage externe.
