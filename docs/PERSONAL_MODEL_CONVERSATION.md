# Piloter le modèle personnel depuis Lumena

Lumena dispose d'outils structurés pour relire l'état réel et administrer le modèle personnel depuis les canaux autorisés. Sa réponse finale est générée par le modèle courant à partir des résultats d'outils ; les handlers ne contiennent pas de réponse conversationnelle préfabriquée.

## Outils de lecture

- `personal_model_status` : modèle principal, version active, défaut effectif, jobs et ressources ;
- `personal_learning_health` : consentements, dépendances et blocages ;
- `personal_experience_stats` : compteurs sans contenu privé ;
- `personal_training_settings` : valeurs demandées et effectives ;
- `personal_training_jobs` : état, checkpoint, progression et erreurs ;
- `personal_model_versions` : lignée, hashes, évaluation et activation ;
- `personal_model_recommendations` : conseils calculés depuis les faits actuels ;
- `personal_model_audit_trail` : preuves bornées.

## Actions directes

Une demande utilisateur explicite est nécessaire pour modifier la politique, préparer un dataset, créer un run, lancer, suspendre, reprendre ou évaluer une version. Lumena doit relire l'état après chaque action et distinguer « demandé », « en cours » et « prouvé ».

## Actions avec confirmation

Annuler un entraînement, exporter une version, créer une sauvegarde, effacer les données d'apprentissage, activer, rollback ou changer le modèle par défaut suit deux appels :

1. `request_personal_model_approval` produit un ticket court, lié à l'action et à la ressource ;
2. `confirm_personal_model_action` consomme ce ticket une seule fois après confirmation humaine.

Un ticket expiré, déjà utilisé ou lié à une autre ressource est refusé. L'autorisation du juge cloud suit la même règle.

Lors de la création d'une lignée, Lumena peut transmettre le préfixe demandé par l'utilisateur. Elle ne doit jamais inventer le suffixe : le service de lignée produit toujours `<préfixe>-model-<semver>` et attribue la version suivante.

## Règles de vérité

Lumena ne doit jamais annoncer qu'un modèle est entraîné sur la seule présence d'un job, qu'il est utilisable sur la seule présence d'un GGUF, ni qu'il est meilleur sans rapport d'évaluation. Elle doit citer le statut, l'identifiant de preuve et le blocage retournés par les outils.
