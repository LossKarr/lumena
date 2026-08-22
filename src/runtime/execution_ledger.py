"""
ExecutionLedger — Source unique de vérité d'exécution pour LUMENA.

Journal append-only des actions réellement exécutées par la boucle ReAct.
Survit à la compaction de contexte (il ne fait pas partie du prompt LLM).

V1 : in-memory, API minimale, intégré dans ReActLoop.

Objectif : permettre à Lumena de *prouver* ce qu'elle a fait,
au lieu de seulement *croire* l'avoir fait.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from time import perf_counter
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class LedgerEntry:
    """Une action vérifiée dans le journal d'exécution."""

    iteration: int
    action: str  # nom de l'outil ("write_file", "discord_send", …)
    target: Optional[str]  # fichier, canal, URL… dépend de l'outil
    success: bool
    proof: Optional[str]  # hash, extrait, assertion — None si non disponible
    timestamp: float  # perf_counter() au moment de l'enregistrement
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Outils considérés comme des mutations (actions vérifiables) ──────────────
MUTATION_TOOLS: frozenset[str] = frozenset({
    # ── Fichiers & code ──────────────────────────────────────────────────────
    "write_file", "edit_file", "apply_patch", "apply_patches", "create_file",
    "delete_file", "create_directory", "delete_directory",
    "insert_at_anchor", "edit_by_lines", "str_replace", "multi_edit_file",
    # ── Shell / exécution ────────────────────────────────────────────────────
    "run_command", "run_shell", "exec_command",
    # ── Web & projets ────────────────────────────────────────────────────────
    "generate_website", "serve_website", "edit_website", "write_website_files",
    "create_project", "create_skill", "update_skill", "delete_skill",
    # ── Documents ────────────────────────────────────────────────────────────
    "create_pdf", "create_docx", "create_pptx", "create_xlsx", "create_csv",
    "create_invoice_pdf", "create_from_template", "generate_studio_document",
    "generate_studio_documents",
    "create_html", "create_markdown", "create_email_html",
    "create_ics", "create_vcard", "create_meeting_report",
    "create_zip", "create_batch_documents",
    "edit_docx", "edit_pptx", "edit_xlsx",
    "annotate_pdf", "add_watermark", "assemble_document", "convert_document",
    # ── Images & vidéos ──────────────────────────────────────────────────────
    "generate_image", "edit_image", "generate_thumbnail", "generate_thumbnail_pro",
    "generate_logo", "generate_svg", "upscale_image",
    "remove_background", "replace_background", "sketch_to_image", "compose_image",
    "generate_video", "edit_video",
    # ── Discord ──────────────────────────────────────────────────────────────
    "discord_send", "discord_send_message", "discord_send_embed",
    "discord_create_channel", "discord_create_category", "discord_create_invite",
    "discord_create_role", "discord_delete_channel", "discord_delete_message",
    "discord_delete_role", "discord_modify_channel", "discord_pin", "discord_unpin",
    "discord_assign_role", "discord_remove_role",
    "discord_ban", "discord_unban", "discord_kick",
    "discord_set_channel_permissions", "discord_server_configure",
    # ── Messagerie ───────────────────────────────────────────────────────────
    "telegram_send_message", "telegram_send_document",
    "mail_send", "send_email", "mail_reply_message", "mail_delete_message",
    "mail_move_message", "mail_remove_account", "mail_account_upsert",
    "send_critical_sms",
    "send_whatsapp_message", "send_whatsapp_document",
    "send_whatsapp_photo", "send_whatsapp_audio", "send_message",
    # ── Twitter / X ──────────────────────────────────────────────────────────
    "twitter_post_tweet", "twitter_reply", "twitter_like", "twitter_compose_thread",
    # ── Stripe ───────────────────────────────────────────────────────────────
    "stripe_create_product", "stripe_update_product", "stripe_delete_product",
    "stripe_create_price", "stripe_create_payment_link", "stripe_update_payment_link",
    "stripe_create_customer", "stripe_update_customer",
    "stripe_create_subscription", "stripe_cancel_subscription",
    "stripe_create_invoice", "stripe_send_invoice", "stripe_void_invoice",
    "stripe_add_invoice_item", "stripe_create_checkout_session",
    "stripe_create_coupon", "stripe_delete_coupon", "stripe_create_refund",
    # ── GitHub / Git ─────────────────────────────────────────────────────────
    "github_repo_create", "github_file_write", "github_push_directory",
    "git_add", "git_commit", "git_push_pull", "git_init",
    # ── Notion ───────────────────────────────────────────────────────────────
    "notion_create_page", "notion_update_page", "notion_add_to_database",
    # ── Ionos / hébergement ──────────────────────────────────────────────────
    "deploy_to_ionos", "update_ionos_files",
    # ── Mémoire & tâches ─────────────────────────────────────────────────────
    "memory_save", "memory_store", "memory_add",
    "schedule_task", "create_task",
    "delegate_task", "delegate_task_bg",
})


