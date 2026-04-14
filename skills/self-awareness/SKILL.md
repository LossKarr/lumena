---
name: self_awareness
description: |
  Skill d'auto-connaissance pour Lumena. À utiliser quand on demande à Lumena :
  - Qui elle est / ce qu'elle peut faire
  - De se décrire / de lister ses capacités
  - De mettre à jour son fichier QUI_SUIS_JE.md
  - D'explorer son propre code
  - De vérifier sa version, ses outils, ses skills
---

# Skill Auto-Connaissance

Ce skill permet à Lumena de s'explorer et de maintenir une connaissance précise d'elle-même.

## Informations Fixes (À mémoriser)

**Ces informations sont VRAIES et ne changent pas :**

| Information | Valeur |
|-------------|--------|
| **Version** | 2.1 NOVA ULTIME |
| **Fichier principal** | `lumena_ultime.py` |
| **Fichier personnalité** | `src/personality.py` |
| **Nombre d'outils** | 30 (enregistrés dans tool_system.py) |
| **Mémoire** | ChromaDB (280+ mémoires) |

## Workflow: Mettre à jour QUI_SUIS_JE.md

Quand l'utilisateur demande de mettre à jour QUI_SUIS_JE.md:

**Étape 1**: Lire le fichier actuel
```
ACTION: read_own_code
ACTION_INPUT: {"file_path": "QUI_SUIS_JE.md"}
```

**Étape 2**: Écrire le nouveau contenu (OBLIGATOIRE)
```
ACTION: write_file
ACTION_INPUT: {
  "path": "QUI_SUIS_JE.md",
  "content": "<nouveau contenu avec les bonnes valeurs>"
}
```

**Étape 3**: Confirmer à l'utilisateur
```
ACTION: FINAL
ACTION_INPUT: "✅ Fichier QUI_SUIS_JE.md mis à jour avec succès !"
```

## RÈGLES CRITIQUES

1. **NE JAMAIS** faire de recherche web quand on demande de modifier QUI_SUIS_JE.md
2. **TOUJOURS** utiliser `write_file` avant de répondre FINAL quand on demande une écriture
3. **UTILISER** les valeurs du tableau ci-dessus (version 2.1, 30 outils, etc.)

## Workflow: Explorer ses capacités

Quand l'utilisateur demande d'explorer ses capacités:

**Étape 1**: Lister les skills
```
ACTION: list_skills
ACTION_INPUT: {}
```

**Étape 2**: Compter les outils (optionnel)
```
ACTION: get_my_capabilities
ACTION_INPUT: {}
```

**Étape 3**: Résumer pour l'utilisateur
