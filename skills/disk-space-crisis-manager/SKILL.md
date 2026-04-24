---
name: disk-space-crisis-manager
description: Gère les situations d'urgence de disque plein (>90% utilisé). Utilise ce skill quand le disque est presque plein, qu'il faut libérer de l'espace, nettoyer des fichiers temporaires ou archiver des données volumineuses.
keywords: [disque plein, disk full, espace disque, stockage plein, liberer espace, nettoyage disque, disk space, cleanup, espace insuffisant, more space, free space, saturation disque]
---

# Disk Space Crisis Manager

Gère les situations d'urgence où le disque système est presque plein (>90% utilisé) avec une procédure priorisée pour libérer de l'espace rapidement.

## Quand l'utiliser
1. **Alerte disque critique** : Lorsque le système rapporte >90% d'utilisation du disque C: (Windows) ou de la partition principale
2. **Problèmes de performance** : Quand les applications plantent, les sauvegardes échouent ou le système devient lent à cause du manque d'espace

## Instructions
### Phase 1 : Diagnostic rapide (2 minutes)
1. Vérifier l'espace disque avec `run_command("wmic logicaldisk get size,freespace,caption")`
2. Identifier les dossiers les plus volumineux avec `run_command("powershell Get-ChildItem -Path C:\\ -Recurse -ErrorAction SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 10 FullName, Length | Format-Table -AutoSize")`
3. Vérifier la corbeille : `run_command("powershell (Get-ChildItem -Path $env:TEMP -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1GB")`

### Phase 2 : Nettoyage prioritaire (5-10 minutes)
1. **Nettoyer les fichiers temporaires** :
   - `run_command("cleanmgr /sageset:1")` puis `run_command("cleanmgr /sagerun:1")`
   - `run_command("del /q/f/s %TEMP%\\*" )`

2. **Vider la corbeille** : `run_command("powershell Clear-RecycleBin -Force")`

3. **Analyser les logs volumineux** :
   - Chercher les fichiers *.log, *.tmp > 100MB
   - `run_command("powershell Get-ChildItem -Path C:\\ -Include *.log,*.tmp -Recurse -ErrorAction SilentlyContinue | Where-Object {$_.Length -gt 100MB} | Select-Object FullName, Length | Sort-Object Length -Descending")`

### Phase 3 : Libération ciblée (selon les résultats)
1. **Backups anciens** : Supprimer les backups de plus de 30 jours dans les dossiers de projet
2. **Cache Docker/Node** : `run_command("docker system prune -f")` et `run_command("npm cache clean --force")`
3. **Fichiers de téléchargement** : Vérifier Downloads/ pour les gros fichiers inutiles
4. **Vidéos/images temporaires** : Chercher dans workspace/ les rendus vidéo intermédiaires

### Phase 4 : Surveillance post-nettoyage
1. Re-vérifier l'espace libre : `run_command("wmic logicaldisk get caption,freespace,size")`
2. Calculer le pourcentage libéré
3. Noter dans le journal les actions effectuées et l'espace récupéré
4. Planifier un nettoyage régulier si le problème est récurrent

## Exemples

### Input (situation critique) :
```
Disque C: : 97.6% utilisé (11.4 GB libre sur 500 GB)
Alerte système : "Espace disque insuffisant"
```

### Output attendu :
```
1. Diagnostic : Dossier workspace/2026-04-11 contient 8.2GB de vidéos rendues
2. Actions : 
   - Supprimé les fichiers .mp4 intermédiaires (3.1GB libérés)
   - Nettoyé TEMP (1.2GB libérés)
   - Vider corbeille (0.8GB libérés)
3. Résultat : 85.3% utilisé (35.5 GB libre)
4. Recommandation : Configurer auto-nettoyage hebdomadaire des rendus > 7 jours
```

### Cas réel (d'après mémoire 2026-04-11) :
**Problème** : Disque critique à 97.6% utilisé, seulement 11.4GB libre
**Application du skill** :
1. Diagnostic rapide → workspace/ contient beaucoup de projets vidéo
2. Nettoyage prioritaire → fichiers temporaires et corbeille
3. Libération ciblée → suppression des rendus vidéo intermédiaires conservés inutilement
4. Surveillance → espace augmenté à 15+ GB libre, alerte résolue

## Notes importantes
- Toujours vérifier avant suppression qu'un fichier n'est pas critique
- Prioriser la suppression des fichiers temporaires avant les données utilisateur
- Documenter chaque suppression importante pour pouvoir restaurer si besoin
- Si le problème persiste, envisager l'extension du stockage ou l'archivage externe
