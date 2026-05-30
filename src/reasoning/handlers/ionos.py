"""
ionos.py — Handlers IONOS pour le déploiement SFTP de sites web.

Handlers: deploy_to_ionos, update_ionos_files, ionos_add_site,
          ionos_remove_site, ionos_list_sites, ionos_list_files,
          ionos_delete_files, ionos_test_site_database,
          ionos_set_site_database, ionos_clear_site_database,
          ionos_db_list_tables, ionos_db_describe_table, ionos_db_select,
          ionos_db_propose_write (4.5A : propose-only INSERT/UPDATE),
          ionos_db_propose_delete (4.5B : propose-only DELETE, OFF par défaut).
          Aucun handler n'exécute d'écriture/suppression : exécution humaine via le panel.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

# Tables marquées sensibles : jamais choisies automatiquement ; un avertissement
# est ajouté si l'utilisateur les demande explicitement (read-only quand même).
_IONOS_SENSITIVE_TABLES = frozenset({"users", "sessions", "verification_codes"})
# Borne UI/handler : limit défaut 20, max 100 (le service borne déjà à 1000).
_IONOS_DB_PREVIEW_DEFAULT = 20
_IONOS_DB_PREVIEW_MAX = 100


# ── Lazy deployer singleton ──────────────────────────────────────────────

_deployer = None


def _get_deployer():
    # Singleton PARTAGÉ avec web.routes.ionos (même instance → état _sites cohérent).
    # Une injection explicite (_deployer non None, ex. en test) reste prioritaire.
    global _deployer
    if _deployer is None:
        from src.services.ionos_deployer import get_shared_deployer
        _deployer = get_shared_deployer()
    return _deployer


# ── Handlers ─────────────────────────────────────────────────────────────

async def deploy_to_ionos_handler(
    ctx: HandlerContext,
    site: str = "",
    project_dir: str = "",
    dry_run: str = "false",
) -> HandlerResult:
    """Déployer un projet web complet sur un hébergement IONOS via SFTP."""
    try:
        deployer = _get_deployer()

        # Resolve site
        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié et LUMENA_IONOS_DEFAULT_SITE est vide. "
                "Précise le domaine (ex: deploy_to_ionos site=lumena.fr).",
                handler_name="deploy_to_ionos",
            )

        # Resolve project directory
        if not project_dir:
            from src.utils.project_registry import find_project
            found = find_project("")
            if found:
                project_dir = str(found)
            else:
                return HandlerResult.fail(
                    "Aucun projet trouvé. Précise project_dir ou crée un projet d'abord.",
                    handler_name="deploy_to_ionos",
                )

        is_dry = dry_run.lower() in ("true", "1", "yes", "oui")

        result = await deployer.deploy(
            site, Path(project_dir), dry_run=is_dry
        )

        if not result.success:
            return HandlerResult.fail(
                f"❌ Déploiement échoué sur {site}:\n" + "\n".join(result.errors),
                handler_name="deploy_to_ionos",
            )

        mode = " (DRY RUN)" if result.dry_run else ""
        return HandlerResult.ok(
            f"✅ Déploiement{mode} sur **{site}** terminé.\n"
            f"• Fichiers uploadés : {result.uploaded}\n"
            f"• Fichiers ignorés : {result.skipped}\n"
            f"• Taille totale : {result.total_bytes / 1024:.1f} Ko\n"
            f"• Durée : {result.duration_sec:.1f}s",
            handler_name="deploy_to_ionos",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur déploiement IONOS: {e}",
            handler_name="deploy_to_ionos",
        )


async def update_ionos_files_handler(
    ctx: HandlerContext,
    site: str = "",
    files: str = "",
) -> HandlerResult:
    """Mettre à jour des fichiers spécifiques sur un site IONOS."""
    try:
        deployer = _get_deployer()

        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié.",
                handler_name="update_ionos_files",
            )

        if not files:
            return HandlerResult.fail(
                "Aucun fichier spécifié. Paramètre 'files' requis (chemins séparés par des virgules).",
                handler_name="update_ionos_files",
            )

        file_pairs = []
        for f in files.split(","):
            f = f.strip()
            if not f:
                continue
            p = Path(f)
            if not p.is_file():
                return HandlerResult.fail(
                    f"Fichier introuvable: {f}",
                    handler_name="update_ionos_files",
                )
            file_pairs.append((p.name, p))

        result = await deployer.upload_files(site, file_pairs)

        if not result.success:
            return HandlerResult.fail(
                f"❌ Upload échoué:\n" + "\n".join(result.errors),
                handler_name="update_ionos_files",
            )

        return HandlerResult.ok(
            f"✅ {result.uploaded} fichier(s) mis à jour sur **{site}**.",
            handler_name="update_ionos_files",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur update IONOS: {e}",
            handler_name="update_ionos_files",
        )


async def ionos_add_site_handler(
    ctx: HandlerContext,
    domain: str = "",
    host: str = "",
    user: str = "",
    password: str = "",
    port: str = "22",
    root: str = "/",
    label: str = "",
) -> HandlerResult:
    """Ajouter un nouveau site IONOS (credentials SFTP)."""
    try:
        if not domain or not host or not user or not password:
            return HandlerResult.fail(
                "Paramètres requis: domain, host, user, password.",
                handler_name="ionos_add_site",
            )

        deployer = _get_deployer()
        result = deployer.add_site(
            domain=domain, host=host, user=user,
            password=password, port=int(port),
            root=root, label=label,
        )
        return HandlerResult.ok(
            f"✅ Site IONOS ajouté: **{domain}** → {host}\n"
            f"Connexion SFTP testée avec succès.",
            handler_name="ionos_add_site",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur ajout site: {e}",
            handler_name="ionos_add_site",
        )


async def ionos_remove_site_handler(
    ctx: HandlerContext,
    domain: str = "",
) -> HandlerResult:
    """Supprimer un site IONOS de la configuration."""
    try:
        if not domain:
            return HandlerResult.fail(
                "Paramètre 'domain' requis.",
                handler_name="ionos_remove_site",
            )
        deployer = _get_deployer()
        deployer.remove_site(domain)
        return HandlerResult.ok(
            f"✅ Site **{domain}** supprimé de la configuration IONOS.",
            handler_name="ionos_remove_site",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur suppression: {e}",
            handler_name="ionos_remove_site",
        )


async def ionos_list_sites_handler(
    ctx: HandlerContext,
) -> HandlerResult:
    """Lister tous les sites IONOS configurés."""
    try:
        deployer = _get_deployer()
        sites = deployer.list_sites()

        if not sites:
            return HandlerResult.ok(
                "Aucun site IONOS configuré. Utilise `ionos_add_site` pour en ajouter un.",
                handler_name="ionos_list_sites",
            )

        lines = ["**Sites IONOS configurés :**\n"]
        for s in sites:
            deploy_info = f" (dernier déploiement: {s['last_deploy']})" if s["last_deploy"] else ""
            lines.append(
                f"• **{s['domain']}** — {s['host']}:{s['port']} "
                f"(user: {s['user']}, root: {s['root']}){deploy_info}"
            )
        return HandlerResult.ok(
            "\n".join(lines),
            handler_name="ionos_list_sites",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur listing: {e}",
            handler_name="ionos_list_sites",
        )


async def ionos_list_files_handler(
    ctx: HandlerContext,
    site: str = "",
    path: str = "/",
) -> HandlerResult:
    """Lister les fichiers présents sur un site IONOS."""
    try:
        deployer = _get_deployer()

        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié.",
                handler_name="ionos_list_files",
            )

        files = await deployer.list_remote(site, path)

        if not files:
            return HandlerResult.ok(
                f"Aucun fichier trouvé sur **{site}** dans `{path}`.",
                handler_name="ionos_list_files",
            )

        lines = [f"**Fichiers sur {site} ({path}) :**\n"]
        for f in files:
            icon = "📁" if f.is_dir else "📄"
            size = f"({f.size:,} octets)" if not f.is_dir else ""
            lines.append(f"  {icon} {f.path} {size}")

        return HandlerResult.ok(
            "\n".join(lines),
            handler_name="ionos_list_files",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur listing fichiers: {e}",
            handler_name="ionos_list_files",
        )


async def ionos_delete_files_handler(
    ctx: HandlerContext,
    site: str = "",
    paths: str = "",
) -> HandlerResult:
    """Supprimer des fichiers sur un site IONOS."""
    try:
        deployer = _get_deployer()

        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié.",
                handler_name="ionos_delete_files",
            )

        if not paths:
            return HandlerResult.fail(
                "Paramètre 'paths' requis (chemins distants séparés par des virgules).",
                handler_name="ionos_delete_files",
            )

        path_list = [p.strip() for p in paths.split(",") if p.strip()]
        result = await deployer.delete_remote(site, path_list)

        if not result["success"]:
            return HandlerResult.fail(
                f"❌ Suppression partielle:\n" + "\n".join(result["errors"]),
                handler_name="ionos_delete_files",
            )

        return HandlerResult.ok(
            f"✅ {result['deleted']} fichier(s) supprimé(s) sur **{site}**.",
            handler_name="ionos_delete_files",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur suppression: {e}",
            handler_name="ionos_delete_files",
        )


async def ionos_test_site_database_handler(
    ctx: HandlerContext,
    site: str = "",
) -> HandlerResult:
    """Tester la connexion à la BDD associée à un site IONOS.

    Lecture seule : effectue un PING de connexion uniquement, ne lit ni ne
    modifie aucune donnée. N'affiche jamais le mot de passe.
    """
    try:
        deployer = _get_deployer()

        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié.",
                handler_name="ionos_test_site_database",
            )

        result = deployer.test_database_connection(site)

        if not result.get("configured", False):
            return HandlerResult.fail(
                f"Aucune BDD configurée pour le site **{site}**. "
                "Configure-la d'abord (host, nom, user, mot de passe).",
                handler_name="ionos_test_site_database",
            )

        if result.get("ok"):
            return HandlerResult.ok(
                f"✅ Connexion BDD OK sur **{site}** "
                f"(latence {result.get('latency_ms', 0)} ms).",
                handler_name="ionos_test_site_database",
            )

        return HandlerResult.fail(
            f"❌ Connexion BDD échouée sur **{site}** : "
            f"{result.get('message') or 'échec de connexion'}",
            handler_name="ionos_test_site_database",
        )
    except KeyError:
        return HandlerResult.fail(
            f"Site '{site}' introuvable.",
            handler_name="ionos_test_site_database",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur test BDD: {e}",
            handler_name="ionos_test_site_database",
        )


async def ionos_set_site_database_handler(
    ctx: HandlerContext,
    site: str = "",
    host: str = "",
    name: str = "",
    user: str = "",
    password: str = "",
    port: str = "3306",
    label: str = "",
    description: str = "",
    engine: str = "mariadb",
    version: str = "",
) -> HandlerResult:
    """Associer/modifier la BDD d'un site IONOS.

    Le mot de passe est chiffré et n'est jamais réaffiché. Si `password` est
    vide en modification, le secret existant est conservé. Aucune connexion
    réelle ici (utiliser `ionos_test_site_database` pour tester).
    """
    try:
        deployer = _get_deployer()
        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié.", handler_name="ionos_set_site_database",
            )
        try:
            _port = int(port) if str(port).strip() else 3306
        except ValueError:
            _port = 3306
        deployer.set_site_database(
            site, host=host, name=name, user=user, password=password,
            port=_port, label=label, description=description,
            engine=engine, version=version,
        )
        cfg = deployer.get_site_database(site)  # non sensible
        return HandlerResult.ok(
            f"✅ BDD associée au site **{site}** : "
            f"{cfg.get('host', '')}:{cfg.get('port', 3306)} "
            f"(base: {cfg.get('name', '')}, user: {cfg.get('user', '')}, "
            f"moteur: {cfg.get('engine', '')}). Mot de passe chiffré, jamais affiché.",
            handler_name="ionos_set_site_database",
        )
    except KeyError:
        return HandlerResult.fail(
            f"Site '{site}' introuvable.", handler_name="ionos_set_site_database",
        )
    except ValueError as e:
        return HandlerResult.fail(
            f"❌ {e}", handler_name="ionos_set_site_database",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur config BDD: {e}", handler_name="ionos_set_site_database",
        )


async def ionos_clear_site_database_handler(
    ctx: HandlerContext,
    site: str = "",
) -> HandlerResult:
    """Retirer la BDD associée à un site IONOS (laisse le site SFTP intact)."""
    try:
        deployer = _get_deployer()
        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié.", handler_name="ionos_clear_site_database",
            )
        deployer.clear_site_database(site)
        return HandlerResult.ok(
            f"✅ BDD retirée du site **{site}** (le site SFTP reste intact).",
            handler_name="ionos_clear_site_database",
        )
    except KeyError:
        return HandlerResult.fail(
            f"Site '{site}' introuvable.", handler_name="ionos_clear_site_database",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur suppression BDD: {e}", handler_name="ionos_clear_site_database",
        )


async def ionos_db_list_tables_handler(
    ctx: HandlerContext,
    site: str = "",
) -> HandlerResult:
    """Lister les tables de la BDD d'un site IONOS (lecture seule, via bridge)."""
    try:
        deployer = _get_deployer()
        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail("Aucun site spécifié.", handler_name="ionos_db_list_tables")
        r = deployer.db_list_tables(site)
        if not r.get("ok"):
            return HandlerResult.fail(
                f"❌ Lecture BDD impossible sur **{site}** : {r.get('message') or r.get('error') or 'erreur'}",
                handler_name="ionos_db_list_tables",
            )
        tables = r.get("tables", [])
        logger.info("[IONOS DB READ] site={} op=list_tables n={}", site, len(tables))
        lines = [f"**Tables BDD de {site}** ({len(tables)}) :"]
        for t in tables:
            warn = " ⚠️ sensible" if t.lower() in _IONOS_SENSITIVE_TABLES else ""
            lines.append(f"• `{t}`{warn}")
        return HandlerResult.ok("\n".join(lines), handler_name="ionos_db_list_tables")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_list_tables")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur lecture BDD: {e}", handler_name="ionos_db_list_tables")


