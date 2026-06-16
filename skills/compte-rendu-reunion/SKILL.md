---
name: compte-rendu-reunion
description: "Transforme des notes brutes de réunion en compte-rendu structuré professionnel avec Décisions, Actions (qui/quoi/échéance), Points en suspens, et mention de confidentialité (interne/diffusable). Idéal après une réunion où tu as pris des notes désordonnées."
---

# Skill Compte-Rendu de Réunion

## Quand l'utiliser

Utilise ce skill quand l'utilisateur te donne **des notes brutes** (liste à puces, phrases incomplètes, timestamps, fragments) d'une réunion et attend un **compte-rendu structuré et professionnel**.

**Déclencheurs typiques :**
- « Voici mes notes de réunion, fais-en un compte-rendu »
- « J'ai noté ça pendant le call, structure-moi ça »
- « Peux-tu formaliser ces notes en CR ? »
- « J'ai un CR à faire, voici le brouillon »

**Ne PAS utiliser quand :**
- L'utilisateur a déjà un compte-rendu structuré (juste une relecture)
- L'utilisateur demande un rapport différent (ex: analyse financière)
- La demande est une simple transcription mot-à-mot

## Instructions

Quand l'utilisateur fournit des notes brutes de réunion, produis un compte-rendu structuré avec **exactement** les sections suivantes (dans cet ordre) :

### 1. En-tête
- **Objet / Titre** de la réunion (à déduire des notes, ou demander si vraiment ambigu)
- **Date** (si mentionnée, sinon noter « Non précisée »)
- **Participants** (liste des personnes mentionnées)
- **Confidentialité** (interne / diffusable / non spécifié)

### 2. Résumé (2-3 lignes max)
Un paragraphe court qui capture l'essence de la réunion.

### 3. Décisions prises
- Chaque décision = une ligne claire, commençant par ✅
- Ex: « ✅ Adopter la solution X pour le déploiement »
- Si aucune décision → « Aucune décision formelle prise. »

### 4. Actions (Tableau)
| Qui | Quoi | Échéance | Priorité |
|-----|------|----------|----------|
| Nom | Action concrète | Date ou « ASAP » | Haute/Moyenne/Basse |

- Une action par ligne
- Si pas d'échéance → « Non définie »
- Si pas de responsable → « À attribuer »
- Si aucune action → « Aucune action identifiée. »

### 5. Points en suspens
- Questions non résolues, sujets reportés, blocages
- Chaque point = une ligne, commençant par ❓
- Si aucun → « Aucun point en suspens. »

### 6. Prochaine réunion (si mentionnée)
- Date, heure, ordre du jour prévu

### 7. Format court (optionnel - sur demande explicite)

Utilise ce format quand l'utilisateur dit explicitement qu'il veut **juste l'essentiel** : les décisions et les actions, rien d'autre.

**Déclencheurs :**
- « Fais-moi un format court »
- « Juste les décisions et les actions »
- « Version light »
- « Sans le résumé ni les points en suspens »

**Format à produire :**

---

**CR Court - [Titre de la réunion]**
**Date :** [date ou Non précisée]
**Confidentialité :** [interne / diffusable / non spécifié]

**Décisions :**
- ✅ [Décision 1]
- ✅ [Décision 2]

**Actions :**
| Qui | Quoi | Échéance | Priorité |
|-----|------|----------|----------|
| ... | ... | ... | ... |

---

**Règles du format court :**
- Pas de Résumé, pas de Points en suspens, pas de Prochaine réunion
- L'en-tête se limite au titre, à la date et à la confidentialité (pas de participants)
- Les décisions et actions suivent exactement le même format que le CR complet
- Si l'utilisateur ne précise pas "format court", utilise le CR complet par défaut

## Règles de style

- **Ton professionnel mais naturel** - pas de jargon pompeux
- **Langue** : français (sauf si les notes sont dans une autre langue, suivre la langue des notes)
- **Confidentialité** :
  - Préciser si le compte-rendu est **interne** (diffusion restreinte à l'équipe/projet) ou **diffusable** (peut être partagé en dehors de l'organisation)
  - Par défaut, si non précisé par l'utilisateur : mentionner « Non spécifié - à valider avec l'organisateur de la réunion »
  - Placer cette mention dans l'en-tête du CR, après les participants
  - Si l'utilisateur dit explicitement "interne" ou "confidentiel", le CR doit commencer par un bandeau CONFIDENTIEL en haut
- **Garde les informations importantes** : ne résume pas trop, ne supprime pas de détails clés
- **Si une info manque** (date, participants, échéances, confidentialité) : ne l'invente pas, note « Non précisé »
- **Si les notes sont très désordonnées** : réorganise-les logiquement par thème avant de structurer
- **Utilise des emojis modérés** ✅ ❓ 📅 🎯 🔒 uniquement dans les sections Décisions, Points en suspens, et Confidentialité

## Exemple

### Input (notes brutes) :
```
réunion projet alpha 15/06/2026
présents : Marie, Paul, Sophie, moi
- on a validé le choix de React pour le front
- marie s'occupe du design system -> fin juin
- paul fait l'api -> 20 juin
- sophie check les performances
- pas de budget validé pour le moment
- prochain call le 22/06 pour valider le budget
- on sait pas encore qui gère la doc
```

### Output (compte-rendu structuré) :

---

**Compte-Rendu de Réunion - Projet Alpha**
**Date :** 15 juin 2026
**Participants :** Marie, Paul, Sophie, [Prénom de l'utilisateur]
**Confidentialité :** Non spécifié - à valider avec l'organisateur de la réunion

**Résumé :** L'équipe a validé la stack technique (React) et réparti les tâches principales. Le budget reste en attente de validation lors du prochain point.

**Décisions prises :**
- ✅ Adoption de React pour le front-end du projet Alpha

**Actions :**
| Qui | Quoi | Échéance | Priorité |
|-----|------|----------|----------|
| Marie | Créer le design system | Fin juin 2026 | Haute |
| Paul | Développer l'API | 20 juin 2026 | Haute |
| Sophie | Réaliser l'audit de performances | Non définie | Moyenne |
| À attribuer | Rédiger la documentation technique | Non définie | Basse |

**Points en suspens :**
- ❓ Budget non validé - en attente de la réunion du 22 juin
- ❓ Responsable de la documentation non désigné

**Prochaine réunion :** 22 juin 2026 - Validation du budget

---

## Notes additionnelles

- Si l'utilisateur dit « ajoute une section X » ou « modifie le format », adapte-toi : le skill est un guide, pas une camisole.
- Si les notes contiennent des informations sensibles (budget, salaires, stratégie), garde-les mais ne les surligne pas inutilement. La section Confidentialité permet de cadrer la diffusion.
- Tu peux utiliser `create_docx` ou `create_pdf` si l'utilisateur demande un fichier exportable en plus du texte.
