"""Synchronise .env.example depuis web.routes.config._CONFIG_SCHEMA + extras."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / ".env.example"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.routes.config import _CONFIG_SCHEMA

# ---------------------------------------------------------------------------
# Variables utilisées dans le code mais absentes du wizard (_CONFIG_SCHEMA).
# Format: (group, key, default, description)
# ---------------------------------------------------------------------------
_EXTRA_ENV_DOCS: list[tuple[str, str, str, str]] = [
    # --- Voix / STT ---
    ("Voix (avancé)", "LUMENA_STT_DEVICE", "cuda", "Device Whisper (cuda/cpu)"),
    ("Voix (avancé)", "LUMENA_STT_COMPUTE", "float16", "Type calcul Whisper (float16/int8)"),
    ("Voix (avancé)", "LUMENA_STT_MIC_INDEX", "", "Index micro à utiliser (vide = auto)"),
    ("Voix (avancé)", "LUMENA_STT_CALIBRATION_MULTIPLIER", "2.5", "Multiplicateur seuil bruit ambiant"),
    ("Voix (avancé)", "LUMENA_WAKE_WORD_TIMEOUT", "3600", "Timeout écoute wake word (sec)"),
    # --- Paths ---
    ("Paths (avancé)", "LUMENA_LOGS_DIR", "data/logs", "Dossier des logs"),
    ("Paths (avancé)", "LUMENA_BACKUPS_DIR", "./backups", "Dossier des backups"),
    # --- Docker Sandbox ---
    ("Docker Sandbox (avancé)", "LUMENA_SANDBOX_IMAGE", "lumena-sandbox", "Image Docker sandbox"),
    ("Docker Sandbox (avancé)", "LUMENA_SANDBOX_CPUS", "1", "CPUs alloués au conteneur"),
    ("Docker Sandbox (avancé)", "LUMENA_SANDBOX_PIDS_LIMIT", "256", "Limite de PIDs Docker"),
    ("Docker Sandbox (avancé)", "LUMENA_SANDBOX_STARTUP_MARGIN", "30", "Marge timeout startup Docker (sec)"),
    # --- Computer Use (avancé) ---
    ("Computer Use (avancé)", "LUMENA_VISION_MAX_IMAGE_SIZE", "1568", "Max pixels image pour LLM"),
    ("Computer Use (avancé)", "LUMENA_ANTHROPIC_CU_MODEL", "claude-sonnet-4-20250514", "Modèle Anthropic pour CU"),
    ("Computer Use (avancé)", "LUMENA_OPENAI_CU_MODEL", "computer-use-preview", "Modèle OpenAI pour CU"),
    ("Computer Use (avancé)", "LUMENA_CU_NATIVE_ORDER", "", "Ordre natif CU providers (CSV, vide = auto)"),
    ("Computer Use (avancé)", "LUMENA_CU_NATIVE_DISABLED", "0", "Désactiver CU natif (1 = off)"),
    # --- IDE / Éditeur ---
    ("IDE / Éditeur", "LUMENA_DEFAULT_WORKSPACE", "", "Workspace par défaut"),
    ("IDE / Éditeur", "LUMENA_IDE_READ_LINES", "200000", "Max lignes lecture IDE"),
    ("IDE / Éditeur", "LUMENA_IDE_LIST_ITEMS", "20000", "Max items listing IDE"),
    ("IDE / Éditeur", "LUMENA_IDE_COMMAND_TIMEOUT_SEC", "120", "Timeout commande IDE (sec)"),
    ("IDE / Éditeur", "LUMENA_IDE_OUTPUT_LIMIT", "2000000", "Limite output IDE (chars)"),
    ("IDE / Éditeur", "LUMENA_IDE_GREP_RESULTS", "10000", "Max résultats grep IDE"),
    ("IDE / Éditeur", "LUMENA_IDE_GREP_FILE_SIZE_MB", "50", "Max taille fichier grep (MB)"),
    ("IDE / Éditeur", "LUMENA_IDE_FIND_RESULTS", "20000", "Max résultats find IDE"),
    ("IDE / Éditeur", "LUMENA_IDE_WS_PORT", "8245", "Port WebSocket IDE bridge"),
    ("IDE / Éditeur", "LUMENA_CURSOR_IDE_PATH", "", "Chemin exécutable Cursor IDE"),
    # --- LLM / Provider (avancé) ---
    ("LLM (avancé)", "LUMENA_PROVIDER_CONCURRENCY", "2", "Concurrence max providers"),
    ("LLM (avancé)", "LUMENA_PROVIDER_MAX_FAILURES", "3", "Max échecs avant cooldown"),
    ("LLM (avancé)", "LUMENA_PROVIDER_COOLDOWN_MIN", "5", "Cooldown provider (minutes)"),
    ("LLM (avancé)", "LUMENA_FALLBACK_ORDER", "", "Ordre fallback providers (CSV, vide = auto)"),
    ("LLM (avancé)", "LUMENA_MAX_CONTINUATION_STEPS", "3", "Max étapes continuation réponse"),
    ("LLM (avancé)", "LUMENA_CODE_AUTOSWITCH_REASONER", "1", "Auto-switch vers reasoner pour code"),
    ("LLM (avancé)", "LUMENA_LLM_TRANSIENT_RETRIES", "2", "Retries erreurs transientes (429/5xx)"),
    ("LLM (avancé)", "LUMENA_LLM_RETRY_DELAYS", "1.0,3.0", "Délais backoff retry (sec, CSV)"),
    # --- Compaction / Contexte ---
    ("Compaction / Contexte", "LUMENA_DEFAULT_CONTEXT_TOKENS", "128000", "Max tokens fenêtre contexte"),
    ("Compaction / Contexte", "LUMENA_CONTEXT_COMPACTION_THRESHOLD", "0.6", "Seuil compaction (0.0-1.0)"),
    # --- ReAct / Outils ---
    ("ReAct / Outils (avancé)", "LUMENA_PATCH_STRICT", "1", "Mode strict apply_patch"),
    ("ReAct / Outils (avancé)", "LUMENA_CHAT_FILE_CARDS", "1", "Afficher file cards dans chat"),
    ("ReAct / Outils (avancé)", "LUMENA_TOOL_TIMEOUT", "30", "Timeout exécution outil (sec)"),
    ("ReAct / Outils (avancé)", "LUMENA_MAX_REACT_ITERATIONS_IDE", "35", "Max itérations ReAct mode IDE"),
    ("ReAct / Outils (avancé)", "LUMENA_REACT_TIMEOUT_IDE", "", "Timeout ReAct mode IDE (sec, vide = fallback)"),
    ("ReAct / Outils (avancé)", "LUMENA_REACT_HISTORY_OBS_CHARS_IDE", "12000", "Chars obs historique ReAct IDE"),
    ("ReAct / Outils (avancé)", "LUMENA_REACT_OBS_LIMIT", "", "Override global budget observation (chars). Vide = auto calibré par modèle"),
    ("ReAct / Outils (avancé)", "LUMENA_REACT_OBS_CLAMP", "", "Clamp min:max du budget obs (ex '4000:64000'). Vide = pas de clamp"),
    ("ReAct / Outils (avancé)", "LUMENA_REACT_PROTECT_LAST_READ", "1", "Protège la dernière obs d'un outil lecteur (read_file, grep, web_fetch...) de la microcompaction"),
    ("ReAct / Outils (avancé)", "LUMENA_OLLAMA_PROBE", "1", "Active le probe /api/show pour auto-détecter context_length réel des modèles Ollama"),
    ("ReAct / Outils (avancé)", "LUMENA_OLLAMA_PROBE_TTL", "86400", "TTL (sec) du cache de probes Ollama. 86400 = 24h"),
    ("ReAct / Outils (avancé)", "LUMENA_MODEL", "", "Alias legacy modèle (fallback)"),
    # --- Instance (avancé) ---
    ("Instance (avancé)", "LUMENA_MAX_CONTEXTS_PER_PLATFORM", "500", "Nombre max de contextes par plateforme (Telegram, Discord, etc.)"),
    # --- Réseau local ---
    ("Réseau local", "NETWORK_KEY", "", "Clé d'authentification pour scans réseau (WMI, SSH, etc.)"),
    ("Réseau local", "NETWORK_PASS", "", "Mot de passe pour scans réseau authentifiés"),
    # --- Navigateur (avancé) ---
    ("Browser (avancé)", "LUMENA_BROWSER_PROXY", "", "URL proxy navigateur"),
    # --- Email (avancé) ---
    ("Email (avancé)", "LUMENA_MAIL_MAX_ATTACHMENTS", "12", "Max pièces jointes"),
    ("Email (avancé)", "LUMENA_MAIL_MAX_ATTACHMENT_MB", "20", "Max taille PJ (MB)"),
    ("Email (avancé)", "LUMENA_MAIL_MAX_TOTAL_ATTACHMENTS_MB", "45", "Max taille totale PJ (MB)"),
    ("Email (avancé)", "LUMENA_MAIL_ALLOWED_ATTACHMENT_ROOTS", "", "Dossiers autorisés pour PJ (CSV)"),
    ("Email (avancé)", "LUMENA_MAIL_RETRY_DELAY_SEC", "0.8", "Délai retry SMTP (sec)"),
    ("Email (avancé)", "LUMENA_MAIL_ALLOWED_RECIPIENTS", "", "Destinataires autorisés (CSV, vide = tous)"),
    ("Email (avancé)", "LUMENA_MAIL_ALLOWED_DOMAINS", "", "Domaines autorisés (CSV, vide = tous)"),
    ("Email (avancé)", "LUMENA_MAIL_CONNECT_TIMEOUT_SEC", "20", "Timeout connexion SMTP (sec)"),
    ("Email (avancé)", "LUMENA_MAIL_SEND_MAX_PER_WINDOW", "30", "Max envois par fenêtre rate limit"),
    ("Email (avancé)", "LUMENA_MAIL_SEND_WINDOW_SEC", "600", "Fenêtre rate limit envoi (sec)"),
    ("Email (avancé)", "LUMENA_MAIL_MAX_RECIPIENTS", "20", "Max destinataires par email"),
    ("Email (avancé)", "LUMENA_MAIL_MAX_BODY_CHARS", "200000", "Max caractères body"),
    ("Email (avancé)", "LUMENA_MAIL_REPLY_MAX_PER_WINDOW", "30", "Max réponses par fenêtre"),
    ("Email (avancé)", "LUMENA_MAIL_REPLY_WINDOW_SEC", "600", "Fenêtre rate limit réponse (sec)"),
    ("Email (avancé)", "LUMENA_MAIL_DOWNLOAD_ATTACHMENTS_MAX_PER_WINDOW", "60", "Max downloads PJ par fenêtre"),
    ("Email (avancé)", "LUMENA_MAIL_DOWNLOAD_ATTACHMENTS_WINDOW_SEC", "600", "Fenêtre download PJ (sec)"),
    ("Email (avancé)", "LUMENA_MAIL_DELETE_MAX_PER_WINDOW", "100", "Max suppressions par fenêtre"),
    ("Email (avancé)", "LUMENA_MAIL_DELETE_WINDOW_SEC", "600", "Fenêtre suppression (sec)"),
    ("Email (avancé)", "LUMENA_MAIL_MOVE_MAX_PER_WINDOW", "150", "Max déplacements par fenêtre"),
    ("Email (avancé)", "LUMENA_MAIL_MOVE_WINDOW_SEC", "600", "Fenêtre déplacement (sec)"),
    # --- Telegram (avancé) ---
    ("Telegram (avancé)", "LUMENA_TELEGRAM_LOCK_PATH", "", "Chemin fichier lock Telegram (vide = auto)"),
    ("Telegram (avancé)", "LUMENA_TELEGRAM_CONNECT_TIMEOUT", "15.0", "Timeout connexion (sec)"),
    ("Telegram (avancé)", "LUMENA_TELEGRAM_READ_TIMEOUT", "30.0", "Timeout lecture (sec)"),
    ("Telegram (avancé)", "LUMENA_TELEGRAM_WRITE_TIMEOUT", "30.0", "Timeout écriture (sec)"),
    ("Telegram (avancé)", "LUMENA_TELEGRAM_POOL_TIMEOUT", "30.0", "Pool timeout (sec)"),
    ("Telegram (avancé)", "LUMENA_TELEGRAM_BOOTSTRAP_RETRIES", "1", "Retries bootstrap"),
    ("Telegram (avancé)", "LUMENA_TELEGRAM_STARTUP_RETRIES", "2", "Retries startup"),
    ("Telegram (avancé)", "LUMENA_TELEGRAM_STARTUP_RETRY_DELAY", "2.0", "Délai retry startup (sec)"),
    ("Telegram (avancé)", "LUMENA_TELEGRAM_DOC_PREVIEW_CHARS", "2500", "Chars preview document"),
    # --- WhatsApp (avancé) ---
    ("WhatsApp (avancé)", "LUMENA_WHATSAPP_CONNECT_TIMEOUT", "15.0", "Timeout connexion (sec)"),
    ("WhatsApp (avancé)", "LUMENA_WHATSAPP_READ_TIMEOUT", "30.0", "Timeout lecture (sec)"),
    ("WhatsApp (avancé)", "LUMENA_WHATSAPP_DOC_PREVIEW_CHARS", "2500", "Chars preview document"),
    ("WhatsApp (avancé)", "LUMENA_WHATSAPP_WEBHOOK_MAX_RETRIES", "3", "Retries webhook processing"),
    # --- IONOS (Hébergement) ---
    ("IONOS (avancé)", "LUMENA_IONOS_DEFAULT_SITE", "", "Domaine par défaut pour les déploiements SFTP"),
    ("IONOS (avancé)", "LUMENA_IONOS_BACKUP_BEFORE_DEPLOY", "1", "Backup distant avant chaque déploiement (0/1)"),
    ("IONOS (avancé)", "LUMENA_IONOS_MAX_UPLOAD_MB", "100", "Taille max totale d'un déploiement en Mo"),
    # --- Twitter ---
    ("Twitter", "LUMENA_DISABLE_TWITTER", "", "Désactiver canal Twitter (1 = off)"),
    ("Twitter", "LUMENA_TWITTER_POLL_INTERVAL", "90", "Intervalle poll Twitter (sec)"),
    ("Twitter", "LUMENA_TWITTER_MAX_TWEET_LEN", "280", "Max longueur tweet"),
    # --- Alertes (avancé) ---
    ("Alertes (avancé)", "LUMENA_CRITICAL_CALL_TWIML_URL", "", "URL TwiML pour appels critiques"),
    # --- Télémétrie / Traces ---
    ("Télémétrie / Traces", "LUMENA_TRACE_ENABLED", "True", "Activer bus de traces"),
    ("Télémétrie / Traces", "LUMENA_TRACE_BUFFER_SIZE", "500", "Taille buffer traces"),
    ("Télémétrie / Traces", "LUMENA_TRACE_SUMMARY_MAX_LEN", "280", "Max longueur résumé trace"),
    ("Télémétrie / Traces", "LUMENA_TRACE_HEARTBEAT_SEC", "10", "Intervalle heartbeat trace (sec)"),
    # --- File Edits / Undo ---
    ("File Edits / Undo", "LUMENA_UNDO_ENABLED", "True", "Activer undo sur éditions fichiers"),
    ("File Edits / Undo", "LUMENA_FILE_EDIT_PREVIEW_LINES", "12", "Lignes de preview édition"),
    ("File Edits / Undo", "LUMENA_FILE_EDIT_PREVIEW_LINE_MAX", "240", "Max chars par ligne preview"),
    ("File Edits / Undo", "LUMENA_FILE_EDIT_PAYLOAD_TEXT_MAX", "200000", "Max chars payload texte"),
    ("File Edits / Undo", "LUMENA_FILE_EDIT_MAX_SESSIONS", "120", "Max sessions d'édition"),
    # --- Autonomie (avancé) ---
    ("Autonomie (avancé)", "LUMENA_AUTONOMY_DAILY_SKILL_ENABLE", "True", "Skill quotidien activé"),
    ("Autonomie (avancé)", "LUMENA_AUTONOMY_DAILY_SKILL_DRY_RUN", "False", "Dry run skill quotidien"),
    ("Autonomie (avancé)", "LUMENA_AUTONOMY_RESEARCH_TIMEOUT_SEC", "900", "Timeout recherche autonome (sec)"),
    ("Autonomie (avancé)", "LUMENA_AUTONOMY_ACTION_REPEAT_COOLDOWN_SEC", "900", "Cooldown répétition action (sec)"),
    ("Autonomie (avancé)", "LUMENA_RETRAIN_MIN_EXAMPLES", "20", "Min exemples pour retrain"),
    ("Autonomie (avancé)", "LUMENA_AUTONOMY_MIN_FREE_GB", "10", "Seuil disque libre avant blocage actions lourdes"),
    # --- Agents ---
    ("Agents", "LUMENA_SUBAGENT_TIMEOUT", "0", "Timeout sub-agent (sec, 0 = off)"),
    ("Agents", "LUMENA_CODE_AGENT_MAX_ITER", "30", "Max itérations CodeAgent"),
    ("Agents", "LUMENA_CODE_AGENT_MAX_OUTER_RETRIES", "3", "Max retries externes CodeAgent"),
    ("Agents", "LUMENA_PROJECT_VIA_CODEAGENT", "1", "CodeAgent pour create_project (1=on, 0=force batch)"),
    # --- Core / Flags internes ---
    ("Core / Flags internes", "LUMENA_AGENT_FINAL_REPAIR", "True", "Auto-réparation finale agent"),
    ("Core / Flags internes", "LUMENA_MEMORY_AUTO_MIGRATE", "True", "Migration auto mémoire ChromaDB"),
    ("Core / Flags internes", "LUMENA_SKILLS_AUTO_ACTIVATION", "True", "Activation auto des skills"),
    ("Core / Flags internes", "LUMENA_DISABLE_INTENT_ROUTING", "", "Désactiver routage par intention (1 = off)"),
    ("Core / Flags internes", "LUMENA_DISABLE_DISCORD", "", "Désactiver canal Discord (1 = off)"),
    ("Core / Flags internes", "LUMENA_CHANNELS_LEGACY_IMPORTS", "", "Activer imports legacy channels (1 = on)"),
    # --- Orchestrateur ---
    ("Orchestrateur", "LUMENA_TASK_ORCHESTRATOR_STATE_PATH", "", "Chemin state orchestrateur (vide = auto)"),
    # --- Serveur (avancé) ---
    ("Serveur (avancé)", "LUMENA_RATE_WINDOW", "60", "Fenêtre rate limit (sec)"),
    ("Serveur (avancé)", "LUMENA_ADMIN_TOKEN", "", "Token admin API auth (vide = auto-généré)"),
    # --- SLO (avancé) ---
    ("SLO (avancé)", "LUMENA_SLO_WINDOW_SIZE", "300", "Fenêtre SLO (samples)"),
    ("SLO (avancé)", "LUMENA_SLO_ALERT_CONSECUTIVE", "3", "Alertes consécutives SLO"),
    ("SLO (avancé)", "LUMENA_SLO_SUCCESS_RATE_MIN", "0.92", "Taux succès min SLO"),
    ("SLO (avancé)", "LUMENA_SLO_TIMEOUT_RATE_MAX", "0.02", "Taux timeout max SLO"),
    ("SLO (avancé)", "LUMENA_SLO_LATENCY_MEDIAN_MAX_MS", "8000", "Latence médiane max (ms)"),
    ("SLO (avancé)", "LUMENA_SLO_LATENCY_P95_MAX_MS", "35000", "Latence P95 max (ms)"),
    ("SLO (avancé)", "LUMENA_SLO_UNDO_SUCCESS_RATE_MIN", "1.0", "Taux succès undo min"),
    # --- Session / Conversation ---
    ("Session / Conversation", "LUMENA_SESSION_STATE_TTL_SEC", "172800", "TTL state session (sec)"),
    ("Session / Conversation", "LUMENA_SESSION_STATE_MAX_ITEMS", "2048", "Max items state session"),
    ("Session / Conversation", "LUMENA_CONVERSATION_TTL_SEC", "86400", "TTL conversation (sec)"),
    ("Session / Conversation", "LUMENA_CONVERSATION_MAX_ITEMS", "1024", "Max items conversation"),
    # --- IDE Context ---
    ("IDE Context", "LUMENA_IDE_CONTEXT_TTL_SEC", "7200", "TTL contexte IDE (sec)"),
    ("IDE Context", "LUMENA_IDE_CONTEXT_MAX_ITEMS", "256", "Max items contexte IDE"),
    # --- SSE / Streaming ---
    ("SSE / Streaming", "LUMENA_TIMEOUT_AUTO_RESUME", "True", "Auto-reprise après timeout"),
    ("SSE / Streaming", "LUMENA_TIMEOUT_RESUME_BACKOFF_SEC", "0.4", "Backoff reprise timeout (sec)"),
    ("SSE / Streaming", "LUMENA_CHAT_GLOBAL_TIMEOUT_SEC", "0.0", "Timeout global /api/chat (sec, 0 = off)"),
    ("SSE / Streaming", "LUMENA_STREAM_GLOBAL_TIMEOUT_SEC", "0.0", "Timeout global stream (sec, 0 = off)"),
    ("SSE / Streaming", "LUMENA_SSE_MAX_THOUGHTS", "600", "Max pensées SSE par stream"),
    ("SSE / Streaming", "LUMENA_SSE_HEARTBEAT_SEC", "5", "Intervalle heartbeat SSE (sec)"),
    # --- Feature Flags ---
    ("Feature Flags", "LUMENA_RUNTIME_CONTEXT_V2", "False", "Feature flag runtime V2"),
    ("Feature Flags", "LUMENA_WORKSPACE_POLICY_V2", "False", "Feature flag workspace policy V2"),
    ("Feature Flags", "LUMENA_TASK_ORCHESTRATOR_V1", "False", "Feature flag orchestrateur V1"),
    ("Feature Flags", "LUMENA_STREAM_EVENT_V2", "False", "Feature flag stream event V2"),
    ("Feature Flags", "LUMENA_OMNICHANNEL_ENVELOPE_V1", "False", "Feature flag omnichannel V1"),
    ("Feature Flags", "LUMENA_SINGLE_INSTANCE", "True", "Mode instance unique (lock fichier)"),
    ("Feature Flags", "LUMENA_WEB_AUTONOMY_ENABLED", "True", "Autonomie activée sur web"),
    # --- Clés API (non-wizard) ---
    ("Clés API (non-wizard)", "TELEGRAM_CHAT_ID", "", "Chat ID Telegram (envoi direct)"),
    ("Clés API (non-wizard)", "TELEGRAM_BOT_TOKEN", "", "Alias de TELEGRAM_TOKEN (legacy)"),
    ("Clés API (non-wizard)", "DISCORD_BOT_TOKEN", "", "Alias de DISCORD_TOKEN (legacy)"),
    ("Clés API (non-wizard)", "DISCORD_GUILD_ID", "", "ID du serveur Discord"),
    ("Clés API (non-wizard)", "DISCORD_PREFIX", "!", "Préfixe commandes Discord"),
    ("Clés API (non-wizard)", "DISCORD_ADMIN_IDS", "", "User IDs admin Discord (CSV)"),
    ("Clés API (non-wizard)", "DISCORD_ADMIN_ROLE_IDS", "", "Role IDs admin Discord (CSV)"),
    ("Clés API (non-wizard)", "DISCORD_ADMIN_ROLES", "admin,administrateur,moderateur,lumena-admin", "Noms rôles admin Discord (CSV)"),
    ("Clés API (non-wizard)", "DISCORD_GLOBAL_CHANNEL_ID", "", "Channel ID global Discord (alias)"),
    ("Clés API (non-wizard)", "SHODAN_API_KEY", "", "Clé API Shodan (scan réseau)"),
    ("Clés API (non-wizard)", "TWITTER_BEARER_TOKEN", "", "Bearer token Twitter/X"),
    ("Clés API (non-wizard)", "TWITTER_API_KEY", "", "API key Twitter/X"),
    ("Clés API (non-wizard)", "TWITTER_API_SECRET", "", "API secret Twitter/X"),
    ("Clés API (non-wizard)", "TWITTER_ACCESS_TOKEN", "", "Access token Twitter/X"),
    ("Clés API (non-wizard)", "TWITTER_ACCESS_TOKEN_SECRET", "", "Access token secret Twitter/X"),
    ("Clés API (non-wizard)", "SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback", "URI redirect OAuth Spotify"),
    ("Clés API (non-wizard)", "MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1", "URL base API Moonshot/Kimi"),
    ("Clés API (non-wizard)", "OLLAMA_HOST", "http://localhost:11434", "URL Ollama (legacy, utiliser LUMENA_OLLAMA_HOST)"),
    ("Clés API (non-wizard)", "GEMINI_API_KEY", "", "Alias GOOGLE_API_KEY (Gemini)"),
    ("Clés API (non-wizard)", "DEFAULT_MODEL", "", "Alias legacy modèle par défaut"),
    # --- PLAN_SUPREME_CODEAGENT — Feature flags (P0-P11) ---
    # Tous opt-OUT (default=true), DESTRUCTIVE_CONFIRM unique opt-IN.
    ("CodeAgent (flags)", "LUMENA_PROVIDER_PROMPTS", "true", "P0 — Prompts adaptés par provider"),
    ("CodeAgent (flags)", "LUMENA_TOOL_HINTS", "true", "P0b — Descriptions détaillées par outil"),
    ("CodeAgent (flags)", "LUMENA_FUZZY_REPLACE", "true", "P1 — Fuzzy matching multi-passes str_replace/apply_patch"),
    ("CodeAgent (flags)", "LUMENA_COMPACTION_PRUNE", "true", "P2 — Compaction progressive avec pruning intelligent"),
    ("CodeAgent (flags)", "LUMENA_PLAN_MODE", "true", "P3 — Mode plan (read-only puis exécution)"),
    ("CodeAgent (flags)", "LUMENA_TRUNCATION_SAVE", "true", "P4 — Sauvegarde outputs tronqués dans data/logs/codeagent/"),
    ("CodeAgent (flags)", "LUMENA_MAX_STEPS_GRACEFUL", "true", "P5 — Escalation graceful avant max_iter"),
    ("CodeAgent (flags)", "LUMENA_AUTO_FORMAT", "true", "P6 — Auto-format (ruff/prettier) post-edit"),
    ("CodeAgent (flags)", "LUMENA_REACT_QUALITY_GATES", "true", "P7 — Quality gates ReAct (parité CodeAgent)"),
    ("CodeAgent (flags)", "LUMENA_DID_YOU_MEAN", "true", "P8 — Suggestion outil similaire si nom invalide"),
    ("CodeAgent (flags)", "LUMENA_MODEL_TEMPERATURES", "true", "P8 — Températures adaptées par provider"),
    ("CodeAgent (flags)", "LUMENA_COMPACTION_REPLAY", "true", "P8 — Replay derniers turns après compaction"),
    ("CodeAgent (flags)", "LUMENA_INVALID_TOOL_CATCH", "true", "P8 — Catch + correction outils inconnus"),
    ("CodeAgent (flags)", "LUMENA_CRLF_NORMALIZE", "true", "P8 — Normalisation CR/LF Windows<->Unix"),
    ("CodeAgent (flags)", "LUMENA_ENV_CONTEXT", "true", "P8 — Inject OS/shell/cwd dans system prompt"),
    ("CodeAgent (flags)", "LUMENA_SSE_TIMEOUT", "true", "P8 — Timeout adaptatif streaming SSE"),
    ("CodeAgent (flags)", "LUMENA_PROMPT_CACHE", "true", "P8 — Prompt caching Anthropic/DeepSeek"),
    ("CodeAgent (flags)", "LUMENA_CODING_METRICS", "true", "P10 — Métriques coding -> data/logs/codeagent/metrics.jsonl"),
    ("CodeAgent (flags)", "LUMENA_DESTRUCTIVE_CONFIRM", "false", "P11 — OPT-IN: confirmation rm/git push --force/etc."),
    ("CodeAgent (flags)", "LUMENA_FRENCH_ERRORS", "true", "P11 — Messages d'erreur en français"),
]


def _comment_lines(entry: dict) -> list[str]:
    comments = [f"# {entry['label']}"]
    hint = entry.get("hint")
    if hint:
        comments.append(f"# {hint}")
    comments.append(f"# Type: {entry['type']} | Niveau: {entry.get('level', 'avancé')}")
    if entry.get("restart"):
        comments.append("# Redémarrage requis après modification")
    options = entry.get("options")
    if options:
        comments.append("# Options: " + ", ".join(str(option) for option in options))
    return comments


def render_env_example() -> str:
    # -- Keys already in schema (avoid duplicates in extras) --
    schema_keys = {e["key"] for e in _CONFIG_SCHEMA}

    groups: dict[str, list[dict]] = defaultdict(list)
    for entry in _CONFIG_SCHEMA:
        groups[entry["group"]].append(entry)

    lines = [
        "# Auto-generated from web.routes.config._CONFIG_SCHEMA + extras",
        "# Do not edit manually; run: .\\venv\\Scripts\\python.exe scripts\\sync_env_example.py",
        "",
    ]
    for group in sorted(groups):
        lines.append(f"# === {group} ===")
        for entry in groups[group]:
            lines.extend(_comment_lines(entry))
            lines.append(f"{entry['key']}={entry.get('default', '')}")
            lines.append("")

    # -- Extra env vars (not in wizard, but used in code) --
    extra_groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for grp, key, default, desc in _EXTRA_ENV_DOCS:
        if key not in schema_keys:
            extra_groups[grp].append((key, default, desc))

    if extra_groups:
        lines.append("")
        lines.append("#" + "=" * 70)
        lines.append("# VARIABLES AVANCÉES (non présentes dans le wizard)")
        lines.append("# Valeurs par défaut — modifier uniquement si nécessaire.")
        lines.append("#" + "=" * 70)
        lines.append("")
        for grp in sorted(extra_groups):
            lines.append(f"# --- {grp} ---")
            for key, default, desc in extra_groups[grp]:
                lines.append(f"# {desc}")
                lines.append(f"# {key}={default}")
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    TARGET.write_text(render_env_example(), encoding="utf-8")
    print(f"Updated {TARGET}")


if __name__ == "__main__":
    main()