async def ionos_db_describe_table_handler(
    ctx: HandlerContext,
    site: str = "",
    table: str = "",
) -> HandlerResult:
    """Décrire les colonnes d'une table (lecture seule, via bridge)."""
    try:
        deployer = _get_deployer()
        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site or not table:
            return HandlerResult.fail("Paramètres 'site' et 'table' requis.", handler_name="ionos_db_describe_table")
        r = deployer.db_describe_table(site, table)
        if not r.get("ok"):
            return HandlerResult.fail(
                f"❌ Schéma indisponible : {r.get('message') or r.get('error') or 'erreur'}",
                handler_name="ionos_db_describe_table",
            )
        logger.info("[IONOS DB READ] site={} op=describe table={}", site, table)
        warn = "\n⚠️ Table marquée **sensible**.\n" if table.lower() in _IONOS_SENSITIVE_TABLES else ""
        lines = [f"**Schéma de `{table}` ({site})**{warn}", "", "| Colonne | Type | Null | Clé | Défaut | Extra |", "|---|---|---|---|---|---|"]
        for c in r.get("columns", []):
            lines.append(
                f"| {c.get('field','')} | {c.get('type','')} | {c.get('null','')} | "
                f"{c.get('key','')} | {c.get('default')} | {c.get('extra','')} |"
            )
        # Garde anti-hallucination : describe = STRUCTURE seulement, jamais le contenu.
        lines.append(
            "\n_ℹ️ Ceci décrit la **structure** uniquement — pas le contenu. Pour savoir si la "
            "table contient des lignes (avant un DROP/CLEAR), utilise `ionos_db_select` ; "
            "n'en déduis JAMAIS le nombre de lignes depuis ce schéma._"
        )
        return HandlerResult.ok("\n".join(lines), handler_name="ionos_db_describe_table")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_describe_table")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur schéma BDD: {e}", handler_name="ionos_db_describe_table")


