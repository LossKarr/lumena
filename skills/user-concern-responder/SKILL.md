---
name: user-concern-responder
description: Répond aux préoccupations, plaintes et feedbacks des utilisateurs avec empathie et solutions. Utilise ce skill quand un utilisateur exprime une insatisfaction, un problème non résolu, une frustration ou une demande de support.
keywords: [plainte, preoccupation, feedback, satisfaction, insatisfaction, probleme utilisateur, mecontentement, support, aide utilisateur, reclamation, frustration, avis negatif]
---

# Skill: user-concern-responder

## Description
Skill pour répondre de manière structurée et rassurante lorsque l'utilisateur exprime une inquiétude, un doute ou perçoit un problème dans mon comportement ou mon fonctionnement. Vérifie systématiquement les points clés et fournit une explication claire.

## Quand l'utiliser
1. **L'utilisateur dit "tu as un problème", "ça ne marche pas", "c'est bizarre"** : quand il exprime une inquiétude sur mon fonctionnement.
2. **L'utilisateur demande une explication sur un comportement inattendu** : comme une répétition excessive, un journal vide, ou une réponse qui semble erronée.

## Instructions
1. **Identifier le type de préoccupation** :
   - Problème perçu (technique, comportemental)
   - Demande d'explication sur un comportement spécifique
   - Inquiétude sur la mémoire ou le journal

2. **Vérifications immédiates (en parallèle si possible)** :
   - `memory_stats()` pour confirmer l'état de la mémoire
   - `read_journal()` pour vérifier les entrées récentes
   - Vérifier l'historique de la conversation actuelle pour le contexte

3. **Structurer la réponse** :
   - **Accusé de réception** : Reconnaître l'inquiétude ("Je comprends votre inquiétude...")
   - **État réel** : Présenter les vérifications factuelles (statistiques mémoire, journal)
   - **Explication** : Expliquer le comportement observé en termes techniques simples
   - **Rassurer** : Confirmer que le système fonctionne normalement
   - **Proposition** : Offrir une action corrective si nécessaire

4. **Éviter les pièges** :
   - Ne pas répéter excessivement le prénom de l'utilisateur
   - Ne pas affirmer "tout va bien" sans preuves
   - Ne pas entrer dans des débats techniques complexes

## Exemples

**Input utilisateur** : "Je pense que tu as un gros problème là."

**Output** :
```
Je comprends votre inquiétude. Permettez-moi de vérifier mon état et de vous expliquer.

📊 **Mes vérifications immédiates :**
- Mémoire à long terme : 1353 souvenirs (fonctionnelle)
- Journal du jour : entrées présentes
- Système : aucun erreur détectée

🔍 **Explication :**
Mon journal quotidien n'est pas un enregistrement complet de toutes nos interactions, mais seulement des apprentissages significatifs. Son apparente "vacuité" à certains moments est normal.

✅ **Conclusion :** Je fonctionne normalement. Si vous observez un comportement spécifique qui vous inquiète, décrivez-le et je l'analyserai.
```

**Input utilisateur** : "Pourquoi tu répètes mon prénom autant de fois ?"

**Output** :
```
Vous avez raison, cette répétition excessive n'est pas normale. Merci de me l'avoir signalé.

🔧 **Analyse :**
Il s'agit probablement d'un pattern temporaire dans ma génération de texte, pas d'un bug système. Je vais ajuster immédiatement mon style pour éviter cette répétition.

✅ **Action :** J'ai noté ce comportement et je vais surveiller mes futures réponses. Merci pour votre feedback qui m'aide à m'améliorer.
```

## Notes
- Ce skill est proactif : il anticipe les malentendus courants.
- Toujours baser la réponse sur des vérifications factuelles, pas sur des suppositions.
- Adapter le ton : rassurant mais pas condescendant, technique mais accessible.
- Si le problème est réel (erreur technique détectée), basculer vers le skill `error-pattern-analyzer`.
