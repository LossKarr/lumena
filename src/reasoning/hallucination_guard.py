"""Guard anti-hallucination d'ACTION — primitives pures, extraites de react.py.

Ce module regroupe TOUT ce qui sert à détecter qu'un FINAL *prétend* avoir agi
sans qu'un outil de la famille requise ait RÉELLEMENT réussi :

- les familles sémantiques d'outils (`_HC_TOOLS_*`) ;
- les patterns de "claim d'action" (`_HALLUCINATION_CLAIM_PATTERNS`) ;
- la normalisation de texte + la détection de négation de claim ;
- la fonction pure `hallucination_retry_query(...)` qui décide du retry.

Extraction sans changement de comportement : le contenu est déplacé verbatim
depuis react.py. La méthode `ReActLoop._action_hallucination_retry_query` est
désormais un mince wrapper qui délègue à `hallucination_retry_query`.

NB : on NE code JAMAIS en dur les noms d'outils MCP (`mcp__<serveur>__<Tool>`).
Ils sont dynamiques (ajoutés à l'install d'un MCP) ; à la place, les familles
bureau/login (`_HC_CU_FAMILIES`) acceptent génériquement « un outil MCP a réussi »
comme preuve plausible.
"""
from __future__ import annotations

import re
import unicodedata

from loguru import logger


# ── Normalisation de texte (partagée avec les guards read-only/mutation) ─────
def _normalize_guard_text(text: str) -> str:
    """Normalise une requête pour un matching robuste des mots-clés.

    - minuscule + suppression des diacritiques (créer/creer, génère/genere…) ;
    - unification des apostrophes typographiques (’ ‘ ` ´ ʼ) en ' ;
    - compactage des espaces (apostrophe + espaces : "n' envoie" → "n'envoie").

    Évite les faux négatifs sur les négations ("N’envoie rien" vs "n'envoie rien").
    """
    nfd = unicodedata.normalize("NFD", (text or "").lower())
    no_accents = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    for apo in ("’", "‘", "ʼ", "´", "`"):
        no_accents = no_accents.replace(apo, "'")
    # "n' envoie" / "n'  envoie" → "n'envoie" ; compacte aussi les espaces.
    no_accents = no_accents.replace("' ", "'")
    return " ".join(no_accents.split())


# Alias rétro-compatible : ancienne API interne.
def _strip_accents(text: str) -> str:
    return _normalize_guard_text(text)


# ── Familles sémantiques d'outils pour le guard anti-hallucination ───────────
_HC_TOOLS_FILE = frozenset({
    "write_file", "edit_file", "apply_patch", "insert_at_anchor", "edit_by_lines",
    "str_replace", "multi_edit_file", "create_file", "create_html", "create_markdown",
    "create_from_template", "create_email_html", "create_ics", "create_vcard",
    "create_meeting_report", "create_zip",
})
_HC_TOOLS_DOC = frozenset({
    "create_pdf", "create_docx", "create_pptx", "create_xlsx", "create_csv",
    "create_invoice_pdf", "create_batch_documents", "edit_docx", "edit_pptx",
    "edit_xlsx", "annotate_pdf", "add_watermark", "assemble_document", "convert_document",
})
_HC_TOOLS_SITE = frozenset({
    "generate_website", "serve_website", "edit_website", "write_website_files",
    "create_project", "delegate_task", "delegate_task_bg",
})
_HC_TOOLS_TASK = frozenset({
    "create_task", "schedule_task", "memory_save", "memory_store", "memory_add", "create_skill",
})
_HC_TOOLS_MAIL = frozenset({"mail_send", "send_email", "mail_reply_message"})
_HC_TOOLS_DISCORD = frozenset({
    "discord_send", "discord_send_message", "discord_send_embed",
    "discord_create_channel", "discord_create_category", "discord_create_invite",
    "discord_create_role", "discord_delete_channel", "discord_delete_message",
    "discord_delete_role", "discord_modify_channel", "discord_pin", "discord_unpin",
    "discord_assign_role", "discord_remove_role", "discord_ban", "discord_unban",
    "discord_kick", "discord_set_channel_permissions", "discord_server_configure",
})
_HC_TOOLS_MESSAGING = frozenset({
    "telegram_send_message", "telegram_send_document",
    "send_whatsapp_message", "send_whatsapp_document", "send_whatsapp_photo",
    "send_whatsapp_audio", "send_message", "send_critical_sms",
})
_HC_TOOLS_SOCIAL = frozenset({
    "twitter_post_tweet", "twitter_reply", "twitter_like", "twitter_compose_thread",
})
_HC_TOOLS_STRIPE = frozenset({
    "stripe_create_product", "stripe_update_product", "stripe_delete_product",
    "stripe_create_price", "stripe_create_payment_link", "stripe_update_payment_link",
    "stripe_create_customer", "stripe_update_customer", "stripe_create_subscription",
    "stripe_cancel_subscription", "stripe_create_invoice", "stripe_send_invoice",
    "stripe_void_invoice", "stripe_add_invoice_item", "stripe_create_checkout_session",
    "stripe_create_coupon", "stripe_delete_coupon", "stripe_create_refund",
})
_HC_TOOLS_GITHUB = frozenset({
    "github_repo_create", "github_file_write", "github_push_directory",
    "git_add", "git_commit", "git_push_pull", "git_init",
})
_HC_TOOLS_IMAGE = frozenset({
    "generate_image", "edit_image", "generate_thumbnail", "generate_thumbnail_pro",
    "generate_logo", "generate_svg", "upscale_image", "remove_background",
    "replace_background", "sketch_to_image", "compose_image", "generate_video", "edit_video",
})
_HC_TOOLS_NOTION = frozenset({"notion_create_page", "notion_update_page", "notion_add_to_database"})
_HC_TOOLS_RUNTIME = frozenset({
    "process_status", "health_check", "web_fetch",
    "browser_navigate", "browser_get_content", "browser_dom_state",
})
# Phase I-8 (Fix AF) : outils MCP comptant comme preuve d'une action
# d'installation/activation MCP réelle (exonération du guard sans-plan).
_HC_TOOLS_MCP = frozenset({
    "run_mcp_autonomy", "add_mcp", "resume_mcp_task", "request_mcp_ticket",
})
_HC_TOOLS_ANY_CREATE = (
    _HC_TOOLS_FILE | _HC_TOOLS_DOC | _HC_TOOLS_SITE | _HC_TOOLS_TASK
    | _HC_TOOLS_GITHUB | _HC_TOOLS_STRIPE | _HC_TOOLS_IMAGE | _HC_TOOLS_NOTION
    | _HC_TOOLS_DISCORD | _HC_TOOLS_MCP
)
_HC_TOOLS_ANY_SEND = _HC_TOOLS_MAIL | _HC_TOOLS_MESSAGING | _HC_TOOLS_DISCORD | _HC_TOOLS_SOCIAL | _HC_TOOLS_GITHUB

