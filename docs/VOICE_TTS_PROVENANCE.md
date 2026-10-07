# Provenance et décision TTS locale

État vérifié le 28 septembre 2026 pour Voice V3.

## Décision produit

Piper reste le moteur local principal de Lumena pendant cette campagne. Les voix
`fr_FR-siwis-low` et `fr_FR-siwis-medium` sont présentes et le runtime installé est
`piper-tts 1.4.2`. Le choix reste réversible et observable.

Kokoro et sherpa-onnx ne sont pas ajoutés silencieusement aux dépendances de
production. Leurs runtimes ne sont pas installés dans l'environnement certifié et
aucun paquet de voix français, anglais et espagnol n'a encore passé l'écoute humaine
H1-H15. Leur intégration reste éligible via un Voice Pack signé après benchmark.

XTTS-v2 reste une option locale explicitement restreinte. Ses poids sont publiés
sous Coqui Public Model License, qui limite le modèle et ses sorties à un usage non
commercial. Lumena ne doit donc jamais l'activer par défaut dans un produit publié.

## Sources primaires

- Piper maintenu par Open Home Foundation : <https://github.com/OHF-Voice/piper1-gpl>
- sherpa-onnx, familles TTS et paquets Kokoro multilingues : <https://github.com/k2-fsa/sherpa-onnx/blob/master/c-api/docs/tts.dox>
- Poids Kokoro-82M, Apache-2.0 : <https://huggingface.co/hexgrad/Kokoro-82M>
- Licence des poids XTTS-v2 : <https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt>

La licence d'un runtime ne suffit pas : chaque Voice Pack doit aussi déclarer la
licence des poids, de la voix, du corpus et du phonémiseur, avec leurs empreintes.

## Inventaire local audité

| Composant | Source et licence | SHA-256 local |
|---|---|---|
| `fr_FR-siwis-low.onnx` | `rhasspy/piper-voices`, dépôt MIT ; corpus SIWIS CC-BY 4.0 | `a7b7dcaf87229b32af8275cb1a719371234ea677bc0313d2289d5c50ab0ac53d` |
| `fr_FR-siwis-low.onnx.json` | même modèle et mêmes conditions | `9722527d748c284f6dd7639c4b4e661853d4f7d4514079571f67d6a97a652b69` |
| `fr_FR-siwis-medium.onnx` | `rhasspy/piper-voices`, dépôt MIT ; corpus SIWIS CC-BY 4.0 | `641d1ab097da2b81128c076810edb052b385decc8be3381814802a64a73baf99` |
| `fr_FR-siwis-medium.onnx.json` | même modèle et mêmes conditions | `39479916c2db192b5ac9764daddd0c744d83e023ad890c6976c0633ae4df8959` |

Les poids Piper sont ignorés par Git et ne font pas partie du dépôt source. Le
constructeur de l'installateur lit `installer/voice-assets.json`, télécharge les
quatre ressources depuis la révision `v1.0.0`, vérifie chaque SHA-256 puis refuse
la compilation en cas d'écart. Inno Setup les place dans `models/piper` avec le
manifeste de provenance. Les attributions MIT et CC-BY 4.0 restent obligatoires.

Le runtime présent pendant l'audit est `piper-tts 1.4.2`; le projet Piper OHF
actuel est GPL-3.0-or-later. `faster-whisper 1.2.1`, `CTranslate2 4.7.1` et le
modèle `Systran/faster-whisper-small` sont MIT. Silero n'est pas installé dans
l'environnement certifié ; son code et son modèle officiels sont MIT, mais il
reste optionnel tant qu'un pack précis n'a pas été inventorié et signé.

Le frontal acoustique mains libres utilise `pywebrtc-audio 0.2.0` sous licence
Apache-2.0. Son wheel Windows CPython 3.12 x64 est épinglé dans le lock et dans le
wheelhouse hors ligne. Il reçoit la capture et la référence de lecture sous forme
PCM en mémoire ; aucun enregistrement utilisateur n'est créé par ce traitement.

## Reproduire le benchmark local

```powershell
.venv\Scripts\python.exe -m scripts.benchmark_voice_tts `
  --output data\logs\voice-tts-benchmark.json
```

Le rapport ne contient ni transcription utilisateur ni audio. Les WAV du corpus
fixe sont créés dans un dossier temporaire puis supprimés.
