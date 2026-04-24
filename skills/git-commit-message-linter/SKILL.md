---
name: git-commit-message-linter
description: Formate et valide les messages de commit git selon les conventions (Conventional Commits, semantic versioning). Utilise ce skill pour écrire un bon message de commit, vérifier le format, respecter les conventions feat/fix/chore/docs, ou générer un changelog.
keywords: [git, commit, message commit, conventional commits, semantic, changelog, feat, fix, chore, docs, branch, push, format commit, lint commit, bonne pratique git]
---

# Skill: git-commit-message-linter

**Date de création**: 2026-04-07

**But**: Valider et formater les messages de commit Git pour qu'ils soient clairs, cohérents et utiles pour l'historique du projet. Ce skill analyse un message de commit proposé, vérifie sa conformité à des règles prédéfinies (comme la convention conventionnelle des commits), et suggère des corrections immédiates.

## Quand l'utiliser
1. **Avant de pousser un commit** : Quand un développeur a écrit un message de commit vague comme "fix bug" ou "update", ce skill peut l'aider à le reformuler en un message structuré (ex: "fix(api): handle null response in user endpoint").
2. **Dans un pipeline CI/CD** : Intégré dans un hook pre-commit ou dans une action GitHub, pour rejeter automatiquement les messages non conformes et fournir des conseils de correction aux contributeurs.

## Instructions
1. **Fournir le message de commit à analyser** : Le message peut être en une seule ligne (sujet) ou multi-lignes (sujet + corps).
2. **Le skill applique les règles suivantes** :
   - Le sujet doit commencer par un type valide parmi : feat, fix, docs, style, refactor, test, chore, perf, ci, build, revert.
   - Le sujet doit avoir une portée optionnelle entre parenthèses après le type (ex: fix(api)).
   - Le sujet doit être en minuscules, sans point final, et ne pas dépasser 72 caractères.
   - Le sujet doit être séparé du corps par une ligne vide.
   - Le corps (si présent) doit décrire le « pourquoi » plus que le « quoi », et chaque ligne ne doit pas dépasser 100 caractères.
3. **Le skill retourne** :
   - Un booléen `is_valid`.
   - Une liste `errors` détaillant chaque violation.
   - Une suggestion `corrected_message` si des corrections automatiques sont possibles (ex: ajustement de casse, ajout de type).
   - Une explication `rule` pour chaque erreur.
4. **Si le message est invalide**, l'utilisateur doit être invité à le corriger en suivant les suggestions.

## Exemples

**Input** :
```
update user login
```

**Output** :
```json
{
  "is_valid": false,
  "errors": [
    {
      "rule": "subject_must_start_with_type",
      "message": "Le sujet doit commencer par un type valide (feat, fix, docs, etc.).",
      "position": "line 1"
    },
    {
      "rule": "subject_lowercase",
      "message": "Le sujet doit être en minuscules.",
      "position": "line 1"
    }
  ],
  "corrected_message": "chore: update user login",
  "explanation": "Le type 'chore' a été ajouté car la modification semble être une tâche de maintenance. Le sujet a été converti en minuscules."
}
```

**Input** :
```
feat(auth): add two-factor authentication via SMS

- Implement Twilio integration
- Add 2FA settings page in user profile
- Store 2FA secret securely
```

**Output** :
```json
{
  "is_valid": true,
  "errors": [],
  "corrected_message": "",
  "explanation": "Le message respecte toutes les règles : type valide avec portée, sujet en minuscules, longueur correcte, et corps présent."
}
```

**Implémentation technique suggérée** :
- Utiliser une regex pour valider le format du sujet.
- Fournir une liste de types autorisés.
- Compter les caractères pour les limites.
- Pour la correction automatique : proposer le type le plus probable en fonction de mots-clés (ex: "fix" si le message contient "bug", "error").

**Intégration** :
Ce skill peut être utilisé comme outil en ligne de commande, plugin d'IDE, ou service dans un chatbot de développement pour améliorer la qualité des commits.