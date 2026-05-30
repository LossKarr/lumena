---
name: xlsx
description: "À utiliser quand un tableur (.xlsx/.xlsm/.csv/.tsv) est l'entrée ou la sortie principale : créer, lire, éditer, nettoyer, calculer, grapher. RÈGLE LUMENA PRIORITAIRE : Lumena possède des OUTILS NATIFS (create_xlsx, edit_xlsx, create_csv, data_profile_file, data_aggregate, data_filter_rows, generate_chart, data_export). Utilise TOUJOURS ces outils. N'écris JAMAIS de script Python (openpyxl/pandas) pour produire ou modifier un tableur tant qu'un outil natif couvre le besoin. Ne pas utiliser si le livrable est un Word, un PDF ou du code."
keywords: [excel, xlsx, csv, tableur, feuille calcul, spreadsheet, tableau donnees, colonnes, formules, graphique excel, nettoyer donnees, creer tableur, fichier excel, tsv, xlsm]
license: Lumena - usage interne
---

# XLSX / Tableurs — Utilise les outils natifs Lumena, pas du code

⛔ **NE CODE PAS de script Python (openpyxl, pandas) pour produire ou modifier un
tableur.** Lumena a des outils natifs qui le font sans écrire de `.py`. Le code n'est
qu'un dernier recours.

## Table de routage : tâche → outil natif Lumena

| Tu veux… | Utilise l'outil | Au lieu de coder |
|---|---|---|
| **Créer un .xlsx** | `create_xlsx` | ~~openpyxl~~ |
| **Créer un .csv** | `create_csv` | ~~csv/pandas~~ |
| **Éditer** un tableur (colonnes, formules, formatage) | `edit_xlsx` | ~~openpyxl~~ |
| **Profiler / explorer** un fichier de données | `data_profile_file`, `data_unique_values` | ~~pandas.describe~~ |
| **Agréger / grouper** (somme, count, moyenne) | `data_aggregate` | ~~pandas.groupby~~ |
| **Filtrer** des lignes | `data_filter_rows` | ~~pandas~~ |
| **Joindre** deux jeux de données | `data_join` | ~~pandas.merge~~ |
| **Exporter / convertir** (csv↔xlsx…) | `data_export` | — |
| Créer un **graphique** | `generate_chart` | ~~matplotlib~~ |
| **Lire** un tableur | `read_document` / `data_profile_file` | — |

## Règle d'usage

1. Trouve la tâche dans la table et appelle l'outil natif.
2. Pour analyser un CSV/XLSX déjà téléchargé, la suite `data_*` (profile → aggregate →
   filter) est la bonne approche — pas un script pandas.
3. Ne passe au code openpyxl/pandas QUE si la tâche n'est couverte par aucun outil natif.
