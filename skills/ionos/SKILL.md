---
name: ionos
description: "À utiliser pour l'hébergement IONOS : déploiement de site, gestion des fichiers distants, et base de données MySQL via le bridge sécurisé. RÈGLE LUMENA PRIORITAIRE : utilise TOUJOURS les outils natifs `ionos_*` / `deploy_to_ionos`. Pour la BDD, passe EXCLUSIVEMENT par le bridge `ionos_db_*` (jamais config.php, shell, PHP, MySQL direct ou CodeAgent)."
keywords: [ionos, hebergement, hosting, deployer site, deploy, sftp, base de donnees ionos, bdd ionos, mysql ionos, site web ionos, openlumena, hosting-data]
license: Lumena - usage interne
---

# IONOS — Déploiement, fichiers & BDD via outils natifs

⛔ **Pour la BDD IONOS, le bridge `ionos_db_*` est le SEUL chemin.** Jamais de
`config.php`, shell, PHP, client MySQL direct, ni CodeAgent pour toucher une BDD IONOS.
Ne jamais **inventer** un identifiant, un chemin ou un nom de site — lister d'abord.

## Table de routage : besoin → outil natif

### Sites & fichiers
| Tu veux… | Outil |
|---|---|
| Lister les sites configurés | `ionos_list_sites` |
| Ajouter / retirer un site | `ionos_add_site` · `ionos_remove_site` *(confirmation)* |
| Lister les fichiers distants | `ionos_list_files` |
| **Déployer** un site | `deploy_to_ionos` *(confirmation)* |
| Mettre à jour des fichiers | `update_ionos_files` *(confirmation)* |
| Supprimer des fichiers distants | `ionos_delete_files` *(confirmation explicite)* |
| Tester la connexion BDD d'un site | `ionos_test_site_database` |

### BDD — lecture (sûr, lecture seule)
| Tu veux… | Outil |
|---|---|
| Statut du bridge | `ionos_db_bridge_status` |
| Config BDD (masquée) | `ionos_db_get_config` |
| Lister les tables | `ionos_db_list_tables` |
| Décrire une table (structure) | `ionos_db_describe_table` |
| Lire des lignes | `ionos_db_select` |
| Lister snapshots / actions en attente | `ionos_db_list_snapshots` · `ionos_db_list_pending_actions` |

### BDD — écriture (PROPOSE-ONLY : l'humain approuve dans le panel)
| Tu veux… | Outil |
|---|---|
| Proposer un INSERT/UPDATE | `ionos_db_propose_write` |
| Proposer un DELETE | `ionos_db_propose_delete` |
| Proposer création table sandbox | `ionos_db_create_sandbox_table` |
| Proposer vider / drop sandbox | `ionos_db_propose_clear_sandbox_table` · `ionos_db_propose_drop_sandbox_table` |
| Installer / configurer le bridge | `ionos_db_install_bridge` · `ionos_db_set_*_config` |

## Règles de sécurité
1. **Lecture d'abord** : `ionos_list_sites` / `ionos_db_list_tables` / `ionos_db_select` avant toute action.
2. **Écriture BDD = propose-only** : les `ionos_db_propose_*` n'exécutent PAS — l'humain approuve dans le panel.
3. **Confirmation explicite** avant tout `deploy_to_ionos`, `ionos_delete_files`, `ionos_remove_site`, DELETE/DROP.
4. **Sandbox + snapshot** quand disponible avant une opération risquée (restore possible).
5. **Ne jamais inventer** site, identifiant, chemin ou table — toujours vérifier via les outils de liste.
6. Les secrets (config.php/.env) sont **masqués** : ne jamais tenter de les reconstituer.