async def ionos_db_select_handler(
    ctx: HandlerContext,
    site: str = "",
    table: str = "",
    columns: str = "",
    where: str = "",
    limit: str = str(_IONOS_DB_PREVIEW_DEFAULT),
) -> HandlerResult:
    """Aperçu read-only borné d'une table (SELECT structuré via bridge).

    `columns` : noms séparés par des virgules (optionnel). `where` : `col=valeur`
    (égalité simple, optionnel). `limit` : défaut 20, max 100. Aucune écriture,
    aucun SQL libre.
    """
    try:
        deployer = _get_deployer()
        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site or not table:
            return HandlerResult.fail("Paramètres 'site' et 'table' requis.", handler_name="ionos_db_select")
        # Borne limit : défaut 20, max 100 (couche exposition).
        try:
            _limit = int(limit) if str(limit).strip() else _IONOS_DB_PREVIEW_DEFAULT
        except ValueError:
            _limit = _IONOS_DB_PREVIEW_DEFAULT
        _limit = max(1, min(_limit, _IONOS_DB_PREVIEW_MAX))
        _cols = [c.strip() for c in columns.split(",") if c.strip()] or None
        _where = None
        if where.strip():
            if "=" not in where:
                return HandlerResult.fail("Filtre 'where' attendu sous la forme col=valeur.", handler_name="ionos_db_select")
            k, v = where.split("=", 1)
            _where = {k.strip(): v.strip()}
        r = deployer.db_select(site, table, columns=_cols, where=_where, limit=_limit)
        if not r.get("ok"):
            return HandlerResult.fail(
                f"❌ Aperçu indisponible : {r.get('message') or r.get('error') or 'erreur'}",
                handler_name="ionos_db_select",
            )
        logger.info("[IONOS DB READ] site={} op=select table={} rows={}", site, table, r.get("count", 0))
        cols = r.get("columns", [])
        rows = r.get("rows", [])
        warn = "⚠️ Table **sensible**. " if table.lower() in _IONOS_SENSITIVE_TABLES else ""
        head = f"**Aperçu `{table}` ({site})** — {r.get('count', 0)} ligne(s){' (tronqué)' if r.get('truncated') else ''}. {warn}"
        if not cols:
            return HandlerResult.ok(head + "\n_(aucune colonne)_", handler_name="ionos_db_select")
        out = [head, "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for row in rows:
            out.append("| " + " | ".join("" if v is None else str(v).replace("|", "\\|").replace("\n", " ") for v in row) + " |")
        return HandlerResult.ok("\n".join(out), handler_name="ionos_db_select")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_select")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur aperçu BDD: {e}", handler_name="ionos_db_select")


async def ionos_db_propose_write_handler(
    ctx: HandlerContext,
    site: str = "",
    table: str = "",
    op: str = "",
    values: str = "",
    where: str = "",
) -> HandlerResult:
    """PROPOSER une écriture INSERT/UPDATE sur la BDD IONOS (Étape 4.5A).

    N'EXÉCUTE RIEN : prépare une proposition structurée qui devra être confirmée
    par un humain dans l'interface. Aucun paramètre de confirmation n'est accepté
    ici — l'agent ne peut pas déclencher une écriture.

    `op` : insert | update. `values` : `col=valeur` séparés par des virgules.
    `where` : `col=valeur` (égalité simple, OBLIGATOIRE pour update). Aucun SQL libre.
    """
    try:
        deployer = _get_deployer()
        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        op = (op or "").strip().lower()
        if not site or not table or op not in ("insert", "update"):
            return HandlerResult.fail(
                "Paramètres requis : site, table, op=insert|update.",
                handler_name="ionos_db_propose_write",
            )

        def _parse_pairs(s: str) -> Dict[str, str]:
            out: Dict[str, str] = {}
            for part in (s or "").split(","):
                part = part.strip()
                if not part:
                    continue
                if "=" not in part:
                    raise ValueError(part)
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
            return out

        try:
            _values = _parse_pairs(values)
            _where = _parse_pairs(where) if where.strip() else None
        except ValueError:
            return HandlerResult.fail(
                "Format attendu : values='col=valeur, col2=valeur2' (idem where).",
                handler_name="ionos_db_propose_write",
            )
        if not _values:
            return HandlerResult.fail("Au moins une colonne à écrire (values).", handler_name="ionos_db_propose_write")

        r = deployer.propose_write(site, op, table, _values, where=_where, source="react")
        if not r.get("ok"):
            return HandlerResult.fail(
                f"❌ Proposition refusée : {r.get('message') or r.get('error') or 'erreur'}",
                handler_name="ionos_db_propose_write",
            )
        # Résumé NON sensible : on ne renvoie JAMAIS les valeurs au modèle.
        est = r.get("estimated_count")
        est_txt = f" (~{est} ligne(s) ciblée(s))" if est is not None else ""
        wk = ", ".join(r.get("where_keys") or []) or "—"
        vk = ", ".join(r.get("value_keys") or [])
        msg = (
            f"📝 **Proposition {r['op'].upper()} sur `{table}` ({site})** créée "
            f"(id `{r['proposal_id']}`).\n"
            f"- Colonnes : {vk}\n- WHERE : {wk}{est_txt}\n\n"
            "⚠️ **Non exécutée.** Elle doit être **confirmée par un humain** dans le panel "
            "(IONOS → BDD → Actions IA en attente). L'agent ne peut pas l'exécuter."
        )
        return HandlerResult.ok(msg, handler_name="ionos_db_propose_write")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_propose_write")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur proposition : {e}", handler_name="ionos_db_propose_write")


async def ionos_db_propose_delete_handler(
    ctx: HandlerContext,
    site: str = "",
    table: str = "",
    where: str = "",
) -> HandlerResult:
    """PROPOSER une suppression DELETE sur la BDD IONOS (Étape 4.5B).

    N'EXÉCUTE RIEN : crée une proposition que l'humain doit confirmer dans le panel.
    Aucun paramètre de confirmation n'est accepté ici. Désactivé par défaut
    (kill-switch global + flag site). WHERE obligatoire. Aucun SQL libre.

    `where` : `col=valeur` (égalité simple, OBLIGATOIRE — pas de suppression totale).
    """
    try:
        deployer = _get_deployer()
        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site or not table:
            return HandlerResult.fail(
                "Paramètres requis : site, table, where.",
                handler_name="ionos_db_propose_delete",
            )
        _where = {}
        for part in (where or "").split(","):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                return HandlerResult.fail(
                    "Format attendu : where='col=valeur, col2=valeur2'.",
                    handler_name="ionos_db_propose_delete",
                )
            k, v = part.split("=", 1)
            _where[k.strip()] = v.strip()
        if not _where:
            return HandlerResult.fail(
                "WHERE obligatoire (pas de suppression totale).",
                handler_name="ionos_db_propose_delete",
            )

        r = deployer.propose_delete(site, table, where=_where, source="react")
        if not r.get("ok"):
            return HandlerResult.fail(
                f"❌ Proposition DELETE refusée : {r.get('message') or r.get('error') or 'erreur'}",
                handler_name="ionos_db_propose_delete",
            )
        est = r.get("estimated_count")
        est_txt = f" (~{est} ligne(s) ciblée(s))" if est is not None else ""
        wk = ", ".join(r.get("where_keys") or []) or "—"
        msg = (
            f"🗑️ **Proposition DELETE sur `{table}` ({site})** créée "
            f"(id `{r['proposal_id']}`).\n"
            f"- WHERE : {wk}{est_txt}\n\n"
            "⚠️ **Non exécutée.** Suppression définitive — elle doit être **confirmée par un "
            "humain** dans le panel (IONOS → BDD → Actions IA en attente). Un snapshot sera "
            "capturé avant. L'agent ne peut pas l'exécuter."
        )
        return HandlerResult.ok(msg, handler_name="ionos_db_propose_delete")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_propose_delete")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur proposition DELETE : {e}", handler_name="ionos_db_propose_delete")


