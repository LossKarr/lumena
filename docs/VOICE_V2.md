# Voix locale Lumena — socle V2 et finalisation V3

Voice V2 est la chaîne vocale locale de Lumena. Elle relie le micro, la
reconnaissance vocale, le gestionnaire de tours, le Chat ou l'Agent, la narration
d'activité publique et la synthèse vocale. Le texte reste la sortie canonique :
une panne audio ne transforme jamais une action inachevée en réussite.

## Chaîne effective

```text
micro PyAudio
  -> VAD énergétique calibré
  -> WebRTC AEC/NS/AGC local en mains libres
  -> faster-whisper local
  -> garde d'activation locale
  -> TurnManager à file unique
  -> Chat ou Agent officiel
  -> projection publique validée
  -> SpeechCoordinator
  -> worker TTS local isolé
  -> lecture locale annulable
```

Le worker TTS est un processus borné. Un timeout, une annulation ou un crash le
ferme sans bloquer la boucle principale. Les segments portent leur format, leur
fréquence et leur nombre de canaux réels. La lecture ne suppose plus un WAV mono
à 22,05 kHz.

## Activation

Le mode produit par défaut est `wake_phrase`. La transcription locale doit
contenir « Lumena » avant d'atteindre le raisonnement. Une fenêtre de conversation
bornée accepte ensuite les phrases suivantes. Les autres modes sont :

- `push_to_talk` : le bouton ou l'API authentifiée autorise exactement le prochain
  énoncé ;
- `open_mic` : écoute ambiante volontaire, à activer explicitement.

Cette activation est un filtre de phrase fondé sur le STT local. Elle ne constitue
ni une reconnaissance du locuteur, ni une authentification, ni un modèle neuronal
de wake word. Les actions sensibles conservent les rôles, politiques et
confirmations ordinaires de Lumena.

## Parole pendant le travail

L'Agent peut produire un champ public `PUBLIC_UPDATE`. Un validateur retire toute
pensée privée, secret, chemin absolu ou succès non prouvé. La narration applique
une cadence, un budget et une déduplication. Elle n'ajoute aucun appel LLM.

L'utilisateur peut interrompre, corriger et orienter plusieurs fois. Le travail
initial continue sauf ordre d'arrêt. Après un faux barge-in, Lumena reprend depuis
le texte non joué ; elle ne recommence pas la phrase déjà entendue.

## Configuration principale

| Variable | Rôle | Défaut |
|---|---|---|
| `LUMENA_VOICE_V2_AUTO` | démarrage automatique de Voice V2 | `0` |
| `LUMENA_VOICE_V2_MODE` | `chat` ou `agent` | `chat` |
| `LUMENA_VOICE_ACTIVATION_MODE` | activation micro | `wake_phrase` |
| `LUMENA_VOICE_WAKE_PHRASE` | phrase locale | `Lumena` |
| `LUMENA_VOICE_CONVERSATION_WINDOW_S` | fenêtre après activation | `20` |
| `LUMENA_STT_MODEL` | modèle faster-whisper | `large-v3-turbo` |
| `LUMENA_STT_DEVICE` | `cuda` ou `cpu` | `cuda` |
| `LUMENA_STT_COMPUTE` | précision du moteur | `float16` |
| `LUMENA_STT_PARTIAL_EVERY_MS` | intervalle des partiels | `0` |
| `LUMENA_STT_TIMEOUT_S` | borne d'une transcription | `45` |
| `LUMENA_TTS_WORKER_TIMEOUT_S` | borne du worker TTS | `90` |
| `LUMENA_VOICE_TTS_ENGINE` | `auto`, `qwen3` ou `piper` | `auto` |
| `LUMENA_QWEN_TTS_TIMEOUT_S` | borne d'une synthèse Qwen isolée | `90` |
| `LUMENA_QWEN_TTS_DEVICE` | `auto`, `cuda:0` ou `cpu` | `auto` |
| `LUMENA_QWEN_TTS_DTYPE` | `auto`, `bfloat16` ou `float32` | `auto` |
| `LUMENA_VOICE_CLOUD_ALLOWED` | autorise Edge-TTS | `0` |
| `LUMENA_XTTS_ALLOW_RESTRICTED` | autorise les poids XTTS CPML | `0` |

