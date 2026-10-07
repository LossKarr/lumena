# Certification humaine et matérielle Voice V3

Cette campagne complète les tests automatiques. Elle mesure ce que le code seul ne
peut pas prouver : le naturel, l'intelligibilité, la fatigue, l'acoustique réelle,
les périphériques Windows, l'installation propre et l'endurance.

Le rapport ne contient ni audio, ni transcription, ni clé. Chaque preuve porte le
nom du testeur, la machine, l'heure et un checksum. Le checksum rend une modification
ultérieure visible ; il ne remplace pas l'identité juridique du signataire.

## Matrice obligatoire

La campagne utilise au moins deux machines distinctes et couvre :

- la machine de développement et une machine Windows propre installée par l'EXE ;
- un profil CPU uniquement et un profil NVIDIA compatible ;
- micro intégré, micro USB et casque Bluetooth ;
- haut-parleur intégré et sortie externe.

La machine `clean_exe` doit référencer le SHA-256 exact de l'installateur testé.
Une même machine ne peut pas signer à la fois les profils `cpu` et `nvidia`.

## Démarrer la campagne

```powershell
.\venv\Scripts\python.exe scripts\certify_voice_v3.py init `
  --report data\logs\voice-v3-certification.json `
  --revision "branche-et-revision-testee"

.\venv\Scripts\python.exe scripts\certify_voice_v3.py add-machine `
  --report data\logs\voice-v3-certification.json `
  --id dev-nvidia `
  --profiles development,nvidia `
  --devices integrated_mic,usb_mic,external_speaker `
  --os "Windows 11 version exacte" `
  --hardware "CPU, RAM, GPU, pilotes"

.\venv\Scripts\python.exe scripts\certify_voice_v3.py add-machine `
  --report data\logs\voice-v3-certification.json `
  --id clean-cpu `
  --profiles clean_exe,cpu `
  --devices bluetooth_headset,integrated_speaker `
  --os "Windows version exacte" `
  --hardware "CPU et RAM" `
  --installer-sha256 "empreinte-de-l-exe"
```

## Scénarios H1 à H15

| ID | Exécution et preuve attendue |
|---|---|
| H1 | Vingt tours naturels sans outil. Noter naturel, intelligibilité et score minimal d'une phrase. |
| H2 | Vingt commandes d'outils variées. Vérifier que la parole n'annonce jamais une réussite avant la preuve. |
| H3 | Vingt interruptions pendant la parole. Mesurer l'arrêt audible et vérifier la reprise depuis le texte non joué. |
| H4 | Hésitations, reprises, phrases suspendues et autocorrections en français, anglais et espagnol. |
| H5 | Rejouer avec télévision puis musique ; relever faux réveils, faux tours et phrases perdues. |
| H6 | Faire parler une deuxième personne ; vérifier l'absence d'escalade de rôle ou de permission. |
| H7 | Volume élevé et double-talk ; vérifier l'interruption et relever honnêtement les limites AEC. |
| H8 | Débrancher puis rebrancher chaque micro et sortie ; la récupération ne doit pas exiger de terminal. |
| H9 | Tuer les workers STT et TTS ; vérifier le diagnostic, le repli borné et la reprise. |
| H10 | Lancer une mission longue, envoyer plusieurs orientations et vérifier l'objectif initial ainsi que leur ordre. |
| H11 | Passer FR→EN→ES→FR, une fois puis durablement, sans dérive non demandée. |
| H12 | Demander traductions et mots étrangers sans mutation accidentelle de la langue de session. |
| H13 | Converser vingt minutes et noter la fatigue ; la moyenne doit rester au plus à deux sur cinq. |
| H14 | Laisser le produit fonctionner vingt-quatre heures avec le moniteur d'endurance. |
| H15 | Installer l'EXE sur Windows propre sans modèle en cache, configurer depuis l'interface et lancer sans terminal. |

Exemple d'enregistrement :

```powershell
.\venv\Scripts\python.exe scripts\certify_voice_v3.py record `
  --report data\logs\voice-v3-certification.json `
  --scenario H1 --machine dev-nvidia --result pass `
  --tester "Nom du testeur" --iterations 20 `
  --naturalness 4.3 --intelligibility 4.6 `
  --minimum-phrase-score 3 --notes "Conditions et anomalies observées"
```

Une anomalie de langue, un terminal nécessaire ou une anomalie critique se déclare
avec `--language-drift`, `--terminal-required` ou `--critical-anomaly`. Ces valeurs
font échouer la gate ; elles ne sont jamais masquées par une moyenne.

## Endurance H14

Lancer Lumena et relever son PID. Le token reste dans l'environnement et n'est jamais
écrit dans le rapport :

```powershell
$env:LUMENA_ADMIN_TOKEN = "token-local-de-la-machine"
.\venv\Scripts\python.exe scripts\voice_v3_endurance.py `
  --pid 1234 --duration-hours 24 --interval-s 30 `
  --base-url http://127.0.0.1:8080 `
  --output data\logs\voice-v3-endurance.json
```

Le moniteur enregistre RSS, threads, enfants du processus et un sous-ensemble expurgé
du statut voix. Une durée inférieure à vingt-quatre heures, une erreur de sondage ou
un runtime vocal arrêté donne `NON CERTIFIÉE`.

## Vérifier et signer

```powershell
.\venv\Scripts\python.exe scripts\certify_voice_v3.py check `
  --report data\logs\voice-v3-certification.json

.\venv\Scripts\python.exe scripts\certify_voice_v3.py finalize `
  --report data\logs\voice-v3-certification.json `
  --signer "Responsable de publication"
```

La finalisation est refusée tant que H1 à H15 ne sont pas `PASS`, que la matrice
matérielle est incomplète, que les volumes minimaux ne sont pas atteints, que les
scores manquent, qu'une preuve a été altérée ou que H14 ne représente pas une durée
réelle de vingt-quatre heures.