# ── Computer Use / bureau / login — interactions clavier/souris ──────────────
# Couvre les DEUX systèmes de pilotage bureau (CU natif + serveur MCP
# windows-mcp) + les interactions browser, pour qu'un claim « j'ai tapé /
# cliqué / ouvert l'app / connecté » exige qu'un outil correspondant ait
# RÉELLEMENT réussi (cas vécu : "texte tapé" alors que Type a échoué 2×).
# NB : on NE code PAS en dur les noms d'outils MCP (mcp__<serveur>__<Tool>).
# Ils sont dynamiques (ajoutés à l'install d'un MCP) → les lister serait fragile
# et provoquerait des faux positifs. À la place, hallucination_retry_query
# accepte génériquement « un outil MCP a réussi » comme preuve plausible pour ces
# familles bureau/login (cf. _HC_CU_FAMILIES).
_HC_TOOLS_TYPE = frozenset({
    "type_text", "ui_type", "paste", "clear_field",
    "browser_type", "browser_type_index", "browser_login", "browser_save_login",
})
_HC_TOOLS_OPEN_APP = frozenset({
    "open_app", "open_url", "open_application", "browser_start", "browser_navigate",
    "run_command",
    # Spotify : "j'ai lancé Spotify" est prouvé par le lancement réel du média.
    "spotify_play", "spotify_api_play",
})
_HC_TOOLS_CLICK = frozenset({
    "click", "double_click", "ui_click", "mouse_pattern", "click_element", "find_element",
    "browser_click", "browser_click_index", "browser_click_smart", "browser_click_at", "browser_navigate",
})
_HC_TOOLS_LOGIN = frozenset({"browser_login", "browser_verify"}) | _HC_TOOLS_TYPE