# ══════════════════════════════════════════════════════════════════════════
# Exposition ReAct complète mais encadrée des capacités BDD IONOS.
# Tous ces handlers appellent les méthodes EXISTANTES de IonosDeployer.
# Règles : zéro secret/valeur en sortie ; les set_* ne touchent QUE des flags ;
# create sandbox impose lumena_sandbox_* + confirm interne seulement si le flag
# sandbox est déjà ON ; write/delete restent propose-only (handlers dédiés).
# ══════════════════════════════════════════════════════════════════════════

def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "vrai", "oui", "on", "yes")


def _env_truthy(name: str, default: str = "0") -> bool:
    return _truthy(os.getenv(name, default))


def _resolve_site(site: str) -> str:
    return site or os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")


def _mask_mid(value: str, keep_start: int = 3, keep_end: int = 3) -> str:
    """Masque le milieu en gardant un repère (ex: dbu4924776 → dbu****776)."""
    v = str(value or "")
    if not v:
        return ""
    if len(v) <= keep_start + keep_end:
        return v[:1] + "****"
    return f"{v[:keep_start]}****{v[-keep_end:]}"


def _mask_host(host: str) -> str:
    """Masque le label d'hôte en gardant le domaine (db5020513717.hosting-data.io → db50****.hosting-data.io)."""
    h = str(host or "")
    if not h:
        return ""
    if "." not in h:
        return _mask_mid(h, 4, 0)
    label, domain = h.split(".", 1)
    masked = (label[:4] + "****") if len(label) > 4 else ((label[:1] + "****") if label else "")
    return f"{masked}.{domain}"


# ── SAFE / read-only ──────────────────────────────────────────────────────

async def ionos_db_get_config_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Config BDD d'un site IONOS : host/user masqués partiellement, base/port/moteur en clair. JAMAIS de mot de passe."""
    try:
        deployer = _get_deployer()
        site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_get_config")
        cfg = deployer.get_site_database(site)  # include_secret=False
        if not cfg:
            return HandlerResult.ok(f"Aucune BDD configurée pour {site}.", handler_name="ionos_db_get_config")
        out = (f"**BDD {site}** — host=`{_mask_host(cfg.get('host',''))}` port={cfg.get('port',3306)} "
               f"base=`{cfg.get('name','')}` user=`{_mask_mid(cfg.get('user',''))}` "
               f"moteur={cfg.get('engine','') or '?'} (host/user partiellement masqués, "
               f"aucun mot de passe affiché).")
        return HandlerResult.ok(out, handler_name="ionos_db_get_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_get_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_get_config")


async def ionos_db_bridge_status_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Statut du bridge BDD sécurisé (installé/version). Aucun secret."""
    try:
        deployer = _get_deployer()
        site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_bridge_status")
        st = await deployer.get_database_bridge_status(site)
        out = (f"**Bridge BDD {site}** — installé={st.get('installed', False)} "
               f"version={st.get('version', '?')}.")
        if not st.get("installed"):
            out += " Installe-le avec `ionos_db_install_bridge`."
        return HandlerResult.ok(out, handler_name="ionos_db_bridge_status")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_bridge_status")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_bridge_status")


def _fmt_flag_cfg(label: str, site: str, cfg: dict) -> str:
    extra = ""
    if "tables" in cfg:
        tabs = cfg.get("tables") or []
        extra = f" — tables=[{', '.join(tabs) if tabs else '∅'}]"
    return f"**{label} ({site})** : {'activé' if cfg.get('enabled') else 'désactivé'}{extra}."


async def ionos_db_get_write_config_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Config écriture (enabled + allowlist tables). Aucun secret."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_get_write_config")
        return HandlerResult.ok(_fmt_flag_cfg("Écriture", site, deployer.get_site_write_config(site)),
                                handler_name="ionos_db_get_write_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_get_write_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_get_write_config")


async def ionos_db_get_delete_config_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Config suppression (enabled + allowlist tables). Aucun secret."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_get_delete_config")
        return HandlerResult.ok(_fmt_flag_cfg("Suppression", site, deployer.get_site_delete_config(site)),
                                handler_name="ionos_db_get_delete_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_get_delete_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_get_delete_config")


async def ionos_db_get_sandbox_config_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Config création de tables sandbox (enabled). Aucun secret."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_get_sandbox_config")
        return HandlerResult.ok(_fmt_flag_cfg("Sandbox (CREATE TABLE)", site, deployer.get_site_sandbox_config(site)),
                                handler_name="ionos_db_get_sandbox_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_get_sandbox_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_get_sandbox_config")


async def ionos_db_get_restore_config_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Config restauration de snapshots (enabled). Aucun secret."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_get_restore_config")
        return HandlerResult.ok(_fmt_flag_cfg("Restauration", site, deployer.get_site_restore_config(site)),
                                handler_name="ionos_db_get_restore_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_get_restore_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_get_restore_config")


async def ionos_db_get_react_write_config_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Config propositions ReAct INSERT/UPDATE (enabled). Aucun secret."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_get_react_write_config")
        return HandlerResult.ok(_fmt_flag_cfg("Propositions IA write", site, deployer.get_site_react_write_config(site)),
                                handler_name="ionos_db_get_react_write_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_get_react_write_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_get_react_write_config")


async def ionos_db_get_react_delete_config_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Config propositions ReAct DELETE (enabled). Aucun secret."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_get_react_delete_config")
        return HandlerResult.ok(_fmt_flag_cfg("Propositions IA DELETE", site, deployer.get_site_react_delete_config(site)),
                                handler_name="ionos_db_get_react_delete_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_get_react_delete_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_get_react_delete_config")


async def ionos_db_list_snapshots_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Liste les snapshots (métadonnées NON sensibles : table, nb lignes/colonnes, dates). Aucune valeur."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_list_snapshots")
        snaps = deployer.list_snapshots(site).get("snapshots", [])
        if not snaps:
            return HandlerResult.ok(f"Aucun snapshot pour {site}.", handler_name="ionos_db_list_snapshots")
        lines = [f"**Snapshots {site}** ({len(snaps)}) :"]
        for s in snaps[:50]:
            lines.append(f"- `{s.get('id')}` · {s.get('op')} `{s.get('table')}` · "
                         f"{s.get('row_count', 0)} ligne(s) · créé {s.get('created_at','')}")
        return HandlerResult.ok("\n".join(lines), handler_name="ionos_db_list_snapshots")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_list_snapshots")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_list_snapshots")


async def ionos_db_list_pending_actions_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Liste les propositions IA en attente (métadonnées seules : op, table, clés). Aucune valeur."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_list_pending_actions")
        actions = deployer.list_pending_actions(site).get("actions", [])
        if not actions:
            return HandlerResult.ok(f"Aucune proposition en attente pour {site}.", handler_name="ionos_db_list_pending_actions")
        lines = [f"**Propositions en attente {site}** ({len(actions)}) — à confirmer par un humain :"]
        for a in actions[:50]:
            wk = ", ".join(a.get("where_keys") or []) or "—"
            vk = ", ".join(a.get("value_keys") or []) or "—"
            lines.append(f"- `{a.get('id')}` · {a.get('op')} `{a.get('table')}` · cols={vk} · WHERE={wk}")
        return HandlerResult.ok("\n".join(lines), handler_name="ionos_db_list_pending_actions")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_list_pending_actions")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_list_pending_actions")


# ── CONFIG / préparation (mutation de flags uniquement, jamais de données) ──

async def ionos_db_install_bridge_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Installer/réinstaller le bridge BDD sécurisé via SFTP. Aucun secret renvoyé."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_install_bridge")
        r = await deployer.install_database_bridge(site)
        if not r.get("ok"):
            return HandlerResult.fail(f"❌ Installation échouée : {r.get('message') or r.get('error') or 'erreur'}",
                                      handler_name="ionos_db_install_bridge")
        return HandlerResult.ok(f"✅ Bridge BDD installé sur {site} (version {r.get('version')}).",
                                handler_name="ionos_db_install_bridge")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_install_bridge")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_install_bridge")


def _parse_tables(tables: str) -> list:
    return [t.strip() for t in (tables or "").replace(";", ",").split(",") if t.strip()]


async def ionos_db_set_sandbox_config_handler(ctx: HandlerContext, site: str = "", enabled: str = "") -> HandlerResult:
    """Activer/désactiver la création de tables sandbox (flag seulement)."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_set_sandbox_config")
        r = deployer.set_site_sandbox_config(site, _truthy(enabled))
        return HandlerResult.ok(f"Sandbox {site} : {'activé' if r.get('enabled') else 'désactivé'}.",
                                handler_name="ionos_db_set_sandbox_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_set_sandbox_config")
    except ValueError as e:
        return HandlerResult.fail(str(e), handler_name="ionos_db_set_sandbox_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_set_sandbox_config")


