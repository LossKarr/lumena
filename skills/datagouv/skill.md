---
name: datagouv
description: "À utiliser pour toute donnée publique française : recherche de jeux de données data.gouv.fr, entreprises (SIRENE/SIRET/SIREN), géocodage et code INSEE de communes, puis téléchargement et analyse de fichiers de données. RÈGLE LUMENA PRIORITAIRE : utilise TOUJOURS les outils natifs de la catégorie `data` (datagouv_*, sirene_*, geo_*, data_*). N'écris JAMAIS de script Python (requests/pandas) pour interroger data.gouv ou analyser un CSV tant qu'un outil natif couvre le besoin."
keywords: [data.gouv, datagouv, data gouv, open data, opendata, dataset, jeu de données, jeu de donnees, siret, siren, sirene, insee, code insee, code commune, géocodage, geocodage, commune, entreprise, statistiques publiques, csv public]
license: Lumena - usage interne
---

# data.gouv / SIRENE / Géo — Utilise les outils natifs `data`

⛔ **NE CODE PAS de script Python (requests, pandas, urllib) pour interroger
data.gouv.fr, l'API SIRENE, l'API géo, ou pour analyser un CSV.** Lumena a des
outils natifs dédiés (catégorie `data`) qui gèrent l'accès réseau, le SSRF guard,
le rate-limit et l'analyse — en un seul appel.

## Table de routage : tâche → outil natif Lumena

### 🔎 Recherche & métadonnées (data.gouv.fr)
| Tu veux… | Outil | Au lieu de coder |
|---|---|---|
| **Chercher** un jeu de données (50k+ datasets) | `datagouv_search` | ~~requests sur l'API~~ |
| **Métadonnées + ressources** d'un dataset | `datagouv_get_dataset` (URLs `latest` stables) | ~~scraping~~ |
| **Télécharger** une ressource (CSV, JSON…) | `datagouv_download_resource` (→ `workspace/downloads/datagouv/`, param `expected_format`) | ~~urllib.urlretrieve~~ |

### 🏢 Entreprises (SIRENE)
| Tu veux… | Outil |
|---|---|
| Rechercher une **entreprise** par nom | `sirene_search_company` |
| Fiche entreprise par **SIRET** | `sirene_get_by_siret` |

### 📍 Géo / communes
| Tu veux… | Outil |
|---|---|
| **Géocoder** une adresse → coordonnées | `geo_search_address` |
| **Géocodage inverse** (coords → adresse) | `geo_reverse` |
| Infos / **code INSEE** d'une commune | `geo_commune_info` |

### 📊 Analyse de données (fichier téléchargé)
| Tu veux… | Outil | Au lieu de coder |
|---|---|---|
| **Profiler** un fichier (colonnes, types, lignes) | `data_profile_file` | ~~pandas.describe~~ |
| **Agréger / grouper** (count, somme, moyenne) | `data_aggregate` | ~~pandas.groupby~~ |
| **Filtrer** des lignes | `data_filter_rows` | ~~pandas~~ |
| **Valeurs uniques** d'une colonne | `data_unique_values` | — |
| **Joindre** deux jeux de données | `data_join` | ~~pandas.merge~~ |
| **Exporter / convertir** le résultat | `data_export` | — |

## Workflow recommandé (du besoin au résultat)

```
1. datagouv_search           → trouver le bon dataset
2. datagouv_get_dataset      → récupérer la ressource CSV (URL latest stable)
3. datagouv_download_resource→ télécharger dans le workspace
4. data_profile_file         → comprendre la structure avant d'analyser
5. data_aggregate / filter   → produire les chiffres demandés
6. (option) data_export      → sortir un CSV/XLSX final
   (option) create_chart/generate_chart → visualiser
```

## Règles d'usage

1. Identifie la tâche dans les tables ci-dessus et appelle l'outil natif.
2. Pour **analyser** un fichier déjà téléchargé : `data_profile_file` d'abord, puis
   `data_aggregate`/`data_filter_rows` — jamais un script pandas.
3. `datagouv_download_resource` accepte `expected_format` (avertit si mismatch, **pas
   de conversion auto**) — vérifie le format réel avant d'analyser.
4. Ne passe au code Python QUE si la tâche n'est couverte par aucun outil de la
   catégorie `data` (cas rare).