# ── Familles d'action complémentaires (carte complète des outils natifs) ──────
# Issues de docs/tool_guard_classification.md. Servent à : (1) la preuve des
# claims VAGUES via _HC_TOOLS_ANY_ACTION, (2) le test anti-dérive (tout outil
# natif doit être classé). Les outils MCP dynamiques (mcp__*) sont exclus.
# Contrôle lecture média (Spotify)
_HC_TOOLS_MEDIA = frozenset({
    "spotify_api_play", "spotify_next", "spotify_pause", "spotify_play", "spotify_prev",
    "spotify_queue", "spotify_resume", "spotify_volume"
})
# Exécution code/commandes & processus
_HC_TOOLS_EXEC = frozenset({
    "bg_cancel", "bg_start", "browser_evaluate", "browser_frame_evaluate", "capture_traffic",
    "dev_run_fix", "edit_notebook_cell", "execute_multilang", "execute_skill", "fanout_tasks",
    "lint_and_fix", "multi_agent_parallel", "netcat_probe", "nmap_scan", "parallel_tools",
    "port_scan_fast", "process_input", "process_kill", "process_run", "reload_skills",
    "reverse_shell_listen", "rollback", "run_command", "run_tests", "ssh_exec",
    "stripe_cli_start", "stripe_cli_stop", "sync_skills_main", "test_and_fix"
})
# Mutations IDE Lumena
_HC_TOOLS_IDE = frozenset({
    "ide_diff", "ide_editor_close_tab", "ide_editor_cursor_goto", "ide_editor_insert",
    "ide_editor_save", "ide_editor_select", "ide_editor_switch_tab", "ide_find_replace",
    "ide_launch", "ide_navigate", "ide_open_file", "ide_open_workspace",
    "ide_sidebar_create_file", "ide_sidebar_create_folder", "ide_sidebar_delete",
    "ide_sidebar_rename", "ide_terminal", "ide_terminal_clear", "ide_toggle_chat",
    "ide_toggle_search", "ide_toggle_sidebar", "ide_toggle_terminal", "ide_window_close",
    "ide_window_maximize", "ide_window_minimize", "ide_write_file"
})
# Mutations techniques navigateur
_HC_TOOLS_BROWSER_TECH = frozenset({
    "browser_accept_cookies", "browser_batch", "browser_block_resources",
    "browser_cookies_clear", "browser_dismiss_popups", "browser_emulate_device",
    "browser_emulate_media", "browser_handle_dialog", "browser_network_clear",
    "browser_set_geolocation", "browser_storage_clear", "browser_storage_set",
    "browser_trace_start", "browser_trace_stop", "browser_unblock_resources"
})
# Déploiement/transfert distant IONOS
_HC_TOOLS_DEPLOY = frozenset({
    "deploy_to_ionos", "ionos_add_site", "ionos_clear_site_database", "ionos_delete_files",
    "ionos_remove_site", "ionos_set_site_database", "update_ionos_files"
})
# Écriture structurelle BDD IONOS (bridge)
_HC_TOOLS_DB = frozenset({
    "ionos_db_create_sandbox_table", "ionos_db_install_bridge"
})
# Proposition d'écriture/suppression BDD (n'exécute rien)
_HC_TOOLS_DB_PROPOSE = frozenset({
    "ionos_db_propose_clear_sandbox_table", "ionos_db_propose_delete",
    "ionos_db_propose_drop_sandbox_table", "ionos_db_propose_write"
})
# Flags config BDD IONOS
_HC_TOOLS_DB_CONFIG = frozenset({
    "ionos_db_set_delete_config", "ionos_db_set_react_delete_config",
    "ionos_db_set_react_write_config", "ionos_db_set_restore_config",
    "ionos_db_set_sandbox_clear_config", "ionos_db_set_sandbox_config",
    "ionos_db_set_sandbox_drop_config", "ionos_db_set_write_config"
})
# Actions machines distantes
_HC_TOOLS_NETWORK = frozenset({
    "network_exec", "network_file_download", "network_file_edit", "network_file_upload",
    "network_self_deploy", "network_set_credentials", "network_shutdown", "network_wol"
})
# Mutations workflows n8n
_HC_TOOLS_N8N = frozenset({
    "n8n_activate_workflow", "n8n_create_from_template", "n8n_create_workflow",
    "n8n_deactivate_workflow", "n8n_delete_workflow", "n8n_import_online_template",
    "n8n_search_online_templates", "n8n_trigger_webhook", "n8n_trigger_workflow",
    "n8n_update_workflow"
})
# Auto-modification (skills/outils custom)
_HC_TOOLS_SKILL = frozenset({
    "create_skill", "update_skill", "delete_skill",
    "custom_tool_create", "custom_tool_load", "edit_own_code"
})
# Requêtes HTTP mutatives
_HC_TOOLS_HTTP = frozenset({
    "http_api_register", "http_request", "http_upload_file", "http_webhook_test"
})
# Délégation à un pair Lumena
_HC_TOOLS_PEER = frozenset({
    "delegate_to_peer", "orchestrate_peer_request", "peer_team_request",
    "propose_peer_knowledge", "run_peer_task_sync", "submit_peer_task"
})
# « Confier/envoyer une mission à un pair » est une forme d'ENVOI légitime :
# un claim « j'ai envoyé/confié la mission à l'autre Lumena » doit accepter les
# outils peer comme preuve (sinon faux positif d'hallucination → retry inutile).
_HC_TOOLS_ANY_SEND = _HC_TOOLS_ANY_SEND | _HC_TOOLS_PEER
# Config Lumena/heartbeat
_HC_TOOLS_CONFIG = frozenset({
    "heartbeat_manage", "update_lumena_config"
})
# Ingestion document en mémoire
_HC_TOOLS_MEMORY = frozenset({
    "ingest_document"
})
# Administration boîte mail
_HC_TOOLS_MAIL_ADMIN = frozenset({
    "mail_account_upsert", "mail_delete_message", "mail_download_attachments",
    "mail_move_message", "mail_remove_account"
})
# Tâche Computer Use autonome
_HC_TOOLS_CU_TASK = frozenset({
    "computer_task"
})