def _extract_target(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """Extrait la cible principale d'une action depuis ses arguments.

    Retourne un chemin, un nom de canal, une URL… selon l'outil.
    Retourne None si rien d'exploitable.
    """
    if not isinstance(args, dict):
        return None

    # Fichiers
    for key in ("path", "file_path", "target", "destination", "filepath"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # apply_patches : premier fichier du batch comme cible représentative.
    # Suffisant pour has_any_mutation() et has_mutation_for_target_hint().
    # Pour un batch multi-fichiers, seule la première entrée est indexée ici.
    if tool_name == "apply_patches":
        patches = args.get("patches")
        if isinstance(patches, list) and patches:
            first = patches[0]
            if isinstance(first, dict):
                f = first.get("file")
                if isinstance(f, str) and f.strip():
                    return f.strip()

    # Canal Discord/Telegram
    for key in ("channel_name", "channel_id", "chat_id"):
        val = args.get(key)
        if val is not None:
            return str(val).strip()

    # Mail
    for key in ("to", "recipient", "email"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # Commandes shell : cwd en priorité
    if tool_name in ("run_command", "run_shell", "exec_command"):
        cwd = args.get("cwd") or args.get("working_dir")
        if isinstance(cwd, str) and cwd.strip():
            return cwd.strip()

    # delegate_task : description courte comme cible
    if tool_name in ("delegate_task", "delegate_task_bg"):
        desc = args.get("description", "")
        if isinstance(desc, str) and desc.strip():
            return desc.strip()[:120]

    # Skills : la cible est le nom du skill (args `name`/`skill_name`), pas un path.
    # Scopé aux outils skills → aucun impact sur la cible des autres outils.
    if tool_name in ("create_skill", "update_skill", "delete_skill"):
        nm = args.get("name") or args.get("skill_name")
        if isinstance(nm, str) and nm.strip():
            return nm.strip()

    return None


# ── Familles d'outils attendues par intent (pour le FINAL guard affiné) ──────
# Utilisées pour vérifier que les mutations réellement exécutées sont pertinentes
# pour l'intent courant. Conservateur : uniquement les intents où on peut inférer
# une famille de mutation de façon robuste.
INTENT_TO_MUTATION_FAMILY: Dict[str, frozenset] = {
    "discord": frozenset({
        "discord_send", "discord_send_message", "discord_send_embed",
        "discord_create_channel", "discord_create_category",
        "discord_modify_channel", "discord_delete_channel",
        "discord_create_role", "discord_delete_role",
        "discord_assign_role", "discord_remove_role",
        "discord_set_channel_permissions",
        "discord_kick", "discord_ban", "discord_unban",
    }),
    "code_edit": frozenset({
        "write_file", "edit_file", "apply_patch", "apply_patches", "create_file",
        "insert_at_anchor", "edit_by_lines", "str_replace", "multi_edit_file",
    }),
    "create_project": frozenset({
        "create_project", "write_file", "create_file", "create_directory",
        "delegate_task", "delegate_task_bg",
    }),
    "file_ops": frozenset({
        "write_file", "edit_file", "create_file", "delete_file",
        "create_directory", "delete_directory",
    }),
}


# LOT Z28 — un chemin dans du texte : entre backticks, absolu (C:\… ou /…),
# ou relatif au workspace. Sert à récupérer le dossier d'un livrable quand
# l'outil ne l'expose pas dans ses arguments.
_PATH_IN_TEXT_RE = re.compile(
    r"`([^`\n]{3,})`"
    r"|([A-Za-z]:[\\/][^\s`'\"]{3,})"
    r"|((?:^|\s)workspace[\\/][^\s`'\"]{3,})"
)


def _extract_proof(tool_name: str, observation_text: str, success: bool) -> Optional[str]:
    """Extrait une preuve simple depuis l'observation d'un outil.

    V1 : très conservateur — ne retourne quelque chose que quand c'est fiable.
    """
    if not success or not observation_text:
        return None

    text = observation_text.strip()

    # Fichier écrit/modifié : première ligne significative de l'observation
    if tool_name in ("write_file", "edit_file", "create_file", "apply_patch", "apply_patches",
                      "insert_at_anchor", "edit_by_lines", "str_replace",
                      "multi_edit_file"):
        # Les observations de write contiennent souvent "✅ Fichier écrit: <path> (N lignes)"
        for line in text.split("\n")[:3]:
            line = line.strip()
            if line and ("✅" in line or "écrit" in line.lower() or "modifié" in line.lower()
                         or "lignes" in line.lower() or "written" in line.lower()):
                return line[:200]
        return text[:200] if len(text) < 300 else None

    # Discord/Telegram/mail : confirmation d'envoi
    if tool_name in ("discord_send", "discord_send_message", "telegram_send_message",
                      "mail_send", "send_email"):
        for line in text.split("\n")[:3]:
            if "✅" in line or "envoyé" in line.lower() or "sent" in line.lower():
                return line[:200]
        return None

    # LOT Z28 — les outils qui produisent un DOSSIER entier n'avaient ni cible
    # ni preuve : `create_project` reçoit `description`/`project_name`, jamais un
    # `path`, donc `_extract_target` renvoie None. Résultat mesuré sur le run
    # « Papier Cousu » (2026-08-19) : le lead avait créé 6 fichiers, et RIEN au
    # ledger ne permettait de retrouver où. L'observation, elle, contient le
    # chemin absolu (« Projet créé via CodeAgent dans `C:\\…\\papier-cousu` ») —
    # le fait était affiché puis jeté. On le range.
    if tool_name in ("create_project", "generate_website", "create_website",
                      "write_website_files", "publish_mission_workspace"):
        for line in text.split("\n")[:4]:
            line = line.strip()
            if line and _PATH_IN_TEXT_RE.search(line):
                return line[:200]
        return None

    # Pas de preuve fiable pour les autres outils en V1
    return None


class ExecutionLedger:
    """Journal append-only des actions exécutées.

    Thread-safe : non (utilisé dans une seule boucle async à la fois).
    Persistance : non en V1 (in-memory, durée = une invocation de ReActLoop.run()).
    """

    def __init__(self) -> None:
        self._entries: List[LedgerEntry] = []

    # ── Écriture ─────────────────────────────────────────────────────────────

    def append(
        self,
        *,
        iteration: int,
        action: str,
        target: Optional[str] = None,
        success: bool,
        proof: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> LedgerEntry:
        """Ajoute une entrée au journal. Retourne l'entrée créée."""
        entry = LedgerEntry(
            iteration=iteration,
            action=action,
            target=target,
            success=success,
            proof=proof,
            timestamp=perf_counter(),
            meta=dict(meta or {}),
        )
        self._entries.append(entry)
        return entry

    # ── Lecture ───────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._entries)

    def recent(self, n: int = 10) -> List[LedgerEntry]:
        """Retourne les N dernières entrées (plus récentes en dernier)."""
        return list(self._entries[-max(1, n):])

    def latest_for_target(self, target: str) -> Optional[LedgerEntry]:
        """Retourne la dernière entrée concernant une cible donnée."""
        target_lower = target.lower()
        for entry in reversed(self._entries):
            if entry.target and entry.target.lower() == target_lower:
                return entry
        return None

    def has_successful_action(self, action: str) -> bool:
        """True si au moins une exécution réussie de cet outil existe."""
        return any(e.action == action and e.success for e in self._entries)

    # ── Preuve d'exécution de tests (VERROU DE VÉRITÉ mission) ────────────────
    # La méta `test_outcome` est posée à l'append par react.py (parseur pur
    # test_proof.parse_test_outcome) pour les commandes de test. Voir run
    # bibliotech 2026-07-01 : sans ce signal, une mission pouvait clamer
    # « tests verts » alors que le dernier pytest réel échouait.

    def has_green_test_run(self) -> bool:
        """True SSI un run de tests VERT probant a réellement eu lieu.

        « Vert probant » = `test_outcome.green` (≥1 passed, 0 failed, 0 error,
        pas d'erreur de collecte, exit 0, SANS `--ignore` inventé).
        """
        return any(
            e.success and bool((e.meta or {}).get("test_outcome", {}).get("green"))
            for e in self._entries
        )

    @staticmethod
    def _is_source_mutation(entry: LedgerEntry) -> bool:
        if not entry.success or entry.action not in {
            "write_file", "edit_file", "create_file", "apply_patch", "apply_patches",
            "insert_at_anchor", "edit_by_lines", "str_replace", "multi_edit_file",
            "write_website_files", "edit_website",
        }:
            return False
        target = str(entry.target or "").lower().split("?", 1)[0]
        return target.endswith((
            ".py", ".js", ".mjs", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css",
        ))

    def has_source_mutation(self) -> bool:
        return any(self._is_source_mutation(entry) for entry in self._entries)

    def _latest_source_mutation_timestamp(self) -> float:
        stamps = [e.timestamp for e in self._entries if self._is_source_mutation(e)]
        return max(stamps, default=-1.0)

    def has_fresh_green_test_run(self) -> bool:
        """True when a green test run happened after the latest source mutation."""
        last_mutation = self._latest_source_mutation_timestamp()
        return any(
            e.timestamp > last_mutation
            and e.success
            and bool((e.meta or {}).get("test_outcome", {}).get("green"))
            for e in self._entries
        )

    def last_test_outcome(self) -> Optional[Dict[str, Any]]:
        """Dernière issue de tests connue (dict test_outcome) ou None.

        Sert au message honnête de rétrogradation : « dernier pytest réel :
        X passed / Y errors ». Prend la plus récente commande de test.
        """
        for e in reversed(self._entries):
            outcome = (e.meta or {}).get("test_outcome")
            if isinstance(outcome, dict) and outcome.get("is_test_cmd"):
                return outcome
        return None

    def written_basenames(self) -> set:
        """Basenames (minuscule) des fichiers réellement écrits/modifiés avec succès.

        Sert au croisement artefact↔ledger. NB : ne contient PAS les fichiers
        produits par des SOUS-agents (ledgers distincts) — à utiliser avec
        prudence côté mission-lead (cf. react: on n'infère un artefact fantôme
        que de façon conservatrice).
        """
        import os as _os
        write_tools = {
            "write_file", "edit_file", "create_file", "apply_patch", "apply_patches",
            "insert_at_anchor", "edit_by_lines", "str_replace", "multi_edit_file",
            "create_markdown", "create_html",
        }
        out: set = set()
        for e in self._entries:
            if e.success and e.action in write_tools and e.target:
                out.add(_os.path.basename(str(e.target)).lower())
        return out

    def writes_after_last_publish(self) -> List[LedgerEntry]:
        """LOT Z24 — ecritures REUSSIES posterieures a la derniere publication.

        Run « jeu 3D » (2026-08-19), les deux faits sont persistes COTE A COTE
        dans le meme enregistrement de tache, et personne ne les croise :

            published_files : ['CONTRAT.md','contract.json','index.html',
                               'script.js','style.css']
            ledger          : write_file -> 'jeu-3d-monde-ouvert/README.md'
                              success=True, iteration 26  (APRES la publication)
            terminal_reason : completed — « toutes les portes de cloture ont
                              autorise le resultat »

        Le README que l'objectif demandait (« index.html, styles, scripts,
        instructions ») n'a jamais rejoint le livrable, et rien ne l'a dit.

        Publier fige un instantane : tout ce qui s'ecrit APRES est, par
        construction, hors du livrable tant qu'on ne republie pas. Liste vide
        s'il n'y a pas eu de publication reussie — inerte par defaut.
        """
        last_pub = -1.0
        for e in self._entries:
            if e.success and e.action == "publish_mission_workspace":
                last_pub = max(last_pub, e.timestamp)
        if last_pub < 0:
            return []
        write_tools = {
            "write_file", "edit_file", "create_file", "apply_patch", "apply_patches",
            "insert_at_anchor", "edit_by_lines", "str_replace", "multi_edit_file",
            "create_markdown", "create_html",
        }
        return [
            e for e in self._entries
            if e.success and e.action in write_tools and e.target
            and e.timestamp > last_pub
        ]

    def has_published(self) -> bool:
        """LOT E (run FidéliBar 2026-07-04) — True si `publish_mission_workspace`
        a réussi dans CE run. Sert au verrou de vérité : « publié / livrable final
        publié / succès complet livré » n'est licite qu'avec cette preuve
        déterministe. Publier ≠ écrire des fichiers : FidéliBar a écrit des fichiers
        (has_any_mutation=True) mais n'a JAMAIS publié, et a pourtant annoncé
        « Publié ✅ dans workspace/fidelibar/ »."""
        return self.has_successful_action("publish_mission_workspace")

    def has_browser_action(self) -> bool:
        """LOT 2.10 — True si une VRAIE action navigateur (browser_*) a réussi dans
        CE run. Sert au verrou de vérité : « vérifié au navigateur » n'est licite
        qu'avec cette preuve (run StockPilot : claim fabriqué passé sans elle).
        NB : les actions navigateur d'un SOUS-agent (CodeAgent) ne sont pas ici —
        rétrogradation conservatrice assumée (jamais fabriquer une vérif)."""
        return any(e.success and str(e.action).startswith("browser_") for e in self._entries)

    def has_fresh_browser_action(self) -> bool:
        """True when browser proof happened after the latest source mutation."""
        last_mutation = self._latest_source_mutation_timestamp()
        return any(
            e.timestamp > last_mutation
            and e.success
            and str(e.action).startswith("browser_")
            for e in self._entries
        )

    def has_js_syntax_check(self) -> bool:
        """LOT 2.4 (run MotDuJour 2026-07-06) — True si un contrôle de syntaxe JS
        (`node --check`) a RÉUSSI dans ce run. Preuve du JS GATE : pytest vert ne
        dit rien du JS (deux runs de suite : script.js invalide/CSS non chargé
        avec 4/4 pytest verts)."""
        for e in self._entries:
            if not e.success:
                continue
            hay = " ".join(
                str(x) for x in (e.action, e.target, (e.meta or {}).get("command")) if x
            ).lower()
            if "node --check" in hay or "node-check" in hay:
                return True
        return False

    def has_any_mutation(self) -> bool:
        """True si au moins une mutation réussie a été enregistrée."""
        return any(e.success and e.action in MUTATION_TOOLS for e in self._entries)

    def successful_mutations(self) -> List[LedgerEntry]:
        """Retourne toutes les mutations réussies."""
        return [e for e in self._entries if e.success and e.action in MUTATION_TOOLS]

    def has_mutation_in_family(self, family: frozenset) -> bool:
        """True si au moins une mutation réussie appartient à la famille donnée."""
        return any(e.success and e.action in family for e in self._entries)

    def has_mutation_for_target_hint(self, hint: str) -> bool:
        """True si une mutation réussie a une cible contenant hint (insensible à la casse).

        Conservateur : retourne True dès qu'un hint non vide matche une cible existante.
        Retourne False si hint est vide — ne bloque pas sur signal nul.
        """
        if not hint or len(hint) < 2:
            return True  # pas de signal clair → on ne bloque pas
        h = hint.lower().strip("#").strip()
        return any(
            e.success and e.action in MUTATION_TOOLS
            and e.target is not None
            and h in e.target.lower()
            for e in self._entries
        )

    def successful_actions(self) -> List[str]:
        """Retourne la liste dédupliquée des noms d'outils exécutés avec succès."""
        seen: set[str] = set()
        result: List[str] = []
        for e in self._entries:
            if e.success and e.action not in seen:
                seen.add(e.action)
                result.append(e.action)
        return result

    def snapshot(self) -> Dict[str, Any]:
        """Vue sérialisable complète du ledger."""
        return {
            "total_entries": len(self._entries),
            "successful_mutations": len(self.successful_mutations()),
            "entries": [e.to_dict() for e in self._entries],
        }

    def summary(self, max_entries: int = 10) -> str:
        """Résumé textuel compact pour debug/log."""
        if not self._entries:
            return "ExecutionLedger: (vide)"
        mutations = self.successful_mutations()
        lines = [f"ExecutionLedger: {len(self._entries)} entrées, {len(mutations)} mutations réussies"]
        for e in self._entries[-max_entries:]:
            status = "✅" if e.success else "❌"
            target_str = f" → {e.target}" if e.target else ""
            proof_str = f" [{e.proof[:60]}]" if e.proof else ""
            lines.append(f"  iter {e.iteration}: {status} {e.action}{target_str}{proof_str}")
        if len(self._entries) > max_entries:
            lines.append(f"  ... (+{len(self._entries) - max_entries} entrées)")
        return "\n".join(lines)

    def checkpoint_projection(self, max_recent: int = 5) -> Dict[str, Any]:
        """Projection compacte pour enrichir un checkpoint TaskOrchestrator.

        Retourne un dict léger avec :
        - total_actions, successful_mutations, success_rate
        - recent : les N dernières actions (action, target, success, iteration)
        Ne duplique pas le ledger complet — c'est un résumé orienté checkpoint.
        """
        total = len(self._entries)
        mutations = self.successful_mutations()
        successes = sum(1 for e in self._entries if e.success)
        recent_entries = self._entries[-max(1, max_recent):]
        return {
            "total_actions": total,
            "successful_mutations": len(mutations),
            "success_rate": round(successes / total, 2) if total else 0.0,
            "recent": [
                {
                    "action": e.action,
                    "target": e.target,
                    "success": e.success,
                    "iteration": e.iteration,
                }
                for e in recent_entries
            ],
        }

    def clear(self) -> None:
        """Vide le journal (pour reset entre runs dans les tests)."""
        self._entries.clear()


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
