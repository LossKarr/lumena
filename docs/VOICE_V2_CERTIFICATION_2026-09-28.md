# Rapport de certification Voice V2 — 2026-09-28

## Verdict

**CERTIFIÉ AVEC LIMITES pour la chaîne logicielle locale.**

La régression automatisée, l'isolation TTS réelle et les contrats de sécurité sont
validés. La promotion par défaut, le label de voix propriétaire et la
certification acoustique publique restent bloqués jusqu'à la campagne humaine et
matérielle H1 à H10. Voice V2 demeure donc activable explicitement et le backend
historique reste le rollback.

Référence Git observée au départ : branche `main`, HEAD `e9a53cf`. Aucun commit,
push, tag ou release n'a été créé pendant cette mission.

## Résultat livré

- configuration STT réellement propagée du panneau au runtime ;
- activation locale `wake_phrase`, push-to-talk et fenêtre bornée ;
- rejet des paroles ambiantes avant tout appel au raisonnement ;
- autorité audio unique avec priorités, expiration et préemption ;
- parole publique produite par le modèle, validée et sans pensée privée ;
- interruption, correction, orientations multiples et reprise du texte non joué ;
- STT local hors event loop, timeout et fallback CUDA vers CPU ;
- TTS local isolé dans un processus borné, annulable et redémarrable ;
- format, fréquence et canaux audio issus du fichier réel ;
- prosodie du VoiceProfile appliquée à Piper ;
- Voice Pack offline signé, vérifié et installé atomiquement ;
- statut runtime sans transcription, audio brut, clé ou stack trace ;
- documentation opérateur et présentation README mises à jour.

## Preuves automatisées

### Baseline avant modification Voice V2

```text
294 passed in 11.00s
23319 passed, 14 skipped, 2 warnings in 1307.13s
```

### Campagnes après modification

```text
Activation/config/VAD/STT ciblés : 77 passed
STT/fallback/dictée/lifecycle     : 78 passed
Worker TTS/audio/runtime          : 73 passed
Voice Pack + worker               : 9 passed
Matrice voix élargie              : 351 passed
Config/API/sécurité/runtime/web    : 234 passed
Lifecycle/release                 : 40 passed
Suite complète finale             : 23367 passed, 14 skipped, 2 warnings
Durée suite complète finale       : 1026.67 s (17 min 06 s)
```

Une première passe complète a révélé onze régressions contractuelles dans
`react.py` : quatre gardes de forme AST et sept constructeurs historiques sans
`step_callback`. Le point d'intégration a été rendu tolérant, l'enveloppe
défensive attendue a été conservée, les onze tests ont repassé, puis la suite
complète a été rejouée avec zéro échec. `react.py` finit une ligne plus court que
la version de départ.

Les deux avertissements finaux sont des dépréciations Starlette/httpx et
`websockets.server.WebSocketServerProtocol`. Ils existaient hors du contrat Voice
V2 et ne signalent aucun échec.

## Vérification runtime réelle

Un smoke local a démarré le worker Windows `spawn`, chargé `LumenaTTS`, sélectionné
Piper `fr_FR-siwis-medium`, synthétisé une phrase française et rendu un WAV PCM16
mono à 22 050 Hz. Le worker a ensuite été fermé proprement. Cette preuve valide le
chemin process et le moteur installé sur la machine de référence.

Il s'agit d'une seule mesure froide, insuffisante pour publier des percentiles.
Les métriques p50/p95/p99 acoustiques sont donc marquées **non mesurées** au lieu
d'être extrapolées.

## Sécurité et confidentialité

- aucune parole hors activation ne produit `start_llm` ;
- le wake phrase ne confère aucun rôle ni permission ;
- les confirmations sensibles existantes restent autoritatives ;
- les mises à jour parlées rejettent pensée privée, secret, prompt, chemin absolu
  et réussite non prouvée ;
- aucun audio brut ni transcript n'est journalisé par défaut ;
- XTTS exige consentement et opt-in CPML et reste désactivé par défaut ;
- les Voice Packs refusent code exécutable, traversal, lien, signature invalide,
  hash incorrect et preuve de droits absente.

## Licences et identité

Piper est le moteur local générique actuellement prouvé. XTTS-v2 utilise la Coqui
Public Model License ; son usage commercial général n'est pas annoncé. Le système
de packs exige que chaque composant déclare sa licence, sa source et son empreinte.

Le nom « Lumena Voice v1 » n'est pas attribué à une voix générique. Sa création
requiert un corpus enregistré avec consentement couvrant entraînement,
transformation, distribution et usage commercial, puis une écoute humaine
aveugle. Aucun de ces faits n'est simulé par le code.

## Limites et gates restantes

1. Le backend micro emploie un VAD énergétique avec seuil anti-auto-voix. Il ne
   contient pas encore d'AEC serveur ni de modèle Silero livré et vérifié.
2. L'activation « Lumena » est un filtre STT local, pas un modèle KWS dédié ni une
   authentification du locuteur.
3. Piper produit un fichier par segment ; le pipeline est progressif par phrase,
   mais n'émet pas encore de PCM avant la fin de synthèse du segment.
4. Les comparatifs Parakeet/Qwen3-TTS/Chatterbox/CosyVoice exigent un corpus
   consenti et du matériel de référence identique.
5. Le soak 24 h, télévision/musique, second locuteur, microphones ouverts,
   débranchement/rebranchement et écoute de fatigue doivent être exécutés sur les
   profils matériels réels.

## Rollback

- laisser `LUMENA_VOICE_V2_AUTO=0` conserve le backend historique ;
- désactiver `LUMENA_VOICE_CLOUD_ALLOWED` force la chaîne locale ;
- `LUMENA_VOICE_ACTIVATION_MODE=push_to_talk` fournit le secours le plus strict ;
- un Voice Pack conserve la version précédente et expose un rollback atomique ;
- les nouveaux modules Voice V2 sont isolés et peuvent être retirés sans modifier
  les contrats des providers LLM, outils ou missions.

## Décision de publication

Le code peut être relu et versionné comme **Voice V2 logicielle locale avec limites
déclarées**. Il ne faut pas annoncer « voix unique Lumena certifiée », « AEC »,
« faux réveil inférieur à un par jour », « streaming PCM natif » ou une latence
p95 tant que les preuves humaines et matérielles correspondantes ne sont pas
jointes.