async def ionos_db_set_write_config_handler(ctx: HandlerContext, site: str = "", enabled: str = "", tables: str = "") -> HandlerResult:
    """Activer/désactiver l'écriture + fixer l'allowlist des tables (flags seulement)."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_set_write_config")
        r = deployer.set_site_write_config(site, _truthy(enabled), _parse_tables(tables))
        return HandlerResult.ok(f"Écriture {site} : {'activé' if r.get('enabled') else 'désactivé'} — "
                                f"tables=[{', '.join(r.get('tables') or []) or '∅'}].",
                                handler_name="ionos_db_set_write_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_set_write_config")
    except ValueError as e:
        return HandlerResult.fail(str(e), handler_name="ionos_db_set_write_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_set_write_config")


async def ionos_db_set_delete_config_handler(ctx: HandlerContext, site: str = "", enabled: str = "", tables: str = "") -> HandlerResult:
    """Activer/désactiver la suppression + fixer l'allowlist DÉDIÉE (flags seulement)."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_set_delete_config")
        r = deployer.set_site_delete_config(site, _truthy(enabled), _parse_tables(tables))
        return HandlerResult.ok(f"Suppression {site} : {'activé' if r.get('enabled') else 'désactivé'} — "
                                f"tables=[{', '.join(r.get('tables') or []) or '∅'}].",
                                handler_name="ionos_db_set_delete_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_set_delete_config")
    except ValueError as e:
        return HandlerResult.fail(str(e), handler_name="ionos_db_set_delete_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_set_delete_config")


async def ionos_db_set_restore_config_handler(ctx: HandlerContext, site: str = "", enabled: str = "") -> HandlerResult:
    """Activer/désactiver la restauration de snapshots (flag seulement)."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_set_restore_config")
        r = deployer.set_site_restore_config(site, _truthy(enabled))
        return HandlerResult.ok(f"Restauration {site} : {'activé' if r.get('enabled') else 'désactivé'}.",
                                handler_name="ionos_db_set_restore_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_set_restore_config")
    except ValueError as e:
        return HandlerResult.fail(str(e), handler_name="ionos_db_set_restore_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_set_restore_config")


async def ionos_db_set_react_write_config_handler(ctx: HandlerContext, site: str = "", enabled: str = "") -> HandlerResult:
    """Activer/désactiver les propositions ReAct INSERT/UPDATE (flag seulement)."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_set_react_write_config")
        r = deployer.set_site_react_write_config(site, _truthy(enabled))
        return HandlerResult.ok(f"Propositions IA write {site} : {'activé' if r.get('enabled') else 'désactivé'}.",
                                handler_name="ionos_db_set_react_write_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_set_react_write_config")
    except ValueError as e:
        return HandlerResult.fail(str(e), handler_name="ionos_db_set_react_write_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_set_react_write_config")


async def ionos_db_set_react_delete_config_handler(ctx: HandlerContext, site: str = "", enabled: str = "") -> HandlerResult:
    """Activer/désactiver les propositions ReAct DELETE (flag seulement ; kill-switch global requis en plus)."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_set_react_delete_config")
        r = deployer.set_site_react_delete_config(site, _truthy(enabled))
        note = "" if not r.get("enabled") else " (nécessite aussi LUMENA_IONOS_REACT_DELETE_ENABLED=1)"
        return HandlerResult.ok(f"Propositions IA DELETE {site} : {'activé' if r.get('enabled') else 'désactivé'}{note}.",
                                handler_name="ionos_db_set_react_delete_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_set_react_delete_config")
    except ValueError as e:
        return HandlerResult.fail(str(e), handler_name="ionos_db_set_react_delete_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_set_react_delete_config")


# ── ACTION BDD encadrée : CREATE TABLE sandbox ─────────────────────────────

async def ionos_db_create_sandbox_table_handler(
    ctx: HandlerContext,
    site: str = "",
    name: str = "",
    columns: str = "",
) -> HandlerResult:
    """Créer une table sandbox de test sur la BDD IONOS via le bridge sécurisé.

    Préfixe `lumena_sandbox_` imposé (ajouté si absent). Nécessite que la création
    sandbox soit déjà activée (sinon message d'activation, JAMAIS de mysql/php/node).
    `columns` : 'nom:TYPE' ou 'nom:VARCHAR:longueur', séparés par des virgules.
    Une colonne `id` (PK auto) est ajoutée par le bridge. Aucun SQL libre.
    """
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site or not name:
            return HandlerResult.fail("Paramètres requis : site, name, columns.",
                                      handler_name="ionos_db_create_sandbox_table")
        # Flag d'abord : confirm interne seulement si sandbox ON, ou auto-sandbox
        # explicitement autorisée par config globale.
        sandbox_was_enabled = bool(deployer.get_site_sandbox_config(site).get("enabled"))
        auto_sandbox = _env_truthy("LUMENA_IONOS_AUTO_SANDBOX_CREATE_ENABLED", "0")
        if not sandbox_was_enabled and not auto_sandbox:
            return HandlerResult.fail(
                "⛔ La création de tables sandbox est désactivée pour ce site. "
                "Active-la d'abord avec `ionos_db_set_sandbox_config(site=..., enabled=true)` "
                "ou active `LUMENA_IONOS_AUTO_SANDBOX_CREATE_ENABLED` dans Configuration. "
                "N'utilise PAS mysql/php/node/config.php.",
                handler_name="ionos_db_create_sandbox_table",
            )
        if not sandbox_was_enabled and auto_sandbox:
            deployer.set_site_sandbox_config(site, True)
        # Préfixe imposé.
        if not name.startswith("lumena_sandbox_"):
            name = "lumena_sandbox_" + name.lstrip("_")
        # Parse colonnes : 'nom:TYPE' | 'nom:VARCHAR:len'.
        cols = []
        for part in (columns or "").split(","):
            part = part.strip()
            if not part:
                continue
            bits = [b.strip() for b in part.split(":")]
            if len(bits) < 2:
                return HandlerResult.fail(
                    "Format colonnes attendu : 'nom:TYPE' ou 'nom:VARCHAR:longueur', séparés par des virgules.",
                    handler_name="ionos_db_create_sandbox_table",
                )
            col = {"name": bits[0], "type": bits[1].upper()}
            if col["type"] == "VARCHAR":
                try:
                    col["length"] = int(bits[2]) if len(bits) > 2 else 255
                except ValueError:
                    return HandlerResult.fail("Longueur VARCHAR invalide.", handler_name="ionos_db_create_sandbox_table")
            cols.append(col)
        if not cols:
            return HandlerResult.fail("Au moins une colonne requise (ex: columns='label:VARCHAR:120, qty:INT').",
                                      handler_name="ionos_db_create_sandbox_table")
        # confirm=True posé en interne uniquement via flag site ON ou auto-sandbox globale.
        try:
            r = deployer.db_create_sandbox_table(site, name, cols, confirm=True, source="react")
        finally:
            if not sandbox_was_enabled and auto_sandbox:
                deployer.set_site_sandbox_config(site, False)
        if not r.get("ok"):
            return HandlerResult.fail(f"❌ Création refusée : {r.get('message') or r.get('error') or 'erreur'}",
                                      handler_name="ionos_db_create_sandbox_table")
        verb = "créée" if r.get("created") else "déjà existante"
        suffix = " Sandbox temporaire remise OFF." if (not sandbox_was_enabled and auto_sandbox) else ""
        return HandlerResult.ok(f"✅ Table sandbox `{r.get('table', name)}` {verb} sur {site}.{suffix}",
                                handler_name="ionos_db_create_sandbox_table")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_create_sandbox_table")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_create_sandbox_table")


# ── DROP sandbox encadré (Étape 4.6) : config + proposition (propose-only) ──

async def ionos_db_get_sandbox_drop_config_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Config DROP sandbox (enabled). Aucun secret."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_get_sandbox_drop_config")
        return HandlerResult.ok(_fmt_flag_cfg("DROP sandbox", site, deployer.get_site_sandbox_drop_config(site)),
                                handler_name="ionos_db_get_sandbox_drop_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_get_sandbox_drop_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_get_sandbox_drop_config")


async def ionos_db_set_sandbox_drop_config_handler(ctx: HandlerContext, site: str = "", enabled: str = "") -> HandlerResult:
    """Activer/désactiver le DROP de tables sandbox (flag seulement ; kill-switch global requis en plus)."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_set_sandbox_drop_config")
        r = deployer.set_site_sandbox_drop_config(site, _truthy(enabled))
        note = "" if not r.get("enabled") else " (nécessite aussi LUMENA_IONOS_SANDBOX_DROP_ENABLED=1)"
        return HandlerResult.ok(f"DROP sandbox {site} : {'activé' if r.get('enabled') else 'désactivé'}{note}.",
                                handler_name="ionos_db_set_sandbox_drop_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_set_sandbox_drop_config")
    except ValueError as e:
        return HandlerResult.fail(str(e), handler_name="ionos_db_set_sandbox_drop_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_set_sandbox_drop_config")