Sur CPU, une précision float16 incompatible est ramenée à int8. Une erreur CUDA
à l'inférence déclenche un seul repli CPU int8. Les partiels règlent le timing ;
seul le final peut lancer une demande.

### Profils de performance

Le panneau Conversation propose cinq profils ordonnés. Ils modifient uniquement
le STT, le TTS et le VAD. La langue, le périphérique micro, le mode Chat/Agent,
l'activation et l'identité restent inchangés.

| Profil | STT | TTS local | Réseau TTS |
|---|---|---|---|
| Essentiel | tiny CPU int8 | Piper | non |
| Léger | base CPU int8 | Piper | non |
| Équilibré | small CPU int8 | Piper | non |
| Précision | medium CPU int8 | Piper | non |
| Studio GPU | large-v3-turbo CUDA float16 | auto, Qwen seulement si qualifié | non |

Chaque profil désactive les transcriptions partielles bloquantes. Le serveur sonde
les capacités réelles avant d'autoriser Studio GPU : voir une carte NVIDIA ne
suffit pas, cuBLAS et cuDNN doivent être disponibles. Une application écrit
atomiquement les seules variables du profil, conserve le reste du `.env`, crée
une sauvegarde. Si l'écoute est active, le panneau redémarre uniquement le pipeline
vocal pour appliquer le profil ; sinon il sera appliqué au prochain démarrage.

## Confidentialité et traces

La chaîne est locale par défaut. Elle n'enregistre ni audio brut ni transcription
dans les journaux. L'option cloud TTS est visible dans le statut et doit être
activée explicitement. Le statut expose le moteur effectif, la localité, les
timeouts, les redémarrages, la profondeur de file, le premier audio et la latence
d'interruption sans publier le contenu parlé.

## Voice Packs

Le gestionnaire `VoicePackManager` importe des packs hors ligne. Avant activation,
il exige un manifeste signé Ed25519, les empreintes SHA-256, les tailles, les
licences et la preuve de consentement vocal. Il rejette les chemins traversants,
les liens, les types exécutables, les archives chiffrées et les ratios de compression
hostiles ; taille décompressée et nombre de fichiers sont bornés. Il vérifie l'espace
disque, installe dans un dossier temporaire puis bascule atomiquement. Le pack
précédent reste disponible pour rollback et un pack actif ou utilisé ne peut pas
être supprimé.

Le panneau **Voice > Modèles** expose désormais ce cycle complet : sélection d'une
archive locale, vérification, installation, activation, retour à la génération
précédente et suppression d'un pack inactif. Toutes les routes exigent le jeton
administrateur. L'upload est diffusé par blocs dans `data/voice/.uploads`, borné par
`LUMENA_VOICE_PACK_MAX_MB`, puis effacé dans tous les cas. Une activation redémarre
Voice V2 ; si le redémarrage échoue ou bascule vers le backend historique, Lumena
restaure la sélection précédente avant de rendre l'erreur.

Un pack Piper doit déclarer `engine: piper` et une liste `models`. Chaque entrée
contient `language`, `model_name`, `model_path` et `config_path`; les deux chemins
doivent désigner des fichiers déclarés et hashés dans le manifeste. L'activation
recalcule les empreintes installées, rejette une configuration JSON illisible, puis
projette les langues du pack dans le profil vocal sans modifier le profil source.
Le worker TTS résout ensuite les poids depuis le pack actif ; le français intégré
reste le repli si le pack est absent ou corrompu.

Un pack Qwen déclare `engine: qwen3`, le modèle Base 0.6B et sa révision immuable,
une référence WAV, sa transcription exacte, une configuration et une provenance.
Les droits de distribution doivent être confirmés dans cette provenance et dans le
consentement signé. Le pack projette cette preuve dans le profil runtime ; il ne
permet ni référence vocale libre ni révision `latest`.

Qwen tourne dans un Python séparé. Le provider communique avec un worker JSONL
local, borne les textes, les délais et la durée audio, puis publie un WAV atomique.
Le mode `auto` exige simultanément les composants prêts, un pack actif et un canari
de performance qualifié. Un échec de synthèse bascule par segment sur Piper ; une
annulation ne déclenche jamais de parole de repli tardive.

