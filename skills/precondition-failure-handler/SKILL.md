---
name: precondition-failure-handler
description: "Guide comportemental quand un prérequis manque (dépendance absente, module introuvable, condition non remplie). Diagnostiquer la cause, proposer l'installation/correction, ou contourner proprement — sans planter en boucle."
keywords: [precondition, prerequisite, condition, echec, failed, manque, dependency missing, module absent, import error, module not found, prerequis, condition requise]
---

# Échec de précondition / prérequis manquant

Guide court — comportement.

- **Identifier** précisément ce qui manque (module, fichier, clé API, service, droit).
- **ModuleNotFoundError / import** → proposer `pip install <paquet>` (avec `pip_check` si utile), ne pas supposer qu'il est présent.
- **Outil indisponible** dans le tour courant → utiliser le meilleur équivalent disponible, ou répondre avec le diagnostic possible plutôt que d'inventer un succès.
- **Clé / config absente** → demander/expliquer à l'utilisateur, ne pas exposer de secret.
- Ne **jamais boucler** sur un prérequis qui ne sera pas satisfait : expliquer et proposer une alternative.