# Lecture seule / hors-garde (228 outils) — ne prouvent rien, ne bloquent jamais.
_HC_TOOLS_READONLY = frozenset({
    "analyze_document", "autonomy_activity_summary", "autonomy_next_best_action", "bg_list",
    "bg_status", "browser_check_challenge", "browser_cookies_get", "browser_deep_research",
    "browser_dialog_log", "browser_dom_state", "browser_find", "browser_frame_content",
    "browser_frames", "browser_get_chat_messages", "browser_get_content", "browser_get_text",
    "browser_list_downloads", "browser_list_logins", "browser_list_tabs", "browser_metrics",
    "browser_network_requests", "browser_page_info", "browser_screenshot",
    "browser_screenshot_labels", "browser_search_google", "browser_search_maps",
    "browser_storage_get", "browser_tab_find", "browser_tabs", "browser_verify_local_project",
    "browser_wait_for", "check_injection", "check_web_project", "chunk_document",
    "codebase_index", "codebase_search", "codebase_stats", "compare_documents", "cu_readiness",
    "custom_tool_search", "data_aggregate", "data_filter_rows", "data_join",
    "data_profile_file", "data_unique_values", "datagouv_get_dataset", "datagouv_search",
    "decode_base64", "decode_hex", "deep_research", "discord_fetch_messages",
    "discord_list_channels", "discord_list_guilds", "discord_list_invites",
    "discord_list_members", "discord_list_roles", "discord_server_info", "discover_tools",
    "document_summary", "domain_recon", "email_check", "explain_lumena_config",
    "file_crawl_campaign", "file_crawl_campaign_export", "file_crawl_campaign_status",
    "find_files", "fork_analyze", "geo_commune_info", "geo_reverse", "geo_search_address",
    "get_active_window", "get_agents_status", "get_curiosity_status", "get_last_test_failure",
    "get_lumena_config", "get_my_capabilities", "get_peer_task_status",
    "get_recent_src_changes", "get_time", "get_token_stats", "git_diff", "git_log",
    "git_status", "github_file_read", "github_issues_list", "github_repo_list",
    "github_search_code", "grep_batch", "grep_search", "http_api_list", "http_headers_check",
    "ide_editor_get_content", "ide_get_state", "ide_list_files", "ide_read_file",
    "ide_search_in_files", "ide_status", "ide_terminal_get_output", "ionos_db_bridge_status",
    "ionos_db_describe_table", "ionos_db_get_config", "ionos_db_get_delete_config",
    "ionos_db_get_react_delete_config", "ionos_db_get_react_write_config",
    "ionos_db_get_restore_config", "ionos_db_get_sandbox_clear_config",
    "ionos_db_get_sandbox_config", "ionos_db_get_sandbox_drop_config",
    "ionos_db_get_write_config", "ionos_db_list_pending_actions", "ionos_db_list_snapshots",
    "ionos_db_list_tables", "ionos_db_select", "ionos_list_files", "ionos_list_sites",
    "ionos_test_site_database", "ip_info", "js_surface_map", "kg_search", "list_backups",
    "list_directory", "list_image_models", "list_journal_dates", "list_pdf_fields",
    "list_scheduled_tasks", "list_screens", "list_skills", "list_tasks", "list_templates",
    "list_video_projects", "list_website_projects", "list_windows", "lsp_check",
    "lsp_diagnostics", "lsp_find_references", "lsp_goto_definition", "lsp_hover", "lsp_servers",
    "mail_list_accounts", "mail_list_folders", "mail_list_messages", "mail_quick_test",
    "mail_read_message", "memory_get", "memory_search", "memory_stats", "n8n_get_execution",
    "n8n_get_workflow", "n8n_list_executions", "n8n_list_node_types", "n8n_list_templates",
    "n8n_list_workflows", "n8n_status", "network_file_list", "network_info", "network_list",
    "network_port_scan", "network_scan", "notion_list_databases", "notion_query_database",
    "notion_read_page", "notion_search", "open_file", "osint_scan", "pip_check", "plan_list",
    "port_scan", "process_list", "process_status", "query_peer_knowledge", "read_document",
    "read_file", "read_files_batch", "read_journal", "read_logs", "read_notebook",
    "read_own_code", "read_skill_reference", "reverse_dns", "sanitize_external_content",
    "screenshot", "screenshot_analyze", "search_in_code", "search_journal", "shodan_host_info",
    "shodan_search", "sirene_get_by_siret", "sirene_search_company", "spotify_current",
    "ssl_check", "strings_extract", "stripe_cli_status", "stripe_get_balance",
    "stripe_get_invoice", "stripe_list_checkout_sessions", "stripe_list_coupons",
    "stripe_list_customers", "stripe_list_invoices", "stripe_list_payment_links",
    "stripe_list_prices", "stripe_list_products", "stripe_list_refunds",
    "stripe_list_subscriptions", "stripe_search_customers", "subdomain_enum",
    "suggest_instincts", "task_history", "tech_detect", "threat_check", "twitter_get_mentions",
    "twitter_get_my_stats", "twitter_get_timeline", "twitter_get_user_info", "twitter_search",
    "twitter_status", "ui_list_controls", "view_file_outline", "view_outline", "wait",
    "wayback_check", "web_crawl", "web_crawl_campaign_explain", "web_crawl_campaign_status",
    "web_fetch", "web_search", "web_search_brave", "whois_lookup", "xor_decode"
})

