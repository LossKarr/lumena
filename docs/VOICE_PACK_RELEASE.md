# Publication d'un Voice Pack Lumena

Cette procédure transforme une voix Piper ou une identité Qwen3-TTS déjà créée et
juridiquement validée en archive installable depuis **Voice > Modèles**. Elle ne
crée pas une voix, ne fabrique pas un consentement et ne télécharge rien. La clé privée reste
hors du dépôt, du `.env`, de l'installateur et de l'interface Lumena.

## Conditions avant publication

- les fichiers `.onnx` et `.onnx.json` ont été testés avec la version Piper du
  lock produit ;
- chaque corpus et composant possède une licence de redistribution compatible ;
- la personne dont la voix est reconnaissable a signé le périmètre d'utilisation,
  de transformation et de distribution ;
- une copie expurgée de cette preuve est incluse dans le payload signé ;
- l'écoute en aveugle et les scénarios H11/H12 ont été exécutés avant promotion.

Une archive signée atteste que le responsable de publication a validé ces faits.
Elle ne remplace pas les documents juridiques originaux.

## Créer la clé de publication

Choisir un dossier de secrets hors du dépôt, puis définir un mot de passe d'au
moins seize caractères uniquement dans le processus courant :

```powershell
$env:LUMENA_VOICE_PACK_SIGNING_KEY_PASSWORD = "mot-de-passe-release"
.\venv\Scripts\python.exe tools\release\build_voice_pack.py generate-key `
  --private-key C:\LumenaReleaseSecrets\voice-pack-release.pem `
  --public-key-json config\voice_pack_trusted_keys.json `
  --key-id release-2026
```

La clé privée est un PKCS#8 Ed25519 chiffré et aucun fichier existant n'est
écrasé. Le JSON public peut être distribué avec Lumena. Il ne contient aucun
secret. Sur Windows, le responsable de publication doit également limiter les
ACL NTFS du fichier privé à son compte et conserver une sauvegarde chiffrée.

## Préparer le descripteur

Les chemins sont relatifs au dossier passé avec `--assets-dir`. Aucun glob,
chemin absolu, lien symbolique ou exécutable n'est accepté.

```json
{
  "pack_id": "lumena-voice",
  "version": "1.0.0",
  "label": "Lumena Voice",
  "engine": "piper",
  "models": [
    {
      "language": "fr-FR",
      "model_name": "lumena_fr_medium",
      "model_path": "models/lumena_fr_medium.onnx",
      "config_path": "models/lumena_fr_medium.onnx.json"
    }
  ],
  "payload_files": ["legal/CONSENT_REDACTED.md", "legal/LICENSE.md"],
  "licenses": [
    {
      "component": "Lumena Voice",
      "license": "Contrat de distribution Lumena Voice",
      "source": "legal/LICENSE.md"
    }
  ],
  "voice_consent": {
    "confirmed": true,
    "rights_scope": "training, transformation, distribution, commercial use",
    "evidence": "legal/CONSENT_REDACTED.md"
  }
}
```

Le builder calcule lui-même toutes les tailles et empreintes. Il refuse les champs
`files` et `signature` fournis manuellement. La preuve de consentement doit être
un document déclaré dans le payload signé.

Pour Qwen3-TTS, remplacer `models` par le contrat suivant et fournir les quatre
fichiers dans `assets-dir` :

```json
{
  "pack_id": "lumena-qwen",
  "version": "1.0.0",
  "label": "Lumena Voice",
  "engine": "qwen3",
  "languages": ["fr-FR", "en", "es"],
  "qwen": {
    "model_id": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "model_revision": "5d83992436eae1d760afd27aff78a71d676296fc",
    "reference_path": "identity/reference.wav",
    "transcript_path": "identity/reference.txt",
    "config_path": "identity/voice.json",
    "provenance_path": "identity/provenance.json"
  },
  "payload_files": ["legal/CONSENT_REDACTED.md", "legal/LICENSE.md"],
  "licenses": [{
    "component": "Lumena Voice",
    "license": "Contrat de distribution Lumena Voice",
    "source": "legal/LICENSE.md"
  }],
  "voice_consent": {
    "confirmed": true,
    "rights_scope": "training, transformation, distribution, commercial use",
    "evidence": "legal/CONSENT_REDACTED.md"
  }
}
```

`identity/voice.json` doit contenir `{"schema_version": 1}` et les paramètres
vérifiés de l'identité. `identity/provenance.json` doit déclarer une méthode de
création non vide et `distribution_rights_confirmed: true`. Le builder puis le
service produit refusent une révision flottante, un transcript vide, une provenance
incomplète ou des droits non confirmés.

## Construire et vérifier

```powershell
.\venv\Scripts\python.exe tools\release\build_voice_pack.py build `
  --descriptor C:\LumenaRelease\lumena-voice-1.0.0.json `
  --assets-dir C:\LumenaRelease\lumena-voice-1.0.0 `
  --private-key C:\LumenaReleaseSecrets\voice-pack-release.pem `
  --output C:\LumenaRelease\lumena-voice-1.0.0.zip `
  --key-id release-2026
```

La sortie est déterministe : mêmes fichiers, même descripteur et même clé donnent
le même ZIP et le même SHA-256. Les modèles sont copiés par blocs, sans être chargés
entièrement en mémoire. Avant le remplacement atomique du fichier de sortie, le
builder repasse l'archive dans `VoicePackManager.inspect`, le même vérificateur que
le panneau produit.

Le mot de passe peut ensuite être retiré de la session :

```powershell
Remove-Item Env:LUMENA_VOICE_PACK_SIGNING_KEY_PASSWORD
```

La rotation consiste à créer un nouvel identifiant de clé, publier sa clé publique,
signer la génération suivante, puis conserver l'ancienne clé publique tant que des
packs installables signés par elle doivent rester acceptés. Retirer une clé publique
révoque toute nouvelle installation et toute réactivation de ses packs.
