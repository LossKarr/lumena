# Modèle personnel Lumena

Le modèle personnel est une lignée locale facultative. Le modèle principal configuré dans Lumena reste disponible. Une première version porte le nom `lumena-model-1.0.0`; un préfixe choisi par l'utilisateur conserve obligatoirement la forme `<préfixe>-model-x.y.z`.

## Parcours utilisateur

1. Ouvrir **Modèle personnel**.
2. Activer séparément **Apprentissage personnel** et **Collecte locale**.
3. Choisir quand l'entraînement est autorisé : manuel, PC inactif, horaires ou automatique borné.
4. Configurer le juge. L'autorisation d'un juge cloud demande une confirmation séparée.
5. Attendre des expériences acceptées ou importer l'historique avec une prévisualisation.
6. Créer un run. Lumena scelle le dataset, réserve une version et lance un worker séparé.
7. Après entraînement, exporter vers Ollama. Lumena conserve l'adapter, fusionne dans un dossier distinct, produit le GGUF, importe le modèle et exige une génération canari.
8. Évaluer la version contre le modèle de référence avec un juge indépendant.
9. Activer la version personnelle. La définir comme cerveau par défaut est une deuxième décision explicite.

Une version sans évaluation ou sans canari Ollama ne peut pas devenir active.

## Données et confidentialité

La collecte est désactivée par défaut. Les messages sont redacted avant leur entrée dans la file non bloquante. Les sondes internes, surfaces exclues, conversations exclues et licences non autorisées restent hors dataset. Le journal d'audit conserve des identifiants, états, compteurs et empreintes, jamais les prompts complets ni les secrets.

Le juge utilise un client neuf et un contexte dédié. Il ne reçoit ni historique actif, ni mémoire, ni humeur, ni workspace, ni outils. Un modèle personnel ne peut pas être son unique preuve de validation.

## Priorité au travail interactif

Le scheduler vérifie CPU, RAM, VRAM, disque, batterie, inactivité et activité Voice/Agent/Mission/CodeAgent/vidéo. Il peut mettre un run en attente ou demander une pause au prochain checkpoint. La reprise recharge le checkpoint enregistré. Le processus d'entraînement a une priorité système basse.

## États et preuves

- expérience : `raw`, `candidate`, `judging`, `accepted`, `rejected`, `quarantined`, `included_in_dataset`, `trained`, `excluded` ;
- run : `queued`, `waiting_idle`, `running`, `pausing`, `paused`, `resuming`, `cancelling`, `cancelled`, `unknown_interrupted`, `failed`, `completed` ;
- version : `candidate`, `evaluated`, `rejected`, `available`, `active`, `archived`, `invalidated`.

Les datasets ont un manifest et des splits stables. Les adapters, rapports d'évaluation et GGUF sont identifiés par SHA-256. Les sauvegardes vérifient chaque fichier et excluent les tickets d'approbation, l'audit et la quarantaine.

## Limites matérielles

Les composants de contrôle fonctionnent sans GPU. Un entraînement réel demande les dépendances de `requirements-finetuning.txt` et un modèle compatible. Le panel affiche les dépendances et les ressources détectées. Une réussite logicielle ne prouve pas qu'une machine donnée peut entraîner une base donnée ; le run réel et son canari fournissent cette preuve.

Le mode from-scratch est un parcours expert séparé. Son préflight refuse un corpus non licencié, un tokenizer non versionné ou des ressources insuffisantes. Il ne s'active jamais automatiquement.

## Récupération

Après un redémarrage, Lumena relit l'identité du processus. Un worker encore vivant reste `running`; un processus absent ou étranger devient `unknown_interrupted` avec une preuve d'audit. Reprendre exige un checkpoint. Le rollback réactive uniquement une version déjà évaluée et testée dans Ollama.

La page **Données** permet de créer et lister les sauvegardes, de restaurer une archive vérifiée, de détecter les anciens JSON/JSONL et de prévisualiser leur migration. Les fichiers volumineux sont hashés et restaurés par flux afin de ne pas charger un modèle entier en mémoire. La validation est terminée dans une zone temporaire avant toute écriture. Les conflits, doublons de chemin, tailles incohérentes et chemins sortant de l'espace personnel sont refusés.

**Effacer les données d'apprentissage** supprime seulement les expériences, index, datasets, quarantaine et cache du juge. Les versions entraînées, runs, réglages, sauvegardes et preuves d'audit sont conservés. L'action est refusée tant qu'un job n'est pas dans un état terminal et exige un ticket d'approbation à usage unique.

L'annulation est coopérative lorsqu'un worker est vivant afin qu'il puisse écrire son checkpoint. Un job en attente, déjà en pause ou dont le worker a disparu passe directement à l'état terminal `cancelled`; il ne reste pas bloqué dans `cancelling`.

## Diagnostic d'un lancement bloqué

Le bouton peut créer un job puis le laisser en `waiting_idle`. Ce statut est normal lorsque le budget CPU/RAM/VRAM/disque, la batterie, Voice ou une mission l'impose. Si `training_dependencies_missing` apparaît, installer le runtime optionnel de `requirements-finetuning.txt` avec une version CUDA de PyTorch compatible. Lumena n'exécute pas un worker voué à échouer et ne déclare jamais le job lancé dans cet état.