# TOUTE action (341 outils natifs + MCP curatés) — preuve d'une action réelle
# quelconque (sert aux claims VAGUES « c'est fait » et à l'exonération ledger).
_HC_TOOLS_ANY_ACTION = frozenset({
    "add_watermark", "annotate_pdf", "apply_patch", "apply_patches", "assemble_document",
    "batch_documents", "bg_cancel", "bg_start", "browser_accept_cookies", "browser_back",
    "browser_batch", "browser_block_resources", "browser_click", "browser_click_at",
    "browser_click_index", "browser_click_smart", "browser_close_all_tabs", "browser_close_tab",
    "browser_cookies_clear", "browser_dismiss_popups", "browser_drag", "browser_drag_at",
    "browser_emulate_device", "browser_emulate_media", "browser_evaluate", "browser_forward",
    "browser_frame_click", "browser_frame_evaluate", "browser_frame_type",
    "browser_handle_dialog", "browser_hover", "browser_keyboard_press", "browser_login",
    "browser_navigate", "browser_network_clear", "browser_new_tab", "browser_open_tab",
    "browser_refresh", "browser_save_login", "browser_save_pdf", "browser_scroll",
    "browser_select", "browser_set_geolocation", "browser_solve_challenge", "browser_start",
    "browser_stop", "browser_storage_clear", "browser_storage_set", "browser_switch_tab",
    "browser_tab_switch", "browser_trace_start", "browser_trace_stop", "browser_type",
    "browser_type_index", "browser_unblock_resources", "browser_upload_file", "browser_verify",
    "browser_wait_for_download", "cancel_task", "capture_traffic", "click", "click_element",
    "close_app", "close_window", "compose_image", "computer_task", "convert_document",
    "create_csv", "create_directory", "create_docx", "create_email_html",
    "create_from_template", "create_html", "create_ics", "create_invoice_pdf",
    "create_markdown", "create_meeting_report", "create_pdf", "create_pptx", "create_project",
    "create_skill", "create_vcard", "create_xlsx", "create_zip", "cursor_ide_local",
    "custom_tool_create", "custom_tool_load", "data_export", "datagouv_download_resource",
    "delegate_task", "delegate_task_bg", "delegate_to_peer", "delete_file", "delete_skill",
    "delete_task", "update_skill",
    "deploy_to_ionos", "dev_run_fix", "discord_assign_role", "discord_ban",
    "discord_create_category", "discord_create_channel", "discord_create_invite",
    "discord_create_role", "discord_delete_channel", "discord_delete_message",
    "discord_delete_role", "discord_kick", "discord_modify_channel", "discord_pin",
    "discord_remove_role", "discord_send", "discord_send_embed", "discord_send_message",
    "discord_server_configure", "discord_set_channel_permissions", "discord_unban",
    "discord_unpin", "double_click", "drag", "edit_docx", "edit_file", "edit_image",
    "edit_notebook_cell", "edit_own_code", "edit_pptx", "edit_video", "edit_website",
    "edit_xlsx", "execute_multilang", "execute_skill", "export_website_zip", "fanout_tasks",
    "fill_pdf_form", "find_element", "generate_chart", "generate_headlines", "generate_image",
    "generate_logo", "generate_svg", "generate_thumbnail", "generate_thumbnail_pro",
    "generate_video", "generate_website", "git_add", "git_branch", "git_commit", "git_init",
    "git_push_pull", "git_remote", "github_file_delete", "github_file_write",
    "github_issue_create", "github_push_directory", "github_repo_create", "github_repo_delete",
    "heartbeat_manage", "hotkey", "html_to_pdf", "http_api_register", "http_request",
    "http_upload_file", "http_webhook_test", "ide_diff", "ide_editor_close_tab",
    "ide_editor_cursor_goto", "ide_editor_insert", "ide_editor_save", "ide_editor_select",
    "ide_editor_switch_tab", "ide_find_replace", "ide_launch", "ide_navigate", "ide_open_file",
    "ide_open_workspace", "ide_sidebar_create_file", "ide_sidebar_create_folder",
    "ide_sidebar_delete", "ide_sidebar_rename", "ide_terminal", "ide_terminal_clear",
    "ide_toggle_chat", "ide_toggle_search", "ide_toggle_sidebar", "ide_toggle_terminal",
    "ide_window_close", "ide_window_maximize", "ide_window_minimize", "ide_write_file",
    "image_to_document", "ingest_document", "insert_at_anchor", "ionos_add_site",
    "ionos_clear_site_database", "ionos_db_create_sandbox_table", "ionos_db_install_bridge",
    "ionos_db_propose_clear_sandbox_table", "ionos_db_propose_delete",
    "ionos_db_propose_drop_sandbox_table", "ionos_db_propose_write",
    "ionos_db_set_delete_config", "ionos_db_set_react_delete_config",
    "ionos_db_set_react_write_config", "ionos_db_set_restore_config",
    "ionos_db_set_sandbox_clear_config", "ionos_db_set_sandbox_config",
    "ionos_db_set_sandbox_drop_config", "ionos_db_set_write_config", "ionos_delete_files",
    "ionos_remove_site", "ionos_set_site_database", "learn_from_action", "lint_and_fix",
    "mail_account_upsert", "mail_delete_message", "mail_download_attachments",
    "mail_move_message", "mail_remove_account", "mail_reply_message", "mail_send", "memory_add",
    "merge_pdfs", "modify_task", "mouse_pattern", "move_mouse", "multi_agent_parallel",
    "multi_edit_file", "n8n_activate_workflow", "n8n_create_from_template",
    "n8n_create_workflow", "n8n_deactivate_workflow", "n8n_delete_workflow",
    "n8n_import_online_template", "n8n_search_online_templates", "n8n_trigger_webhook",
    "n8n_trigger_workflow", "n8n_update_workflow", "netcat_probe", "network_exec",
    "network_file_download", "network_file_edit", "network_file_upload", "network_self_deploy",
    "network_set_credentials", "network_shutdown", "network_wol", "nmap_scan",
    "notify_critical", "notion_add_to_database", "notion_create_page", "notion_update_page",
    "open_app", "open_url", "orchestrate_peer_request", "parallel_tools", "peer_team_request",
    "place_critical_call", "plan_create", "plan_done", "plan_update", "port_scan_fast",
    "press_key", "preview_video", "process_input", "process_kill", "process_run",
    "propose_peer_knowledge", "protect_pdf", "reload_skills", "remind", "remove_background",
    "replace_background", "reverse_shell_listen", "rollback", "run_command",
    "run_peer_task_sync", "run_tests", "save_template", "schedule_task", "scroll",
    "send_critical_sms", "send_whatsapp_audio", "send_whatsapp_document",
    "send_whatsapp_message", "send_whatsapp_photo", "serve_website", "set_screen",
    "sign_document", "sketch_to_image", "split_pdf", "spotify_api_play", "spotify_next",
    "spotify_pause", "spotify_play", "spotify_prev", "spotify_queue", "spotify_resume",
    "spotify_volume", "ssh_exec", "stop_website_server", "stripe_add_invoice_item",
    "stripe_cancel_subscription", "stripe_cli_start", "stripe_cli_stop",
    "stripe_create_checkout_session", "stripe_create_coupon", "stripe_create_customer",
    "stripe_create_invoice", "stripe_create_payment_link", "stripe_create_price",
    "stripe_create_product", "stripe_create_refund", "stripe_create_subscription",
    "stripe_delete_coupon", "stripe_delete_product", "stripe_send_invoice",
    "stripe_update_customer", "stripe_update_payment_link", "stripe_update_product",
    "stripe_void_invoice", "submit_peer_task", "sync_skills_main", "telegram_send_document",
    "test_and_fix", "twitter_compose_thread", "twitter_like", "twitter_post_tweet",
    "twitter_reply", "type_text", "ui_click", "ui_type", "undo_edit", "update_ionos_files",
    "update_lumena_config", "upscale_image", "web_crawl_campaign", "web_crawl_campaign_export",
    "web_crawl_campaign_pro_report", "write_file", "write_journal", "write_website_files",
    "zip_documents", "zoom"
}) | _HC_TOOLS_MCP