async def ionos_db_propose_drop_sandbox_table_handler(
    ctx: HandlerContext,
    site: str = "",
    table: str = "",
) -> HandlerResult:
    """PROPOSER la suppression (DROP) d'une table sandbox VIDE (Étape 4.6).

    N'EXÉCUTE RIEN : crée une proposition que l'humain doit confirmer dans le panel.
    Aucun paramètre de confirmation accepté. Désactivé par défaut (kill-switch global +
    flag site). Préfixe `lumena_sandbox_` imposé. La table doit être vide (vérifié à
    l'exécution). Aucun DROP générique, aucun SQL libre.
    """
    try:
        deployer = _get_deployer()
        site = _resolve_site(site)
        if not site or not table:
            return HandlerResult.fail("Paramètres requis : site, table (lumena_sandbox_*).",
                                      handler_name="ionos_db_propose_drop_sandbox_table")
        r = deployer.propose_drop_sandbox(site, table, source="react")
        if not r.get("ok"):
            return HandlerResult.fail(
                f"❌ Proposition DROP refusée : {r.get('message') or r.get('error') or 'erreur'}",
                handler_name="ionos_db_propose_drop_sandbox_table",
            )
        msg = (
            f"🗑️ **Proposition DROP de la table sandbox `{table}` ({site})** créée "
            f"(id `{r['proposal_id']}`).\n\n"
            "⚠️ **Non exécutée.** Suppression définitive de la table (si elle est vide). "
            "Elle doit être **confirmée par un humain** dans le panel (IONOS → BDD → Actions "
            "IA en attente), nom de table à retaper. L'agent ne peut pas l'exécuter."
        )
        return HandlerResult.ok(msg, handler_name="ionos_db_propose_drop_sandbox_table")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_propose_drop_sandbox_table")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur proposition DROP : {e}", handler_name="ionos_db_propose_drop_sandbox_table")


# ── CLEAR sandbox encadré (Étape 4.7) : config + proposition (propose-only) ──

