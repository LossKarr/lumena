# Provenance et décision TTS locale

État vérifié le 11 octobre 2026 pour Voice V3.

## Décision produit

Piper reste le moteur conversationnel local universel de Lumena. Les voix
`fr_FR-siwis-low` et `fr_FR-siwis-medium` sont présentes et le runtime installé est
`piper-tts 1.4.2`. Le choix reste réversible et observable.

Qwen3-TTS 0.6B Base est intégré comme moteur local haute fidélité isolé. Le modèle
est épinglé à la révision
`5d83992436eae1d760afd27aff78a71d676296fc`. Le spike Windows a mesuré un RTF
d'environ 4,33 sur CPU et 2,0 sur RTX 3060 CUDA : ce backend par fichiers n'est donc
pas promu automatiquement sur cette machine. Il reste sélectionnable ; le routeur
`auto` conserve Piper tant qu'un canari local ne prouve pas la fluidité attendue.

Le runtime Qwen qualifié est figé dans un lock, un SBOM et un inventaire de licences,
puis découpé en assets signés sous la limite GitHub. Le modèle principal et son
tokenizer vocal sont publiés séparément et réassemblés localement par liens physiques.
L'installateur vérifie manifeste Ed25519, tailles et SHA-256 avant un vrai canari de
synthèse. Aucune clé privée, référence non consentie ou poids flottant n'entre dans
le dépôt ou l'EXE.

Kokoro et sherpa-onnx ne sont pas ajoutés silencieusement aux dépendances de
production. Leurs runtimes ne sont pas installés dans l'environnement certifié et
aucun paquet de voix français, anglais et espagnol n'a encore passé l'écoute humaine
H1-H15. Leur intégration reste éligible via un Voice Pack signé après benchmark.

XTTS-v2 reste une option locale explicitement restreinte. Ses poids sont publiés
sous Coqui Public Model License, qui limite le modèle et ses sorties à un usage non
commercial. Lumena ne doit donc jamais l'activer par défaut dans un produit publié.

## Gate multilingue

Le routage logiciel accepte le français, l'anglais et l'espagnol, mais le produit
ne prétend pas livrer une voix locale dans une langue tant qu'un pack signé n'a pas
passé la revue des droits et l'écoute humaine. Au 10 octobre 2026 :

- `en_US-lessac-medium` ne doit pas être embarqué : sa carte renvoie au contrat
  Blizzard/Lessac réservé à la recherche et excluant l'usage commercial ;
- `es_ES-davefx-medium` déclare un corpus CC0, mais reste candidat tant que ses
  empreintes, sa chaîne de dérivation et son rendu n'ont pas passé la gate ;
- `en_GB-cori-medium` est entraîné depuis zéro sur des enregistrements LibriVox
  déclarés dans le domaine public ; c'est le candidat anglais juridiquement le
  plus clair de cet audit ;
- `es_ES-carlfm-x_low` est entraîné depuis zéro sur un corpus déclaré domaine
  public, mais sa qualité `x_low` ne permet pas de le promouvoir comme voix
  produit sans écoute comparative ;
- lorsqu'aucune voix locale compatible n'est installée, Lumena bloque honnêtement
  la synthèse dans cette langue au lieu d'utiliser la voix française.

Les deux candidats entraînés depuis zéro restent non promus : une licence de corpus
ne constitue pas à elle seule une preuve explicite de consentement à l'identité
vocale, et H11/H12 n'ont pas été exécutés. L'infrastructure permet maintenant de
les remplacer ultérieurement par un pack signé sans changer le runtime.

Sources :

- carte Lessac : <https://huggingface.co/rhasspy/piper-voices/raw/v1.0.0/en/en_US/lessac/medium/MODEL_CARD>
- licence Blizzard/Lessac : <https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html>
- carte davefx : <https://huggingface.co/rhasspy/piper-voices/raw/v1.0.0/es/es_ES/davefx/medium/MODEL_CARD>
- carte Cori (révision auditée `c10ece1aade47bb51c153c893d14e5bf8e5b7117`) : <https://huggingface.co/rhasspy/piper-voices/raw/c10ece1aade47bb51c153c893d14e5bf8e5b7117/en/en_GB/cori/medium/MODEL_CARD>
- carte carlfm (même révision) : <https://huggingface.co/rhasspy/piper-voices/raw/c10ece1aade47bb51c153c893d14e5bf8e5b7117/es/es_ES/carlfm/x_low/MODEL_CARD>

## Sources primaires

- Piper maintenu par Open Home Foundation : <https://github.com/OHF-Voice/piper1-gpl>
- sherpa-onnx, familles TTS et paquets Kokoro multilingues : <https://github.com/k2-fsa/sherpa-onnx/blob/master/c-api/docs/tts.dox>
- Poids Kokoro-82M, Apache-2.0 : <https://huggingface.co/hexgrad/Kokoro-82M>
- Licence des poids XTTS-v2 : <https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt>
- Qwen3-TTS officiel, code et modèles : <https://github.com/QwenLM/Qwen3-TTS>

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
actuel est GPL-3.0-or-later. Cette licence concerne le runtime ; elle ne doit pas
être confondue avec la licence du dépôt de poids ni avec celle du corpus SIWIS.
`faster-whisper 1.2.1`, `CTranslate2 4.7.1` et le
modèle `Systran/faster-whisper-small` sont MIT. Silero n'est pas installé dans
l'environnement certifié ; son code et son modèle officiels sont MIT, mais il
reste optionnel tant qu'un pack précis n'a pas été inventorié et signé.

Le frontal acoustique mains libres utilise `pywebrtc-audio 0.2.0` sous licence
Apache-2.0. Son wheel Windows CPython 3.12 x64 est épinglé dans le lock et dans le
wheelhouse hors ligne. Il reçoit la capture et la référence de lecture sous forme
PCM en mémoire ; aucun enregistrement utilisateur n'est créé par ce traitement.

## Reproduire le benchmark local

```powershell
venv\Scripts\python.exe -m scripts.benchmark_voice_tts `
  --output data\logs\voice-tts-benchmark.json
```

Le rapport ne contient ni transcription utilisateur ni audio. Les WAV du corpus
fixe sont créés dans un dossier temporaire puis supprimés.