# Familles « génériques » : un claim VAGUE (« c'est fait », install…) est prouvé
# par n'IMPORTE quelle action réelle, ou un outil MCP/runtime réussi.
_HC_GENERIC_FAMILIES = (_HC_TOOLS_ANY_CREATE, _HC_TOOLS_ANY_ACTION)

# RAPPEL d'une mission déléguée à un PAIR : sur un tour « alors ?/vérifie »,
# l'agent dit « c'est fait / j'ai envoyé / la mission est terminée » en parlant
# d'un travail fait EN ASYNCHRONE par l'autre Lumena, avec seulement des outils
# de LECTURE ce tour-ci. Le travail réel n'est pas local → on ne doit pas exiger
# un outil d'action local (sinon faux positif → l'agent se renie / reboucle).
# Quand ce contexte est détecté, on relâche UNIQUEMENT les familles vagues
# (ANY_CREATE/ANY_ACTION/ANY_SEND) ; les claims PRÉCIS (mail/discord/…) restent stricts.
_PEER_MISSION_RECALL_RE = re.compile(
    r"(?:\b(?:mission|t[âa]che|projet|livrable|site|fichiers?)\b.{0,60}"
    r"(?:confi|d[ée]l[ée]gu|\bpair\b|autre\s+lumena|autre\s+instance|l['’]autre))"
    r"|(?:(?:confi|d[ée]l[ée]gu).{0,60}(?:\blumena\b|\bpair\b|\binstance\b))",
    re.IGNORECASE,
)

# Familles « bureau/login » : pour elles, un outil MCP réussi (quel qu'il soit)
# compte comme preuve plausible (on ne peut pas connaître la sémantique d'un MCP
# installé dynamiquement). Le cas pur (claim SANS aucun outil) reste bloqué.
_HC_CU_FAMILIES = (_HC_TOOLS_TYPE, _HC_TOOLS_OPEN_APP, _HC_TOOLS_CLICK, _HC_TOOLS_LOGIN)

# Patterns de "claim d'action" → famille d'outils dont AU MOINS UN doit avoir
# réussi. Au niveau module pour être partagé entre le chemin "plan" et le
# chemin "sans plan" (anti-drift).
_HALLUCINATION_CLAIM_PATTERNS = [
    (r"\bj[''`]ai (créé|crée|planifié|planifie|enregistré|enregistre|configuré|configure|programmé|programme|ajouté|ajoute|sauvegardé|sauvegarde)\b", _HC_TOOLS_ANY_CREATE),
    (r"\bj[''`]ai (envoyé|envoye|expedié|expedie)\b", _HC_TOOLS_ANY_SEND),
    # NB : PAS de pattern dédié « confié/délégué … pair » → il causait un FAUX
    # POSITIF cross-tour : au tour « alors ? » (status), l'agent SE SOUVIENT
    # d'avoir confié la mission (au tour précédent) mais n'utilise que des outils
    # de lecture ce tour-ci ; le guard ne voyant que les outils du tour courant
    # croyait à une hallucination et forçait l'agent à se renier + re-déléguer.
    # Le couplage `_HC_TOOLS_ANY_SEND |= _HC_TOOLS_PEER` (plus haut) suffit pour
    # le cas légitime « j'ai envoyé la mission » + outil peer dans LE MÊME tour.
    (r"\bla tâche a été (créée|planifiée|enregistrée|programmée)\b", _HC_TOOLS_TASK),
    (r"\bj[''`]ai bien (enregistré|planifié|créé|configuré)\b", _HC_TOOLS_ANY_CREATE),
    (r"\bj[''`]ai bien (envoyé|envoye)\b", _HC_TOOLS_ANY_SEND),
    # Claim VAGUE « c'est fait/configuré/… » → prouvé par TOUTE action réelle
    # (sinon faux positifs : ex. « c'est fait » après spotify_play / un déploiement /
    # une config, outils légitimes hors de la famille "création").
    (r"\bc[''`]est (fait|configuré|planifié|enregistré|créé)\b", _HC_TOOLS_ANY_ACTION),
    # Claims d'install/activation/déploiement (migrés du guard sans-plan _HP_NOPLAN).
    # → ANY_ACTION : un déploiement (deploy_to_ionos), une install MCP (run_mcp_autonomy)
    #   ou tout autre outil d'action réussi compte comme preuve.
    (r"\bj[''`]ai (installé|installe|activé|active|testé|teste|déployé|deploye)\b", _HC_TOOLS_ANY_ACTION),
    (r"\b(a|ont) été (installé|installe|créé|cree|configuré|configure|activé|active|testé|teste|envoyé|envoye|généré|genere|déployé|deploye)", _HC_TOOLS_ANY_ACTION),
    (r"\b(installé|installe|activé|active|créé|cree|configuré|configure|testé|teste|déployé|deploye)\w*( et \w+)? avec succ[èe]s\b", _HC_TOOLS_ANY_ACTION),
    (r"\bdiscord.{0,30}(animé|anime|géré|gere|organisé|organise|avec succès|avec succes)\b", _HC_TOOLS_DISCORD),
    (r"\b(animé|anime).{0,20}discord\b", _HC_TOOLS_DISCORD),
    (r"\b(salon|channel|canal).{0,20}(créé|crée|supprimé|supprime)\b", _HC_TOOLS_DISCORD),
    (r"\b(message|messages|fichier|document|zip).{0,20}(envoyé|envoye|posté|poste|publié|publie)\b", _HC_TOOLS_MESSAGING | _HC_TOOLS_MAIL | _HC_TOOLS_DISCORD | _HC_TOOLS_SOCIAL),
    (r"\bj[''`]ai (appris|découvert|exploré|explore|recherché|recherche|étudié|etudie)\b", frozenset({"web_search", "web_search_brave", "ddg_search", "web_fetch", "memory_search", "browser_navigate", "browser_get_content"})),
    (r"\b(push réussi|push reussi|premier push|repository créé|repo créé|poussé sur github|pushé sur github|commit réussi|commit reussi|fichier poussé)\b", _HC_TOOLS_GITHUB),
    (r"\b(mail|email|courriel).{0,20}(envoyé|envoye|envoi effectué)\b", _HC_TOOLS_MAIL),
    (r"\b(image|logo|thumbnail|vignette|svg|vidéo|video).{0,30}(généré|genere|créé|crée|produit|rendu)\b", _HC_TOOLS_IMAGE),
    (r"\b(produit|abonnement|facture|paiement|remboursement).{0,20}(créé[e]?|crée[e]?|envoyé[e]?|annulé[e]?)\b", _HC_TOOLS_STRIPE),
    (r"\b(page|base de données|database).{0,20}(créée|ajoutée|mise à jour)\b", _HC_TOOLS_NOTION),
    # Computer Use / bureau / login (frappe, ouverture d'app, clic, connexion)
    (r"\bj[''`]ai (tapé|tape|saisi)\b", _HC_TOOLS_TYPE),
    (r"\btexte\b.{0,25}\b(tapé|tape|saisi)\b", _HC_TOOLS_TYPE),
    (r"\bj[''`]ai rempli (le |les |ce |un |mon |mes )?(champ|formulaire|identifiant|mot de passe)\b", _HC_TOOLS_TYPE),
    (r"\bj[''`]ai (ouvert|lancé|lance|démarré|demarre)\b.{0,30}\b(bloc-?notes|notepad|paint|explorateur|spotify|calculatrice|terminal|powershell|cmd|word|excel|chrome|firefox|edge|l[''`]application|l[''`]app)\b", _HC_TOOLS_OPEN_APP),
    (r"\bj[''`]ai cliqué\b", _HC_TOOLS_CLICK),
    (r"\b(connexion réussie|login réussi|authentification réussie|connecté[e]? avec succès)\b", _HC_TOOLS_LOGIN),
]