Le panneau **Voice > Modèles** propose une installation Qwen en un clic lorsque la
release contient son manifeste et ses clés publiques. L'opération tourne hors de la
requête web, affiche une progression bornée, reprend les archives partielles et ne
bascule la voix qu'après vérification des signatures, activation atomique du pack et
canari audio réel. Une fermeture pendant le téléchargement laisse l'installation
reprenable. Une édition de développement sans manifeste signé affiche honnêtement
« non publié » et ne télécharge rien depuis une source libre ou mutable.

Les clés publiques approuvées sont lues depuis
`LUMENA_VOICE_PACK_TRUSTED_KEYS_JSON` sous la forme
`{"release":"<clé Ed25519 brute en base64>"}`. Une valeur vide désactive
honnêtement l'import. Aucune clé privée de signature ne doit être stockée dans le
dépôt, le `.env`, le panneau ou les données runtime.

Le code ne fabrique jamais le pack « Lumena Voice » ni son consentement. Une release
publique Qwen ne peut être construite que lorsque le responsable fournit la référence
finale, la sélection humaine, la provenance et les droits signés. En leur absence,
Piper reste le moteur local générique et le build public échoue au lieu d'inventer
une identité. XTTS reste désactivé par défaut car ses poids sont soumis à la Coqui
Public Model License et ne permettent pas un usage commercial général.

La chaîne de publication est décrite dans `docs/VOICE_PACK_RELEASE.md`. Le script
`tools/release/build_voice_pack.py` crée une clé Ed25519 chiffrée hors dépôt, construit
une archive déterministe par blocs et la vérifie avec le même `VoicePackManager`
que le produit. Il exige que la preuve de consentement soit incluse dans le payload
signé et ne génère jamais de consentement ou de licence à la place du responsable.

## Frontal acoustique local

Le backend micro choisit un VAD Silero local lorsqu'un runtime approuvé est déjà
présent, puis revient au seuil énergétique sans téléchargement silencieux. En
profil mains libres, `pywebrtc-audio` traite localement la capture et la référence
de lecture avec AEC, réduction de bruit, filtre passe-haut et contrôle de gain. La
référence PCM reste en mémoire, est horodatée, bornée et effacée après traitement ;
elle n'est jamais journalisée. Le casque et le push-to-talk n'activent pas ce DSP.

Si le composant ou son initialisation manque, le statut annonce explicitement
`server_aec_unavailable` ou `processor_init_failed` et le runtime applique sa garde
acoustique dégradée. La présence du WebRTC du navigateur ne suffit pas : la capture
PyAudio utilise son propre frontal serveur local.

Le statut runtime expose ces absences. Le backend finalisé reste derrière son flag
de promotion et le backend historique demeure le rollback jusqu'à validation de
la campagne matérielle H1-H15.

## Langues et dialogue

La langue d'entrée est détectée localement avec un niveau de confiance. Une demande
de traduction ou un mot étranger ne modifie pas la session ; une commande explicite
peut basculer durablement en français, anglais ou espagnol. La réponse passe par une
garde de langue, avec une seule réparation bornée avant blocage.

La parole est préparée pour l'écoute : liens réduits à leur libellé, fragments de
code et raisonnement privé retirés, phrases segmentées et synthèse de la phrase
suivante pendant la lecture courante. Les pauses artificielles ne sont pas ajoutées
entre deux segments déjà prêts.

Les réponses provenant du chat vocal reçoivent aussi un contrat oral court et
auto-suffisant. La projection défensive conserve jusqu'à six phrases courtes afin
qu'une conclusion ne disparaisse pas lorsqu'un modèle renvoie malgré tout une
réponse longue. Les phrases courtes voisines sont regroupées dans des blocs bornés
pour que leur lecture masque la synthèse du bloc suivant et évite un long silence
après chaque point. Les fichiers Edge TTS sont produits dans un fichier temporaire,
validés puis publiés atomiquement ; une synthèse vide ou interrompue est rejetée
et la cascade passe au moteur local au lieu de mémoriser un segment muet.

L'état émotionnel reste un contexte interne : il module le ton, le rythme et la
formulation. Les annonces automatiques de changement d'humeur ne sont jamais ajoutées
à une réponse du canal vocal, sauf si l'utilisateur demande explicitement à Lumena
de parler de son humeur.

