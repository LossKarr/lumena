---
name: local-model-manager
description: "Gère les modèles locaux depuis Lumena avec les outils natifs authentifiés : recherche et recommandation Ollama/Hugging Face GGUF, installation suivie, vérification, activation, sélection, déchargement, désactivation et suppression confirmée. À utiliser quand l'utilisateur parle explicitement d'un modèle local, d'Ollama, de Hugging Face GGUF, de VRAM/RAM ou de la bibliothèque de modèles locaux."
keywords: [modele local, modeles locaux, ollama, hugging face, huggingface, gguf, quantification, vram, ram, catalogue ollama, installer modele, telecharger modele, activer modele, desactiver modele, decharger modele, supprimer modele, choisir modele, modele principal, modele code, modele vision]
applyTo: [search_local_models, inspect_local_model, list_installed_local_models, recommend_local_model, list_local_model_jobs, get_local_model_job, install_local_model, enable_local_model, disable_local_model, select_local_model, unload_local_model, verify_local_model, prepare_delete_local_model, confirm_delete_local_model]
license: Lumena - usage interne
---

# Modèles locaux — doctrine d'utilisation

Utilise exclusivement les outils natifs `*_local_model*`. Ne lance jamais `ollama`,
`huggingface-cli`, `hf`, `git clone`, `curl` ou une commande shell pour gérer un modèle.
Le manager natif centralise l'authentification, la validation des identifiants, les limites de
concurrence, le contrôle disque, les jobs persistants, l'audit et les preuves après mutation.

## Comprendre les états

Ne confonds jamais ces états et ne déduis pas l'un depuis un autre :

| État | Signification |
|---|---|
| `installed` | Les poids sont réellement présents dans Ollama. |
| `verified` | Les métadonnées et les canaris bornés ont été contrôlés. |
| `enabled` | Lumena autorise ce modèle dans son catalogue actif. |
| `assigned_roles` / sélectionné | Le modèle est affecté à `primary`, `code`, `vision` ou `web`. |
| `loaded` | Le modèle occupe actuellement de la RAM ou de la VRAM. |

`unload_local_model` libère la mémoire sans désactiver ni supprimer les poids.
`disable_local_model` retire le modèle du routage Lumena sans supprimer ses fichiers.
Une suppression retire les poids du disque et suit obligatoirement le flux en deux étapes.

## Choisir le bon outil

| Besoin | Outil |
|---|---|
| Chercher dans Ollama et Hugging Face GGUF | `search_local_models` |
| Recommander selon l'usage et le matériel | `recommend_local_model` |
| Lister les modèles réellement présents | `list_installed_local_models` |
| Inspecter un modèle précis | `inspect_local_model` |
| Installer | `install_local_model` |
| Suivre une installation | `get_local_model_job` ou `list_local_model_jobs` |
| Vérifier les capacités observées | `verify_local_model` |
| Autoriser dans Lumena | `enable_local_model` |
| Affecter à un rôle | `select_local_model` |
| Désactiver sans effacer | `disable_local_model` |
| Libérer la RAM/VRAM | `unload_local_model` |
| Préparer une suppression | `prepare_delete_local_model` |
| Exécuter après nouvelle confirmation humaine | `confirm_delete_local_model` |

## Recherche et recommandation

Commence par `search_local_models` si l'identifiant exact n'est pas connu. Utilise la référence
canonique renvoyée par l'outil ; n'invente jamais un nom, un tag, une quantification ou une URL.

Pour Hugging Face, le chemin supporté concerne les dépôts **GGUF compatibles avec Ollama**.
Ne présente pas un dépôt Transformers, Safetensors ou un modèle gated comme directement
installable si les résultats ne le prouvent pas. Respecte `gated`, `license`, `partial`,
`unknowns`, la taille et la provenance. Une donnée absente reste inconnue.

Pour aider au choix, appelle `recommend_local_model` avec l'intention adaptée : `general`,
`code`, `vision`, `reasoning` ou `embedding`. Explique les raisons et les inconnues retournées.
Une recommandation n'est ni une installation, ni une garantie de vitesse ou de qualité.

## Installation prouvée

N'appelle `install_local_model` que si le message utilisateur courant demande explicitement
l'installation ou le téléchargement du modèle concerné. Une recherche ou une recommandation
seule n'autorise aucune mutation.

1. Cherche ou inspecte pour obtenir la référence exacte et la source.
2. Appelle `install_local_model(reference=..., source="ollama"|"huggingface")`.
3. Le retour `accepted` signifie seulement que le job est accepté. Mémorise son `job_id`.
4. Relis `get_local_model_job` jusqu'à un état terminal : `succeeded`, `failed`, `cancelled` ou
   `unknown_interrupted`. Ne boucle pas sans borne et n'annonce pas une réussite pendant
   `queued`, `running` ou `cancelling`.
5. Après `succeeded`, inspecte ou liste les modèles installés. Si nécessaire, appelle
   `verify_local_model` avant la sélection.

Rapporte exactement les codes d'échec : accès Hugging Face requis, espace disque insuffisant,
daemon Ollama indisponible, téléchargement interrompu ou vérification échouée. Ne transforme
jamais un accusé HTTP en preuve d'installation.

## Activation, sélection, désactivation et déchargement

Chaque mutation exige que le message utilisateur courant nomme clairement l'action et le
modèle. N'utilise pas un ancien accord vague pour modifier un autre modèle.

- `enable_local_model` rend un modèle installé disponible au routage.
- `select_local_model` vérifie si nécessaire puis affecte le rôle demandé. Utilise seulement
  `primary`, `code`, `vision` ou `web`.
- Avant `disable_local_model`, inspecte le modèle si son rôle ou son état courant est incertain.
  Un modèle principal actif peut être refusé : rapporte le refus et demande quel remplaçant
  utiliser au lieu de contourner la garde.
- `unload_local_model` sert uniquement à libérer la mémoire. Ne prétends pas que le modèle est
  désactivé ou supprimé après un déchargement.

## Suppression en deux messages humains

La suppression est irréversible et ne peut pas être achevée dans le même message utilisateur.

1. Sur une demande explicite de suppression, appelle `prepare_delete_local_model`.
2. Présente l'impact réel retourné : taille, chargement, affectations et blocages. Indique qu'une
   confirmation distincte est requise. Ne supprime rien à cette étape.
3. Attends un **nouveau message humain** confirmant explicitement la suppression du même modèle,
   par exemple « oui, supprime `<référence>` ».
4. Appelle alors `confirm_delete_local_model` avec la même référence, la même source et le ticket
   à usage unique. Ne fabrique jamais la confirmation et ne réutilise jamais un ticket.
5. Annonce la suppression seulement si le résultat prouve l'absence. Sinon, rapporte le code de
   refus ou d'échec exact.

Un modèle sélectionné, affecté, actif ou visé par un job en cours peut être protégé. Ne tente pas
de contourner ces gardes. Les workers de mission ne sont pas autorisés à effectuer ces mutations
globales : rapporte cette limite et laisse l'action au chat principal.

## Réponse de Lumena

Les outils renvoient des faits structurés. Compose toi-même une réponse claire à partir de ces
faits ; ne récite pas de message préenregistré. Mentionne ce qui est prouvé, ce qui reste inconnu,
l'état final observé et la prochaine action utile. Ne dis jamais « installé », « activé »,
« sélectionné », « déchargé » ou « supprimé » sans l'observation correspondante.