_HC_TEMPORAL_BYPASS_RE = re.compile(
    r"\bj[''`']ai\s+\w+(\s+\w+){0,5}\s+(plus\s+t[oô]t|pr[eé]c[eé]demment|avant|hier|la\s+derni[eè]re\s+fois|tout\s+[àa]\s+l[''']heure|tantôt|tantoˆt)|"
    r"\b(que\s+tu\s+m[''']a(vai[st]|s)\s+demand\w*|comme\s+(demand\w*|convenu)|"
    r"tout\s+[àa]\s+l[''']instant|juste\s+avant)\b",
    re.IGNORECASE,
)

_HINT_ONLY_PROOF_REQUIRED_TOOLS = frozenset({"run_command", "run_shell", "exec_command"})
_SERVER_RUNTIME_CLAIM_RE = re.compile(
    r"\b(serveur|server|processus|localhost|127\.0\.0\.1|::1|port\s*\d+).{0,40}"
    r"(lanc[ée]|demarr|démarr|running|tourne|actif|accessible|en ligne)\b",
    re.IGNORECASE,
)


def _has_runtime_server_claim_proof(text: str, successful_tools: set[str]) -> bool:
    if not text or not _SERVER_RUNTIME_CLAIM_RE.search(text):
        return False
    return any(tool in successful_tools for tool in _HC_TOOLS_RUNTIME)


# ── Négation de CLAIM pour le HALLUCINATION GUARD ────────────────────────────
# La négation doit être SYNTAXIQUEMENT liée au claim (négation + objet +
# participe/verbe d'action), PAS une simple présence de "rien"/"pas"/"aucun"
# dans la fenêtre — pour ne pas confondre "aucun problème, le tweet posté" avec
# une négation. Participe/verbe d'action (texte normalisé, sans accents) :
_CLAIM_ACTION_WORD: str = (
    r"(?:envoye?e?s?|expedie?e?s?|poste?e?s?|publie?e?s?|partage?e?s?|"
    r"cree?e?s?|modifie?e?s?|supprime?e?s?|ecrit[es]?|genere?e?s?|"
    r"exporte?e?s?|sauvegarde?e?s?|deploye?e?s?|effectue?e?s?|"
    r"enregistre?e?s?|configure?e?s?|planifie?e?s?|programme?e?s?|"
    r"ajoute?e?s?|produit[es]?|rendu[es]?|gere?e?s?|organise?e?s?|anime?e?s?)"
)
# 1. aucun/aucune/0/zero + objet(0-2 mots) + action  → "aucun message envoyé"
_CLAIM_NEG_QUANT_RE = re.compile(
    rf"\b(?:aucun|aucune|0|zero)\b(?:\s+\w+){{0,2}}?\s+{_CLAIM_ACTION_WORD}\b"
)
# 2. pas de/d' + objet(0-2 mots) + action  → "pas de message envoyé"
_CLAIM_NEG_PASDE_RE = re.compile(
    rf"\bpas\s+d(?:e|')\b(?:\s+\w+){{0,2}}?\s+{_CLAIM_ACTION_WORD}\b"
)
# 3. rien + (0-1 mot) + action  → "rien envoyé", "rien créé"
_CLAIM_NEG_RIEN_RE = re.compile(
    rf"\brien\b(?:\s+\w+){{0,1}}?\s+{_CLAIM_ACTION_WORD}\b"
)
# 4. ne/n' + aux/verbe(0-3 mots) + (pas|rien|jamais) + objet(0-2) + action
#    → "je n'ai rien envoyé", "je n'ai pas créé de fichier", "n'a pas été envoyé"
_CLAIM_NEG_NE_RE = re.compile(
    rf"\bne\b(?:\s+\w+){{0,3}}?\s+(?:pas|rien|jamais)\b"
    rf"(?:\s+\w+){{0,2}}?\s+{_CLAIM_ACTION_WORD}\b"
)


