# Gestion des modèles locaux

Lumena gère les modèles servis par Ollama depuis le panneau **Système → Modèles locaux** et depuis ses outils conversationnels.

## États distincts

- **installé** : les poids sont présents dans l'inventaire `/api/tags` d'Ollama ;
- **vérifié** : `/api/show` et le canari adapté ont fourni une preuve ;
- **activé** : le modèle apparaît dans les nouveaux choix Lumena ;
- **sélectionné** : il est affecté à un rôle comme cerveau principal ;
- **chargé** : il est actuellement résident selon `/api/ps` ;
- **désactivé** : les poids restent sur disque mais le modèle sort des choix ;
- **absent** : `/api/tags` confirme qu'il n'est plus présent.

## Ollama et Hugging Face

Lumena recherche l'index public officiel Ollama et complète les résultats avec une sélection curated datée. Ollama ne documente toutefois pas d'API publique stable permettant d'énumérer exhaustivement sa bibliothèque : cet adaptateur HTML reste borné, désactivable et annoncé comme partiel. Un identifiant direct reste installable après validation stricte, même si l'index est indisponible.

La recherche Hugging Face interroge l'API officielle avec le filtre GGUF. Les références installables prennent la forme `hf.co/{auteur}/{depot}:{quantification}`. Une taille, une licence ou une capacité inconnue reste affichée comme inconnue.

Pour les dépôts gated ou privés, configurez `HF_TOKEN` localement. Le token est envoyé uniquement au Hub et n'est jamais inclus dans les résultats, audits ou erreurs.

## Sécurité

- Aucun `ollama pull` ou `ollama rm` shell n'est exécuté : Lumena utilise l'API HTTP locale.
- L'hôte Ollama doit être loopback sauf activation explicite de `LUMENA_OLLAMA_ALLOW_REMOTE=1`.
- Une mutation conversationnelle doit correspondre à la demande utilisateur originale.
- Un worker de mission ne peut pas modifier le catalogue global.
- Une suppression nécessite une préparation d'impact, un ticket aléatoire à usage unique, puis une confirmation humaine distincte.
- Un modèle actif ou affecté à un rôle ne peut pas être supprimé.
- Un modèle communautaire ne devient jamais fallback automatiquement.

## Preuves et dépannage

Les jobs persistent sous `data/local_models/jobs.json`, les choix d'activation sous `data/local_models/state.json` et l'audit filtré sous `data/local_models/audit.jsonl`. Une fin de téléchargement ne suffit pas : la réussite exige la présence dans `/api/tags`, un digest, `/api/show` et un canari compatible.

Si Ollama est indisponible, vérifiez qu'il écoute sur `LUMENA_OLLAMA_HOST`. Une recherche Hugging Face indisponible ne bloque ni l'inventaire local ni la sélection curated Ollama.

## Configuration

- `LUMENA_OLLAMA_HOST` : URL du daemon, locale par défaut ;
- `LUMENA_OLLAMA_ALLOW_REMOTE=1` : autorise explicitement un daemon distant configuré par l'administrateur ;
- `LUMENA_LOCAL_MODEL_CATALOG_TTL` : durée du cache de recherche, 900 secondes par défaut ;
- `LUMENA_LOCAL_MODELS_OLLAMA_LIBRARY` : active la recherche best-effort dans l'index public officiel Ollama ;
- `LUMENA_LOCAL_MODEL_MAX_CONCURRENT` : téléchargements concurrents, 2 par défaut et 8 au maximum ;
- `LUMENA_LOCAL_MODEL_MAX_QUEUED` : opérations actives ou en attente, 20 par défaut ;
- `LUMENA_LOCAL_MODEL_MIN_FREE_GB` : espace disque à préserver, 2 Gio par défaut ;
- `LUMENA_LOCAL_MODEL_STATE_FILE` : emplacement alternatif du store d'activation ;
- `LUMENA_LOCAL_MODELS_AUTO_FALLBACK=0` : garde contractuelle ; aucun modèle communautaire n'est ajouté automatiquement aux fallbacks.

L'installation et la recherche distante requièrent le réseau. Lumena n'engage aucun téléchargement depuis la suite de tests et ne télécharge pas de modèle pendant le démarrage. La taille Hugging Face peut rester inconnue selon les métadonnées publiées. Vérifiez toujours la licence et les conditions d'un dépôt communautaire avant usage.

Depuis le chat, une demande de recherche ou d'analyse est en lecture seule. Une installation, activation, désactivation, sélection ou décharge exige que la demande originale nomme la cible, ou qu'elle désigne sans ambiguïté un résultat de la recherche courante. Une suppression se déroule en deux demandes séparées : préparation de l'impact, puis confirmation avec le ticket émis.

Sources de contrat :

- <https://docs.ollama.com/api/>
- <https://huggingface.co/docs/huggingface_hub/guides/search>
- <https://huggingface.co/docs/hub/gguf>
- <https://huggingface.co/docs/hub/ollama>