async def ionos_db_get_sandbox_clear_config_handler(ctx: HandlerContext, site: str = "") -> HandlerResult:
    """Config CLEAR (vidage) sandbox (enabled). Aucun secret."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_get_sandbox_clear_config")
        return HandlerResult.ok(_fmt_flag_cfg("CLEAR sandbox", site, deployer.get_site_sandbox_clear_config(site)),
                                handler_name="ionos_db_get_sandbox_clear_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_get_sandbox_clear_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_get_sandbox_clear_config")


async def ionos_db_set_sandbox_clear_config_handler(ctx: HandlerContext, site: str = "", enabled: str = "") -> HandlerResult:
    """Activer/désactiver le vidage de tables sandbox (flag seulement)."""
    try:
        deployer = _get_deployer(); site = _resolve_site(site)
        if not site:
            return HandlerResult.fail("Paramètre 'site' requis.", handler_name="ionos_db_set_sandbox_clear_config")
        r = deployer.set_site_sandbox_clear_config(site, _truthy(enabled))
        return HandlerResult.ok(f"Vidage sandbox {site} : {'activé' if r.get('enabled') else 'désactivé'}.",
                                handler_name="ionos_db_set_sandbox_clear_config")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_set_sandbox_clear_config")
    except ValueError as e:
        return HandlerResult.fail(str(e), handler_name="ionos_db_set_sandbox_clear_config")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="ionos_db_set_sandbox_clear_config")


async def ionos_db_propose_clear_sandbox_table_handler(
    ctx: HandlerContext,
    site: str = "",
    table: str = "",
) -> HandlerResult:
    """PROPOSER le vidage (suppression de toutes les lignes) d'une table sandbox (Étape 4.7).

    N'EXÉCUTE RIEN : crée une proposition que l'humain doit confirmer dans le panel
    (nom de table à retaper). À utiliser pour « vide la table lumena_sandbox_* » au lieu
    d'un DELETE générique. Préfixe imposé ; table déjà vide → rien à faire. Un snapshot
    est capturé avant vidage (restaurable). Aucun SQL libre, aucune confirmation côté agent.
    """
    try:
        deployer = _get_deployer()
        site = _resolve_site(site)
        if not site or not table:
            return HandlerResult.fail("Paramètres requis : site, table (lumena_sandbox_*).",
                                      handler_name="ionos_db_propose_clear_sandbox_table")
        r = deployer.propose_clear_sandbox(site, table, source="react")
        if r.get("status") == "already_empty":
            return HandlerResult.ok(f"La table `{table}` est déjà vide — rien à faire.",
                                    handler_name="ionos_db_propose_clear_sandbox_table")
        if not r.get("ok"):
            return HandlerResult.fail(
                f"❌ Proposition de vidage refusée : {r.get('message') or r.get('error') or 'erreur'}",
                handler_name="ionos_db_propose_clear_sandbox_table",
            )
        cnt = r.get("estimated_count")
        cnt_txt = f" (~{cnt} ligne(s))" if cnt is not None else ""
        msg = (
            f"🧹 **Proposition de VIDAGE de la table sandbox `{table}` ({site})**{cnt_txt} créée "
            f"(id `{r['proposal_id']}`).\n\n"
            "⚠️ **Non exécutée.** Supprime toutes les lignes (un snapshot est capturé avant, "
            "donc restaurable). À **confirmer par un humain** dans le panel (nom à retaper). "
            "L'agent ne peut pas l'exécuter."
        )
        return HandlerResult.ok(msg, handler_name="ionos_db_propose_clear_sandbox_table")
    except KeyError:
        return HandlerResult.fail(f"Site '{site}' introuvable.", handler_name="ionos_db_propose_clear_sandbox_table")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur proposition vidage : {e}", handler_name="ionos_db_propose_clear_sandbox_table")


# ── Handler definitions ──────────────────────────────────────────────────

def get_ionos_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions de handlers IONOS pour le registre V2."""
    return [
        HandlerDef(
            name="deploy_to_ionos",
            description=(
                "Déployer/publier un projet web complet sur un hébergement IONOS via SFTP. "
                "Uploade tous les fichiers du projet sur le serveur distant."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Domaine du site IONOS cible (ex: lumena.fr). Vide = site par défaut.",
                    },
                    "project_dir": {
                        "type": "string",
                        "description": "Chemin du dossier projet local à déployer. Vide = dernier projet.",
                    },
                    "dry_run": {
                        "type": "string",
                        "description": "true = simuler sans uploader. false = déployer réellement.",
                    },
                },
                "required": [],
            },
            handler=deploy_to_ionos_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="update_ionos_files",
            description=(
                "Mettre à jour des fichiers spécifiques sur un site IONOS déjà déployé."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Domaine du site IONOS cible.",
                    },
                    "files": {
                        "type": "string",
                        "description": "Chemins locaux des fichiers à uploader, séparés par des virgules.",
                    },
                },
                "required": ["files"],
            },
            handler=update_ionos_files_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_add_site",
            description=(
                "Ajouter un nouveau site IONOS avec les credentials SFTP. "
                "Teste la connexion avant de sauvegarder."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Nom de domaine du site (ex: lumena.fr).",
                    },
                    "host": {
                        "type": "string",
                        "description": "Hostname SFTP (ex: access123456789.webspace-data.io).",
                    },
                    "user": {
                        "type": "string",
                        "description": "Username SFTP.",
                    },
                    "password": {
                        "type": "string",
                        "description": "Mot de passe SFTP.",
                    },
                    "port": {
                        "type": "string",
                        "description": "Port SFTP (défaut: 22).",
                    },
                    "root": {
                        "type": "string",
                        "description": "Dossier racine distant (défaut: /).",
                    },
                    "label": {
                        "type": "string",
                        "description": "Label descriptif du site.",
                    },
                },
                "required": ["domain", "host", "user", "password"],
            },
            handler=ionos_add_site_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_remove_site",
            description="Supprimer un site IONOS de la configuration Lumena.",
            parameters={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Domaine du site à supprimer.",
                    },
                },
                "required": ["domain"],
            },
            handler=ionos_remove_site_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_list_sites",
            description="Lister tous les sites IONOS configurés dans Lumena.",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=ionos_list_sites_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_list_files",
            description="Lister les fichiers et dossiers présents sur un site IONOS distant.",
            parameters={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Domaine du site IONOS.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Chemin distant à lister (défaut: /).",
                    },
                },
                "required": [],
            },
            handler=ionos_list_files_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_delete_files",
            description="Supprimer des fichiers sur un site IONOS distant via SFTP.",
            parameters={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Domaine du site IONOS.",
                    },
                    "paths": {
                        "type": "string",
                        "description": "Chemins distants à supprimer, séparés par des virgules.",
                    },
                },
                "required": ["paths"],
            },
            handler=ionos_delete_files_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_test_site_database",
            description=(
                "Tester la connexion à la base de données associée à un site IONOS. "
                "Lecture seule : effectue un PING de connexion uniquement, ne lit ni "
                "ne modifie aucune donnée. N'affiche jamais le mot de passe."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Domaine du site IONOS dont tester la BDD.",
                    },
                },
                "required": [],
            },
            handler=ionos_test_site_database_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_set_site_database",
            description=(
                "Associer ou modifier la base de données d'un site IONOS "
                "(host, port, nom, user, mot de passe, moteur). Le mot de passe "
                "est chiffré et jamais réaffiché ; vide en modification = conserver "
                "l'ancien. N'établit aucune connexion (utiliser ionos_test_site_database)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Domaine du site IONOS."},
                    "host": {"type": "string", "description": "Hôte BDD (ex: dbXXXX.hosting-data.io)."},
                    "name": {"type": "string", "description": "Nom de la base."},
                    "user": {"type": "string", "description": "Utilisateur BDD."},
                    "password": {"type": "string", "description": "Mot de passe BDD (vide = conserver l'existant)."},
                    "port": {"type": "string", "description": "Port BDD (défaut 3306)."},
                    "label": {"type": "string", "description": "Libellé optionnel."},
                    "description": {"type": "string", "description": "Description optionnelle."},
                    "engine": {"type": "string", "description": "Moteur (mariadb/mysql, défaut mariadb)."},
                    "version": {"type": "string", "description": "Version optionnelle."},
                },
                "required": ["site", "host", "name", "user"],
            },
            handler=ionos_set_site_database_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_clear_site_database",
            description=(
                "Retirer la base de données associée à un site IONOS. "
                "Ne supprime pas le site SFTP, seulement la config BDD."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Domaine du site IONOS."},
                },
                "required": ["site"],
            },
            handler=ionos_clear_site_database_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_list_tables",
            description=(
                "Lister les tables de la base de données d'un site IONOS "
                "(lecture seule, via le bridge sécurisé). Aucune écriture."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Domaine du site IONOS."},
                },
                "required": [],
            },
            handler=ionos_db_list_tables_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_describe_table",
            description=(
                "Décrire la STRUCTURE (colonnes/types) d'une table de la BDD IONOS "
                "(lecture seule, via le bridge). NE renseigne PAS le contenu : n'en déduis "
                "jamais si la table est vide ou contient des lignes. Pour le nombre de lignes "
                "(avant DROP/CLEAR), utilise ionos_db_select. Aucune écriture."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Domaine du site IONOS."},
                    "table": {"type": "string", "description": "Nom de la table."},
                },
                "required": ["table"],
            },
            handler=ionos_db_describe_table_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_select",
            description=(
                "Aperçu read-only borné d'une table de la BDD IONOS (SELECT structuré "
                "via le bridge). columns=liste séparée par virgules (optionnel), "
                "where=col=valeur (égalité simple, optionnel), limit défaut 20 max 100. "
                "Aucune écriture, aucun SQL libre."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Domaine du site IONOS."},
                    "table": {"type": "string", "description": "Nom de la table."},
                    "columns": {"type": "string", "description": "Colonnes séparées par des virgules (optionnel)."},
                    "where": {"type": "string", "description": "Filtre égalité simple col=valeur (optionnel)."},
                    "limit": {"type": "string", "description": "Nombre de lignes (défaut 20, max 100)."},
                },
                "required": ["table"],
            },
            handler=ionos_db_select_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_propose_write",
            description=(
                "PROPOSER une écriture INSERT/UPDATE sur la BDD d'un site IONOS "
                "(Étape 4.5A). N'EXÉCUTE RIEN : crée une proposition que l'humain doit "
                "confirmer dans le panel. op=insert|update, values='col=valeur,...', "
                "where='col=valeur' (obligatoire pour update). Aucun SQL libre, aucune "
                "suppression, aucune confirmation côté agent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Domaine du site IONOS."},
                    "table": {"type": "string", "description": "Nom de la table (doit être allowlistée en écriture)."},
                    "op": {"type": "string", "description": "insert ou update."},
                    "values": {"type": "string", "description": "Colonnes à écrire : 'col=valeur, col2=valeur2'."},
                    "where": {"type": "string", "description": "Filtre égalité simple col=valeur (OBLIGATOIRE pour update)."},
                },
                "required": ["table", "op", "values"],
            },
            handler=ionos_db_propose_write_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_propose_delete",
            description=(
                "PROPOSER une suppression DELETE sur la BDD d'un site IONOS "
                "(Étape 4.5B). N'EXÉCUTE RIEN : crée une proposition que l'humain doit "
                "confirmer dans le panel. Désactivé par défaut (kill-switch global + flag "
                "site). where='col=valeur' OBLIGATOIRE (pas de suppression totale). "
                "Aucun SQL libre, aucune confirmation côté agent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Domaine du site IONOS."},
                    "table": {"type": "string", "description": "Nom de la table (doit être allowlistée en suppression)."},
                    "where": {"type": "string", "description": "Filtre égalité simple col=valeur (OBLIGATOIRE)."},
                },
                "required": ["table", "where"],
            },
            handler=ionos_db_propose_delete_handler,
            category="ionos",
            source_module="ionos",
        ),
        # ── SAFE / read-only ──────────────────────────────────────────────
        HandlerDef(
            name="ionos_db_get_config",
            description="Config BDD non sensible d'un site IONOS (host/port/base/user/moteur). JAMAIS de mot de passe.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_get_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_bridge_status",
            description="Statut du bridge BDD sécurisé d'un site IONOS (installé/version). Aucun secret.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_bridge_status_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_get_write_config",
            description="Lire la config écriture BDD IONOS (activé + tables autorisées). Aucun secret.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_get_write_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_get_delete_config",
            description="Lire la config suppression BDD IONOS (activé + tables autorisées). Aucun secret.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_get_delete_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_get_sandbox_config",
            description="Lire la config de création de tables sandbox BDD IONOS (activé). Aucun secret.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_get_sandbox_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_get_restore_config",
            description="Lire la config de restauration de snapshots BDD IONOS (activé). Aucun secret.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_get_restore_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_get_react_write_config",
            description="Lire la config des propositions IA INSERT/UPDATE BDD IONOS (activé). Aucun secret.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_get_react_write_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_get_react_delete_config",
            description="Lire la config des propositions IA DELETE BDD IONOS (activé). Aucun secret.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_get_react_delete_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_list_snapshots",
            description="Lister les snapshots BDD IONOS (métadonnées : table, nb lignes/colonnes, dates). Aucune valeur, aucun secret.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_list_snapshots_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_list_pending_actions",
            description="Lister les propositions IA en attente d'approbation humaine (métadonnées seules : op, table, clés). Aucune valeur.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_list_pending_actions_handler, category="ionos", source_module="ionos",
        ),
        # ── CONFIG / préparation (flags seulement) ────────────────────────
        HandlerDef(
            name="ionos_db_install_bridge",
            description="Installer/réinstaller le bridge BDD sécurisé sur un site IONOS via SFTP. Pré-requis pour toute opération BDD. Aucun secret renvoyé.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_install_bridge_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_set_sandbox_config",
            description="Activer/désactiver la création de tables sandbox BDD IONOS (flag uniquement, n'écrit aucune donnée).",
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "enabled": {"type": "string", "description": "true/false."}}, "required": ["enabled"]},
            handler=ionos_db_set_sandbox_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_set_write_config",
            description="Activer/désactiver l'écriture BDD IONOS et fixer l'allowlist des tables (flags uniquement).",
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "enabled": {"type": "string", "description": "true/false."},
                "tables": {"type": "string", "description": "Tables autorisées séparées par des virgules."}}, "required": ["enabled"]},
            handler=ionos_db_set_write_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_set_delete_config",
            description="Activer/désactiver la suppression BDD IONOS et fixer l'allowlist DÉDIÉE (flags uniquement).",
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "enabled": {"type": "string", "description": "true/false."},
                "tables": {"type": "string", "description": "Tables autorisées séparées par des virgules."}}, "required": ["enabled"]},
            handler=ionos_db_set_delete_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_set_restore_config",
            description="Activer/désactiver la restauration de snapshots BDD IONOS (flag uniquement).",
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "enabled": {"type": "string", "description": "true/false."}}, "required": ["enabled"]},
            handler=ionos_db_set_restore_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_set_react_write_config",
            description="Activer/désactiver les propositions IA INSERT/UPDATE BDD IONOS (flag uniquement).",
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "enabled": {"type": "string", "description": "true/false."}}, "required": ["enabled"]},
            handler=ionos_db_set_react_write_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_set_react_delete_config",
            description="Activer/désactiver les propositions IA DELETE BDD IONOS (flag uniquement ; kill-switch global requis en plus).",
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "enabled": {"type": "string", "description": "true/false."}}, "required": ["enabled"]},
            handler=ionos_db_set_react_delete_config_handler, category="ionos", source_module="ionos",
        ),
        # ── ACTION BDD encadrée ───────────────────────────────────────────
        HandlerDef(
            name="ionos_db_create_sandbox_table",
            description=(
                "Créer une table de test sur la BDD d'un site IONOS via le bridge sécurisé. "
                "À UTILISER pour toute demande de création de table BDD IONOS (ex: 'rajoute une "
                "table test à la bdd') — NE PAS utiliser mysql/php/node/config.php. Préfixe "
                "lumena_sandbox_ imposé (ajouté si absent). Nécessite la sandbox déjà activée. "
                "columns='nom:TYPE' ou 'nom:VARCHAR:longueur' séparés par des virgules. "
                "Une colonne id (PK auto) est ajoutée. Aucun SQL libre, aucun DROP/ALTER."
            ),
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "name": {"type": "string", "description": "Nom de la table (préfixe lumena_sandbox_ ajouté si absent)."},
                "columns": {"type": "string", "description": "Colonnes : 'label:VARCHAR:120, qty:INT'."}},
                "required": ["name", "columns"]},
            handler=ionos_db_create_sandbox_table_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_get_sandbox_drop_config",
            description="Lire la config DROP de tables sandbox BDD IONOS (activé). Aucun secret.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_get_sandbox_drop_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_set_sandbox_drop_config",
            description="Activer/désactiver le DROP de tables sandbox BDD IONOS (flag uniquement ; kill-switch global requis en plus).",
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "enabled": {"type": "string", "description": "true/false."}}, "required": ["enabled"]},
            handler=ionos_db_set_sandbox_drop_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_propose_drop_sandbox_table",
            description=(
                "PROPOSER la suppression (DROP) d'une table sandbox VIDE sur la BDD IONOS "
                "(Étape 4.6). N'EXÉCUTE RIEN : crée une proposition que l'humain doit confirmer "
                "dans le panel (nom de table à retaper). Désactivé par défaut (kill-switch global "
                "+ flag site). Préfixe lumena_sandbox_ imposé ; table non vide refusée. "
                "Aucun DROP générique, aucun SQL libre, jamais users/sessions/tables métier."
            ),
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "table": {"type": "string", "description": "Table sandbox à supprimer (lumena_sandbox_*)."}},
                "required": ["table"]},
            handler=ionos_db_propose_drop_sandbox_table_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_get_sandbox_clear_config",
            description="Lire la config CLEAR (vidage) de tables sandbox BDD IONOS (activé). Aucun secret.",
            parameters={"type": "object", "properties": {"site": {"type": "string", "description": "Domaine du site IONOS."}}, "required": []},
            handler=ionos_db_get_sandbox_clear_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_set_sandbox_clear_config",
            description="Activer/désactiver le vidage de tables sandbox BDD IONOS (flag uniquement).",
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "enabled": {"type": "string", "description": "true/false."}}, "required": ["enabled"]},
            handler=ionos_db_set_sandbox_clear_config_handler, category="ionos", source_module="ionos",
        ),
        HandlerDef(
            name="ionos_db_propose_clear_sandbox_table",
            description=(
                "PROPOSER le VIDAGE (suppression de toutes les lignes) d'une table sandbox "
                "lumena_sandbox_* sur la BDD IONOS (Étape 4.7). À UTILISER pour 'vide la table "
                "lumena_sandbox_*' — NE PAS utiliser ionos_db_propose_delete avec id>0. "
                "N'EXÉCUTE RIEN : proposition à confirmer par un humain (nom à retaper). "
                "Snapshot capturé avant (restaurable). Aucun SQL libre, aucune confirmation agent."
            ),
            parameters={"type": "object", "properties": {
                "site": {"type": "string", "description": "Domaine du site IONOS."},
                "table": {"type": "string", "description": "Table sandbox à vider (lumena_sandbox_*)."}},
                "required": ["table"]},
            handler=ionos_db_propose_clear_sandbox_table_handler, category="ionos", source_module="ionos",
        ),
    ]