def claim_text_is_negated(text: str) -> bool:
    """True si le texte exprime un claim d'action NÉGATIF (rien fait).

    Exige une négation liée au verbe/participe d'action ("aucun message
    envoyé", "pas de fichier créé", "rien envoyé", "je n'ai rien envoyé"), pas
    une simple présence de "rien"/"pas"/"aucun". Réutilise `_normalize_guard_text`.
    """
    norm = _normalize_guard_text(text)
    # "n'ai" / "n'a" → "ne ai" / "ne a" pour unifier avec la règle "ne … pas/rien".
    t = norm.replace("n'", "ne ")
    return bool(
        _CLAIM_NEG_QUANT_RE.search(t)
        or _CLAIM_NEG_PASDE_RE.search(t)
        or _CLAIM_NEG_RIEN_RE.search(t)
        or _CLAIM_NEG_NE_RE.search(t)
    )


def claim_match_is_negated(text: str, start: int, end: int) -> bool:
    """True si le claim détecté à [start:end] est nié dans son contexte proche.

    Examine une fenêtre précédant le match (même proposition) : "aucun message
    envoyé" / "0 fichier créé" → nié. Évite de bloquer un rapport read-only.
    """
    window = text[max(0, start - 45):end]
    return claim_text_is_negated(window)


def hallucination_retry_query(
    combined_text: str,
    original_query: str,
    successful_tools: set[str],
    retries_used: int,
) -> tuple[str | None, int]:
    """Anti-hallucination d'ACTION, partagé entre le chemin avec/sans plan.

    Si le texte (thought+answer) prétend une action (créé/envoyé/tapé/cliqué/
    ouvert app/connecté) SANS qu'un outil de la famille requise ait RÉUSSI,
    retourne une requête de retry (et incrémente le compteur). Sinon None.

    Fonction PURE : `retries_used` entre en paramètre et ressort (possiblement
    +1) dans le tuple de retour. Comportement identique à l'ancienne méthode
    `ReActLoop._action_hallucination_retry_query`.
    """
    if retries_used >= 2:
        return None, retries_used
    if _HC_TEMPORAL_BYPASS_RE.search(combined_text):
        return None, retries_used
    tools = successful_tools
    browser_used = any(t.startswith("browser_") for t in tools)
    runtime_proof = _has_runtime_server_claim_proof(combined_text, tools)
    # Rappel d'une mission déléguée à un pair (travail fait en async, pas localement)
    peer_recall = bool(_PEER_MISSION_RECALL_RE.search(combined_text))
    for _pattern, _expected in _HALLUCINATION_CLAIM_PATTERNS:
        m = re.search(_pattern, combined_text, re.IGNORECASE)
        if not m:
            continue
        if claim_match_is_negated(combined_text, m.start(), m.end()):
            continue
        if _expected in _HC_GENERIC_FAMILIES and runtime_proof:
            continue
        # Contexte « mission déléguée à un pair » → relâche les familles VAGUES
        # (le livrable a été produit par l'autre Lumena, pas par un outil local).
        if peer_recall and (_expected in _HC_GENERIC_FAMILIES or _expected is _HC_TOOLS_ANY_SEND):
            continue
        if browser_used and any(
            kw in _pattern for kw in (
                "message|messages", "envoyé|envoye|expedié|expedie",
                r"\bj[''`]ai (envoyé|envoye",
            )
        ):
            continue
        # Un outil MCP réussi (installé dynamiquement, sémantique inconnue)
        # compte comme preuve plausible pour les claims bureau/login ET vagues
        # (« c'est fait », « installé avec succès » après un run_mcp_autonomy…).
        if (_expected in _HC_CU_FAMILIES or _expected in _HC_GENERIC_FAMILIES) and any(
            t.startswith("mcp__") for t in tools
        ):
            continue
        if not any(t in tools for t in _expected):
            retries_used += 1
            logger.warning(
                "[HALLUCINATION GUARD] action prétendue non exécutée "
                "(pattern: {}, attendus: {}, utilisés: {}) - retry {}/2",
                _pattern[:50], _expected, list(tools)[:5], retries_used,
            )
            return (
                f"Requête originale: {original_query}\n\n"
                "⛔ ERREUR CRITIQUE : Tu as déclaré FINAL en affirmant avoir accompli une action "
                f"({_pattern[:60]}...) SANS l'avoir réellement exécutée avec un outil!\n\n"
                f"Outils que tu as réellement appelés : {list(tools) or 'AUCUN'}\n\n"
                "Tu DOIS maintenant appeler l'outil approprié et ATTENDRE l'OBSERVATION de retour "
                "avant de conclure. INTERDICTION de prétendre qu'une action est faite sans OBSERVATION."
            ), retries_used
    return None, retries_used