En profil mains libres sans AEC actif, le runtime passe automatiquement en mode
`guarded_no_aec`. La capture reste ouverte afin que l'utilisateur puisse interrompre
Lumena ; pendant la lecture, le VAD exige toutefois un seuil d'énergie plus élevé
pour rejeter l'écho modéré du haut-parleur. Un casque ou un AEC actif conserve le
mode `full_duplex`. La dictée explicite du compositeur garde l'exclusivité micro.
Le bouton **Couper** arrête immédiatement la lecture sans annuler le travail et
chaque interruption demandée est comptée dans le diagnostic.

Pendant un barge-in, l'ancienne génération reste suspendue tant que la transcription
finale n'a pas produit un résultat terminal. Une phrase valide invalide définitivement
l'ancien audio ; un fragment vide, un rejet d'activation, un timeout ou une erreur
peut reprendre une seule fois depuis la partie estimée non entendue. Les finals
tardifs d'un ancien tour sont ignorés.

Le lecteur audio publie aussi son état physique au chemin VAD. Une parole détectée
pendant qu'un fichier est réellement audible déclenche donc l'interruption même si
un événement de fin ancien a momentanément désynchronisé le mode logique. Les fins
de lecture portent leur `generation_id` : une fin périmée ne peut plus rendre la main
à l'écoute ni effacer la génération courante. Lorsqu'une nouvelle génération prend
le contrôle, elle arrête explicitement l'ancienne sortie avant de jouer son audio.

## Panneau Voix

Le panneau sépare Conversation, Voix, Écoute, Langues, Modèles et Diagnostic. Il
permet de choisir un profil de performance adapté au matériel, le mode d'activation,
tester le micro et la sortie, utiliser le push-to-talk, couper immédiatement la
parole, régler la langue et exporter un diagnostic expurgé. Les menus visuels sont
synchronisés avec la valeur native réellement envoyée au serveur. Enregistrer
redémarre automatiquement le pipeline lorsqu'il est actif ; chaque commande affiche
son résultat dans le panneau. Le test micro réutilise l'état du runtime lorsqu'il
possède déjà le périphérique afin d'éviter une seconde ouverture PyAudio concurrente.
L'application d'un profil rafraîchit aussi les champs Voix du panneau Configuration
depuis `/api/config`. Les brouillons non sauvegardés de ce panneau sont conservés et
restent signalés comme modifications locales.
L'état affiché vient du runtime effectif : matériel, device STT, provider, moteur
demandé, moteur réellement choisi, Qwen installé/prêt/qualifié, fallback, VAD, AEC,
wake word, langues et percentiles de latence.

## Confidentialité et effacement

Les buffers de capture sont vidés après usage et aucun audio brut n'est conservé
par défaut. Les logs ne contiennent ni transcription ni réponse parlée. Le partage
pour un éventuel corpus d'amélioration utilise un consentement séparé, désactivé par
défaut.

Le bouton **Effacer les données vocales** arrête la voix puis supprime uniquement
`data/voice`. L'opération exige le token administrateur et la confirmation `DELETE`,
ne suit pas les liens symboliques et ne touche ni la mémoire générale de Lumena ni
un profil externe configuré hors de ce dossier.

## Diagnostic et récupération

1. Ouvrir **Voix → Diagnostic** et vérifier le profil matériel, le STT effectif,
   le moteur VAD, l'AEC et la dernière erreur expurgée.
2. Si CUDA manque de VRAM ou de DLL, laisser le runtime sélectionner CPU int8 ;
   il n'effectue qu'un seul repli, sans boucle de redémarrage.
3. Si le micro mains libres produit de faux tours, choisir `push_to_talk` ou un
   casque jusqu'à certification d'un AEC serveur sur cette machine.
4. Si un pack de voix échoue, conserver la version active et utiliser le rollback ;
   ne jamais contourner la signature, l'empreinte ou la licence.
5. Le préchauffage d'Edge TTS effectue une sonde réseau réelle lorsque le cloud a été
   autorisé : un MP3 encore présent dans le cache ne masque donc plus un refus 403.
   Le disjoncteur ouvre alors le repli local Piper et expose cet état dans le diagnostic.
6. Si le runtime reste dégradé, désactiver Voice V2/V3. Le chat texte et le travail
   Agent restent autoritatifs et le backend historique demeure disponible.
