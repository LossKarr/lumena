---
name: personal-model-manager
description: "Inspecte et pilote le modèle personnel évolutif de Lumena avec les outils natifs : collecte autorisée, curation, jugement isolé, datasets, entraînements, évaluations, lignée, activation, rollback et diagnostic. À utiliser quand l'utilisateur parle de son propre modèle Lumena, de fine-tuning personnel, d'apprentissage continu, de version personnelle ou de migration de base."
keywords: [modele personnel, modèle personnel, fine tuning, finetuning, apprentissage continu, entrainement, entraînement, dataset personnel, lora, dpo, juge, lignée, version personnelle, rollback, migration de base]
applyTo: [personal_model_status, personal_learning_health, personal_experience_stats, personal_training_settings, personal_training_jobs, personal_model_versions, personal_model_recommendations, personal_model_audit_trail, update_personal_training_settings, prepare_personal_dataset, queue_personal_training, control_personal_training, request_personal_model_approval, confirm_personal_model_action]
license: Lumena - usage interne
---

# Modèle personnel — doctrine d'utilisation

Utilise uniquement les outils `personal_*` et le plan de contrôle canonique. Ne modifie jamais
directement `.env`, les JSON, les datasets, les adapters, les registres ou les poids. Les outils
relisent l'état persistant, appliquent les permissions, écrivent les preuves et isolent chaque
propriétaire.

## Comprendre le système

- La **mémoire** fournit du contexte récupérable ; elle ne modifie pas les poids.
- Une **expérience** est une interaction redacted, attribuée et accompagnée de preuves.
- Un **dataset** est un ensemble scellé et reproductible d'expériences autorisées.
- Un **adapter** est le résultat LoRA conservé pour reprise et portabilité.
- Une **version personnelle** suit le nom `préfixe-model-x.y.z` et appartient à une lignée.
- Le **modèle principal** reste disponible. Une version personnelle active ne le remplace pas
  globalement sauf décision humaine distincte.

Le cycle fiable est : collecte → curation → jugement isolé → dataset → entraînement → évaluation
→ export/canari → activation facultative. Ne saute aucune preuve entre ces étapes.

## Toujours commencer par les faits

Utilise `personal_model_status` pour le principal, le personnel actif et le défaut effectif.
Utilise `personal_learning_health` pour expliquer si Lumena apprend réellement, attend ou est
bloquée. Utilise ensuite l'outil précis : statistiques, réglages, jobs, versions, recommandations
ou audit.

Ne déduis jamais un état réel d'un simple réglage : `enabled=true` ne prouve ni capture, ni
dataset, ni entraînement. Une recommandation expirée doit être recalculée. Si un fait manque,
dis clairement que tu ne sais pas encore et propose l'inspection qui le prouvera.

## Mutations non destructives

Une demande utilisateur explicite permet de régler les horaires et budgets, préparer un dataset,
créer un job, le lancer, le mettre en pause ou le reprendre. Après chaque mutation, relis l'état
et cite l'identifiant de preuve. Un job `queued`, `waiting_idle`, `running`, `pausing` ou
`resuming` n'est pas terminé.

L'entraînement passe dans un processus séparé et respecte le gouverneur de ressources. Ne tente
jamais d'installer une dépendance pendant un run, de lancer un script historique ou d'exécuter
une commande générée par un modèle.

## Approbations sensibles

Annuler un job, activer ou restaurer une version, changer le défaut global, utiliser un juge
cloud, exporter, importer ou supprimer exige une approbation contextualisée à usage unique.

1. Appelle `request_personal_model_approval` avec l'action et la ressource exactes.
2. Explique l'effet réel sans l'exécuter.
3. Attends un nouveau message humain de confirmation.
4. Appelle `confirm_personal_model_action` avec le ticket et les mêmes paramètres.
5. Relis le statut et annonce seulement ce que la preuve finale confirme.

Ne fabrique jamais une confirmation et ne réutilise jamais un ticket.

## Juge et confidentialité

Le juge possède son propre contexte, sans historique actif, mémoire privée, humeur, skills,
workspace ni état du chat. Un modèle personnel peut contribuer à l'auto-évaluation mais ne peut
pas s'auto-valider seul. Un désaccord va en quarantaine. Le cloud nécessite un consentement
séparé ; aucune donnée privée ne sort de la machine sur la seule activation de l'apprentissage.

## Évaluation, export et rollback

Une version non évaluée ou inférieure sur une capacité critique ne peut pas devenir active.
Conserve l'adapter même après fusion. L'export GGUF, l'import Ollama et le canari sont trois
preuves distinctes. Une sauvegarde doit valider ses hashes avant restauration et ne contient ni
secret, ni ticket d'approbation.

En cas d'échec, rapporte le code exact, le dernier checkpoint prouvé et la prochaine action sûre.
N'annonce jamais « entraîné », « exporté », « importé », « évalué », « activé » ou « restauré »
sur la seule présence d'un job ou d'un fichier intermédiaire.
