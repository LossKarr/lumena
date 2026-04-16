"""Configuration (.env) management and alerts routes."""
from __future__ import annotations
import json as _json
import os
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from filelock import FileLock
from loguru import logger

from web.routes.deps import verify_admin_token

from src.utils.paths import ROOT_DIR, IDENTITY_JSON, MEMORY_MD, DATA_DIR, ALERTS_DIR
from src.llm.providers import AVAILABLE_MODELS as _AVAILABLE_MODELS

_PROJECT_ROOT = ROOT_DIR
_ENV_WRITE_LOCK = threading.Lock()  # sérialise toutes les écritures .env
_ENV_FILE_LOCK = DATA_DIR / ".env.lock"
_ENV_BACKUP_DIR = DATA_DIR / "env_backups"

router = APIRouter()

_CONFIG_SCHEMA: list[dict] = [
    {"key": "LUMENA_DEFAULT_MODEL", "label": "Modèle par défaut", "group": "LLM", "type": "select",
     "options": [k for k, m in _AVAILABLE_MODELS.items() if not m.supports_image_generation],
     "default": "deepseek-v3",
     "hint": "Modèle LLM utilisé par défaut pour toutes les requêtes. DeepSeek-V3 offre le meilleur rapport qualité/prix. Les cerveaux spécialisés ci-dessous peuvent surcharger ce choix."},
    # ── Cerveaux Spécialisés ───────────────────────────────────────────────────────────
    {"key": "LUMENA_BRAIN_VISION", "label": "Cerveau Vision (analyse images)",
     "group": "Cerveaux Sp\u00e9cialis\u00e9s", "type": "select",
     "options": ["auto", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-4o", "gpt-4o-mini",
                 "o3", "o4-mini",
                 "claude-opus-4.7", "claude-opus-4.6", "claude-sonnet-4.6", "claude-sonnet-4.5", "claude-haiku-4.5",
                 "gemini-3.1-pro", "gemini-2.5-pro", "gemini-2.5-flash",
                 "grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning", "grok-4-1-fast-reasoning"],
     "default": "auto",
     "hint": "auto = meilleur mod\u00e8le disponible avec support vision (OpenAI/Anthropic/Google/Grok)"},
    {"key": "LUMENA_BRAIN_CODE", "label": "Cerveau Code (analyse et g\u00e9n\u00e9ration)",
     "group": "Cerveaux Sp\u00e9cialis\u00e9s", "type": "select",
     "options": ["auto", "gpt-5.4", "gpt-5.4-mini", "gpt-4o", "gpt-4o-mini",
                 "o3", "o4-mini",
                 "claude-opus-4.7", "claude-opus-4.6", "claude-sonnet-4.6", "grok-code-fast-1",
                 "deepseek-v3", "deepseek-reasoner", "gemini-3.1-pro", "gemini-2.5-pro",
                 "nvidia-glm-4.7", "nvidia-minimax-m2.5", "nvidia-deepseek-v3.2",
                 "minimax-m2.5", "minimax-m2.7",
                 "grok-4.20-0309-reasoning", "grok-4.20-multi-agent-0309"],
     "default": "auto",
     "hint": "auto = meilleur mod\u00e8le code disponible (score HumanEval/SWE-bench)"},
    {"key": "LUMENA_BRAIN_WEB", "label": "Cerveau Web (recherche et analyse web)",
     "group": "Cerveaux Sp\u00e9cialis\u00e9s", "type": "select",
     "options": ["auto", "gpt-5.4", "gemini-3.1-pro", "gemini-2.5-pro", "gemini-2.5-flash",
                 "claude-sonnet-4.6", "claude-opus-4.7", "claude-opus-4.6", "gpt-4o-mini",
                 "grok-4.20-0309-reasoning", "grok-4.20-multi-agent-0309",
                 "kimi-k2.5", "deepseek-v3", "minimax-m2.5"],
     "default": "auto",
     "hint": "auto = meilleur mod\u00e8le disponible pour la recherche et analyse web"},
    {"key": "LUMENA_BRAIN_IMAGE_GEN", "label": "Mod\u00e8le g\u00e9n\u00e9ration d'images",
     "group": "Cerveaux Sp\u00e9cialis\u00e9s", "type": "select",
     "options": ["auto", "dall-e-3"],
     "default": "auto",
     "hint": "DALL-E 3 via OpenAI API (cl\u00e9 OPENAI_API_KEY requise)"},    {"key": "LUMENA_REACT_HISTORY_OBS_CHARS", "label": "Historique observations (chars)", "group": "LLM", "type": "number", "default": "8000", "min": 1000, "max": 50000,
     "hint": "Nombre max de caractères conservés par observation dans l'historique ReAct. Plus haut = plus de contexte, plus de tokens consommés."},
    {"key": "LUMENA_MAX_REACT_ITERATIONS", "label": "Max itérations ReAct", "group": "LLM", "type": "number", "default": "25", "min": 1, "max": 100,
     "hint": "Nombre maximum de cycles THOUGHT→ACTION→OBSERVATION avant arrêt forcé. 25 convient pour la majorité des tâches."},
    {"key": "LUMENA_REACT_TIMEOUT", "label": "Timeout global ReAct (sec)", "group": "LLM", "type": "number", "default": "3600", "min": 60, "max": 86400,
     "hint": "Durée max d'une boucle ReAct complète en secondes. 3600 = 1 heure. Protège contre les tâches infinies."},
    {"key": "LUMENA_TASK_STEP_TIMEOUT_SEC", "label": "Timeout étape externe (0=off)", "group": "LLM", "type": "number", "default": "0", "min": 0, "max": 3600,
     "hint": "Timeout par étape individuelle d'une tâche déléguée. 0 = pas de limite. Utile pour les tâches planifiées."},
    {"key": "LUMENA_TASK_STEP_TIMEOUT_RETRIES", "label": "Retries par étape", "group": "LLM", "type": "number", "default": "1", "min": 0, "max": 10,
     "hint": "Nombre de tentatives si une étape échoue par timeout. 1 = un seul essai."},
    {"key": "LUMENA_PARALLEL_TOOL_MAX_CALLS", "label": "Max outils en parallèle", "group": "LLM", "type": "number", "default": "20", "min": 1, "max": 100,
     "hint": "Nombre max d'outils exécutés simultanément via parallel_tools. 20 = défaut. Lumena choisit librement quels outils paralléliser (seule la récursion est interdite)."},
    {"key": "LUMENA_AUTONOMY_EXECUTE_ACTIONS", "label": "Exécution autonome", "group": "Autonomie", "type": "bool", "default": "1",
     "hint": "Active l'exécution réelle des actions autonomes (daemon). Désactiver = mode observation seule."},
    {"key": "LUMENA_AUTONOMY_PROGRESSIVE_MODE", "label": "Mode progressif", "group": "Autonomie", "type": "bool", "default": "1",
     "hint": "Limite les actions autonomes à la liste autorisée + budget horaire. Recommandé en production."},
    {"key": "LUMENA_AUTONOMY_ALLOWED_ACTIONS", "label": "Actions autorisées", "group": "Autonomie", "type": "text", "default": "EXPLORE_WEB,LEARN_SOMETHING,REFLECT,WRITE_DIARY,CHECK_NEWS",
     "hint": "Liste des types d'actions que le daemon peut exécuter en mode progressif. Séparées par des virgules."},
    {"key": "LUMENA_AUTONOMY_MAX_ACTIONS_PER_HOUR", "label": "Budget actions/heure", "group": "Autonomie", "type": "number", "default": "12", "min": 1, "max": 1000,
     "hint": "Nombre max d'actions autonomes par heure glissante. Protège contre l'emballement du daemon."},
    {"key": "LUMENA_AUTONOMY_ACTION_TIMEOUT_SEC", "label": "Timeout action (sec)", "group": "Autonomie", "type": "number", "default": "180", "min": 10, "max": 3600,
     "hint": "Durée max d'une action autonome individuelle. 180s = 3 minutes."},
    {"key": "LUMENA_AUTONOMY_GOAL_COOLDOWN_SEC", "label": "Cooldown objectif (sec)", "group": "Autonomie", "type": "number", "default": "60", "min": 0, "max": 3600,
     "hint": "Délai minimum entre deux exécutions du même objectif. Empêche la répétition en boucle."},
    {"key": "LUMENA_AUTONOMY_GOAL_MAX_FAILURES", "label": "Max échecs objectif", "group": "Autonomie", "type": "number", "default": "5", "min": 1, "max": 100,
     "hint": "Nombre d'échecs consécutifs avant de mettre en pause un objectif. Protège contre les boucles d'erreur."},
    {"key": "LUMENA_VOICE_AUTO", "label": "Auto-démarrage voix", "group": "Voix", "type": "bool", "default": "0",
     "hint": "Démarre automatiquement l'écoute micro au lancement. 0 = activation manuelle uniquement."},
    {"key": "LUMENA_TTS_AUTO", "label": "TTS automatique", "group": "Voix", "type": "bool", "default": "0",
     "hint": "Lit automatiquement les réponses à voix haute. 0 = texte uniquement sauf demande explicite."},
    {"key": "LUMENA_TTS_MODE", "label": "Mode TTS (fast/premium/offline)", "group": "Voix", "type": "select", "options": ["fast", "premium", "offline"], "default": "premium",
     "hint": "fast = pyttsx3 local rapide | premium = edge-tts Microsoft (meilleure qualité) | offline = Piper ONNX local"},
    {"key": "LUMENA_STT_MODEL", "label": "Modèle Whisper", "group": "Voix", "type": "select", "options": ["tiny", "base", "small", "medium", "large-v3-turbo"], "default": "large-v3-turbo",
     "hint": "Taille du modèle Whisper pour la reconnaissance vocale. Plus gros = plus précis mais plus lent et plus de VRAM."},
    {"key": "LUMENA_VOICE_CONV_TIMEOUT", "label": "Timeout conversation (sec)", "group": "Voix", "type": "number", "default": "45", "min": 10, "max": 300,
     "hint": "Durée max d'écoute vocale continue avant arrêt automatique. 45s = par défaut."},
    {"key": "LUMENA_TTS_TELEGRAM", "label": "TTS Telegram", "group": "Voix", "type": "bool", "default": "0",
     "hint": "Envoie les réponses en message vocal Telegram en plus du texte."},
    {"key": "LUMENA_TTS_WHATSAPP", "label": "TTS WhatsApp", "group": "Voix", "type": "bool", "default": "0",
     "hint": "Envoie les réponses en message vocal WhatsApp en plus du texte."},
    {"key": "LUMENA_DISABLE_TELEGRAM", "label": "Désactiver Telegram", "group": "Telegram", "type": "bool", "default": "",
     "hint": "Désactive complètement le canal Telegram au démarrage. Utile si pas de bot configuré."},
    {"key": "LUMENA_WEB_ONLY", "label": "Mode Web uniquement", "group": "Telegram", "type": "bool", "default": "",
     "hint": "Force tous les canaux sur Web uniquement. Désactive Telegram, WhatsApp, Discord et CLI."},
    {"key": "LUMENA_CRITICAL_ALERTS_ENABLED", "label": "Alertes critiques", "group": "Alertes", "type": "bool", "default": "1",
     "hint": "Active le système d'alertes critiques (disk full, provider down, daemon crash). Via Twilio si configuré."},
    {"key": "LUMENA_CRITICAL_SMS_ENABLED", "label": "SMS critiques", "group": "Alertes", "type": "bool", "default": "1",
     "hint": "Envoie les alertes critiques par SMS via Twilio. Nécessite TWILIO_ACCOUNT_SID et AUTH_TOKEN."},
    {"key": "LUMENA_CRITICAL_CALL_ENABLED", "label": "Appels critiques", "group": "Alertes", "type": "bool", "default": "1",
     "hint": "Appelle le numéro d'alerte en cas d'incident critique. Dernier recours après SMS."},
    {"key": "LUMENA_CRITICAL_ALERT_COOLDOWN_SEC", "label": "Cooldown alertes (sec)", "group": "Alertes", "type": "number", "default": "300", "min": 0, "max": 86400,
     "hint": "Délai minimum entre deux alertes du même type. 300s = 5 minutes. Empêche le spam d'alertes."},
    {"key": "LUMENA_ALERT_TO_NUMBER", "label": "Numéro alerte", "group": "Alertes", "type": "text", "default": "",
     "hint": "Numéro de téléphone destination des alertes SMS/appel. Format international : +33612345678"},
    {"key": "LUMENA_ARCHIVE_MAX_AGE_DAYS", "label": "Age max archive (jours)", "group": "Ops", "type": "number", "default": "30", "min": 1, "max": 365,
     "hint": "Les archives (backups, logs anciens) de plus de X jours sont supprimées automatiquement."},
    {"key": "LUMENA_ARCHIVE_MAX_SIZE_GB", "label": "Taille max archives (GB)", "group": "Ops", "type": "number", "default": "10", "min": 1, "max": 1000,
     "hint": "Taille maximale totale du dossier d'archives en Go. Au-delà, les plus anciennes sont purgées."},
    {"key": "LUMENA_OPS_MEMORY_PURGE_ENABLED", "label": "Purge mémoire active", "group": "Ops", "type": "bool", "default": "1",
     "hint": "Active la purge automatique des souvenirs ChromaDB les plus anciens quand la base dépasse le seuil."},
    {"key": "LUMENA_SLO_WORKSPACE_ERRORS_MAX", "label": "Seuil erreurs workspace", "group": "SLO", "type": "number", "default": "999", "min": 1, "max": 99999,
     "hint": "Nombre max d'erreurs tolérées dans le workspace avant déclenchement d'une alerte SLO."},
    # ── Apprentissage Identité ───────────────────────────────────────────────
    {"key": "LUMENA_IDENTITY_LEARNING", "label": "Apprentissage d'identité",
     "group": "Apprentissage", "type": "bool", "default": "1",
     "hint": "Lumena extrait et mémorise automatiquement les faits personnels (prénom, métier, ville…) au fil des conversations."},
    {"key": "LUMENA_IDENTITY_HINT_COOLDOWN", "label": "Cooldown questions identité (sec)",
     "group": "Apprentissage", "type": "number", "default": "300", "min": 0, "max": 86400,
     "hint": "Délai minimum entre deux demandes d'infos manquantes (en secondes). 300 = 5 min."},
    {"key": "LUMENA_SETUP_COMPLETE", "label": "Setup initial terminé", "group": "Système", "type": "bool", "default": "0",
     "hint": "Passe à 1 après le wizard de première installation. Ne pas modifier manuellement sauf pour relancer le setup."},
    {"key": "LUMENA_SANDBOX_MODE", "label": "Mode Sandbox Docker",
     "group": "Système", "type": "select",
     "options": ["auto", "always", "never"], "default": "auto",
     "hint": "auto = commandes système en local + code en Docker | always = tout Docker | never = pas de Docker"},
    {"key": "LUMENA_SANDBOX_MEMORY", "label": "Mémoire conteneur Docker",
     "group": "Système", "type": "text", "default": "512m",
     "hint": "Limite mémoire RAM du conteneur sandbox Docker (ex: 512m, 1g). 512m par défaut."},
    {"key": "LUMENA_USE_EMOJIS", "label": "Utiliser des emojis",
     "group": "Préférences", "type": "bool", "default": "1",
     "hint": "Lumena utilise des emojis dans ses réponses. 1 = oui, 0 = non."},
    {"key": "LUMENA_EMOTION_ENABLED", "label": "Activer le système émotionnel",
     "group": "Préférences", "type": "bool", "default": "1",
     "hint": "Active le système émotionnel complet (PAD, persistance, injection prompt, dashboard). 0 = désactivé."},
    {"key": "LUMENA_DEFAULT_MOOD", "label": "Humeur par défaut",
     "group": "Préférences", "type": "select",
     "options": ["neutral", "happy", "curious", "playful", "excited", "thoughtful", "tired", "bored", "proud", "touched"], "default": "neutral",
     "hint": "Humeur de base de Lumena. Influence son ton et son style de réponse."},
    {"key": "LUMENA_EMOJI_FREQUENCY", "label": "Fréquence des emojis",
     "group": "Préférences", "type": "range", "min": 0, "max": 100, "default": "30",
     "hint": "Pourcentage de messages avec emojis (0 = jamais, 100 = toujours)."},
    {"key": "LUMENA_ENABLED_MOODS", "label": "Humeurs autorisées",
     "group": "Préférences", "type": "text", "default": "",
     "hint": "Liste d'humeurs séparées par virgules (ex: happy,curious,neutral). Vide = toutes."},
    {"key": "LUMENA_EMOTION_DECAY", "label": "Vitesse de decay émotionnel",
     "group": "Préférences", "type": "text", "default": "0.02",
     "hint": "Vitesse de retour vers neutre (0.01 = lent, 0.1 = rapide)."},
    {"key": "LUMENA_EMOTION_LLM_ANALYSIS", "label": "Analyse sentimentale LLM",
     "group": "Préférences", "type": "bool", "default": "1",
     "hint": "Utilise le LLM pour détecter le sentiment des messages (plus précis). 0 = fallback keyword uniquement."},
    {"key": "LUMENA_EMOTION_SENSITIVITY", "label": "Sensibilité émotionnelle",
     "group": "Préférences", "type": "text", "default": "0.5",
     "hint": "Sensibilité émotionnelle (0 = stoïque, 0.5 = normal, 1 = hypersensible). Multiplie les deltas PAD."},
    {"key": "LUMENA_PERSONALITY_PRESET", "label": "Preset personnalité",
     "group": "Préférences", "type": "select",
     "options": ["", "professional", "creative", "companion"], "default": "",
     "hint": "Applique un preset de traits. Les valeurs individuelles LUMENA_TRAIT_* ont priorité."},
    {"key": "LUMENA_DOCUMENT_THEME", "label": "Thème documents",
     "group": "Préférences", "type": "select",
     "options": ["", "corporate", "minimal", "modern", "legal", "creative"], "default": "",
     "hint": "Thème par défaut pour les PDF/DOCX générés. Vide = corporate."},
    {"key": "OPENAI_API_KEY", "label": "OpenAI API Key", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Clé API OpenAI (sk-...). Utilisée pour GPT-5.4, GPT-4o, DALL-E 3. Obtenir sur platform.openai.com"},
    {"key": "ANTHROPIC_API_KEY", "label": "Anthropic API Key", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Clé API Anthropic (sk-ant-...). Utilisée pour Claude Opus/Sonnet/Haiku. Obtenir sur console.anthropic.com"},
    {"key": "GOOGLE_API_KEY", "label": "Google API Key", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Clé API Google AI (AIza...). Utilisée pour Gemini Pro/Flash. Obtenir sur aistudio.google.com"},
    {"key": "DEEPSEEK_API_KEY", "label": "DeepSeek API Key", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Clé API DeepSeek (sk-...). Modèle principal par défaut. ~0.27$/M tokens. Obtenir sur platform.deepseek.com"},
    {"key": "MOONSHOT_API_KEY", "label": "Moonshot (Kimi) API Key", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Clé API Moonshot pour Kimi K2.5. Obtenir sur platform.moonshot.cn"},
    {"key": "XAI_API_KEY", "label": "xAI (Grok) API Key", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Clé API xAI pour les modèles Grok. Obtenir sur console.x.ai"},
    {"key": "NVIDIA_API_KEY", "label": "NVIDIA NIM API Key", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Clé API NVIDIA NIM. Accès à Kimi-K2, DeepSeek-V3, GLM-4 hébergés. Obtenir sur build.nvidia.com"},
    {"key": "MINIMAX_API_KEY", "label": "MiniMax API Key", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Clé API MiniMax. Accès aux modèles M2.1/M2.5/M2.7 natifs. Obtenir sur platform.minimax.io"},
    {"key": "TELEGRAM_TOKEN", "label": "Telegram Bot Token", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Token du bot Telegram Lumena. Créer via @BotFather sur Telegram."},
    {"key": "DISCORD_TOKEN", "label": "Discord Bot Token", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Token du bot Discord Lumena. Créer sur discord.com/developers/applications"},
    {"key": "GITHUB_TOKEN", "label": "GitHub Token", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Personal Access Token GitHub. Permet à Lumena de créer des repos, commits, PRs. Obtenir dans Settings > Developer settings."},
    {"key": "TWILIO_ACCOUNT_SID", "label": "Twilio Account SID", "group": "Clés API", "type": "secret", "default": "",
     "hint": "SID du compte Twilio (AC...). Requis pour les alertes SMS/appel. Visible sur console.twilio.com"},
    {"key": "TWILIO_AUTH_TOKEN", "label": "Twilio Auth Token", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Token d'authentification Twilio. Paire avec ACCOUNT_SID pour les SMS/appels."},
    {"key": "SPOTIFY_CLIENT_ID", "label": "Spotify Client ID", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Client ID de l'app Spotify. Permet de contrôler la lecture musicale. Créer sur developer.spotify.com"},
    {"key": "SPOTIFY_CLIENT_SECRET", "label": "Spotify Client Secret", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Client Secret de l'app Spotify. Paire avec le Client ID."},
    {"key": "NOTION_API_KEY", "label": "Notion Integration Token", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Token d'intégration Notion (ntn_...). Permet lecture/écriture des pages Notion. Créer sur notion.so/my-integrations"},
    {"key": "BRAVE_SEARCH_API_KEY", "label": "Brave Search API Key", "group": "Clés API", "type": "secret", "default": "",
     "hint": "Clé API Brave Search. Moteur de recherche alternatif à DuckDuckGo. Obtenir sur brave.com/search/api"},
    {"key": "LUMENA_EMAIL", "label": "Adresse email de Lumena", "group": "Email", "type": "text", "default": "",
     "hint": "Adresse Gmail utilisée par Lumena pour envoyer et lire des emails. Ex : lumena.ia@gmail.com"},
    {"key": "LUMENA_EMAIL_PASSWORD", "label": "Mot de passe / App Password", "group": "Email", "type": "secret", "default": "",
     "hint": "App Password Gmail (16 caractères sans espaces). Générer dans Google Account > Sécurité > Mots de passe d'application."},
    {"key": "LUMENA_USER_EMAIL", "label": "Ton adresse email principale", "group": "Email", "type": "text", "default": "",
     "hint": "Ton email personnel. Lumena l'utilise pour t'envoyer des rapports et notifications."},
    {"key": "TWILIO_FROM_NUMBER", "label": "Twilio From Number", "group": "Clés API", "type": "text", "default": "",
     "hint": "Numéro Twilio source des SMS/appels. Format international : +1234567890. Visible sur console.twilio.com"},
    {"key": "DISCORD_MAIN_CHANNEL_ID", "label": "Discord Main Channel ID", "group": "Clés API", "type": "text", "default": "",
     "hint": "ID du salon Discord principal où Lumena poste les messages. Clic droit sur le salon > Copier l'identifiant."},
    # ── Stripe ──
    {"key": "STRIPE_API_KEY", "label": "Stripe Secret Key", "group": "Paiements", "type": "secret", "default": "",
     "hint": "Clé secrète Stripe (sk_test_... ou sk_live_...). Permet de créer produits, prix, liens de paiement, gérer clients et abonnements. Obtenir sur dashboard.stripe.com/apikeys"},
    {"key": "STRIPE_WEBHOOK_SECRET", "label": "Stripe Webhook Secret", "group": "Paiements", "type": "secret", "default": "",
     "hint": "Secret de vérification des webhooks Stripe (whsec_...). Requis pour recevoir les événements (paiement réussi, abonnement annulé, etc.). Configurer sur dashboard.stripe.com/webhooks"},
    {"key": "STRIPE_MODE", "label": "Mode Stripe", "group": "Paiements", "type": "select",
     "options": ["test", "live"], "default": "test",
     "hint": "test = mode sandbox (pas de vrais paiements) | live = production (paiements réels). Commencer par test pour valider l'intégration."},
    {"key": "STRIPE_CLI_AUTO", "label": "Stripe CLI Auto-Start", "group": "Paiements", "type": "bool", "default": "1",
     "hint": "Démarre automatiquement `stripe listen` au lancement de Lumena pour recevoir les webhooks en local. Désactiver si Lumena est hébergée avec un vrai domaine."},
    # ── n8n ──
    {"key": "N8N_BASE_URL", "label": "n8n URL", "group": "Automation (n8n)", "type": "text", "default": "http://localhost:5678",
     "hint": "URL de l'instance n8n. Par défaut http://localhost:5678 si lancé en Docker local."},
    {"key": "N8N_API_KEY", "label": "n8n API Key", "group": "Automation (n8n)", "type": "secret", "default": "",
     "hint": "Clé API n8n. Générer dans n8n > Settings > API > Create API Key. Requise pour piloter les workflows."},
    {"key": "N8N_AUTO_START", "label": "Auto-start n8n (Docker)", "group": "Automation (n8n)", "type": "text", "default": "1",
     "hint": "1 = Lumena démarre automatiquement n8n via Docker au boot. 0 = désactivé."},
    # ── Serveur Web ──────────────────────────────────────────────────────────
    {"key": "LUMENA_PORT", "label": "Port du serveur web", "group": "Serveur", "type": "number", "default": "8080", "min": 1024, "max": 65535,
     "restart": True,
     "hint": "Port d'écoute du serveur FastAPI. 8080 par défaut. Nécessite un redémarrage."},
    {"key": "LUMENA_HOST", "label": "Host du serveur web", "group": "Serveur", "type": "text", "default": "0.0.0.0",
     "restart": True,
     "hint": "Adresse d'écoute. 0.0.0.0 = accessible réseau. 127.0.0.1 = local uniquement. Nécessite un redémarrage."},
    {"key": "LUMENA_CORS_ORIGINS", "label": "CORS origins autorisées", "group": "Serveur", "type": "text", "default": "",
     "hint": "Origines CORS supplémentaires séparées par des virgules (ex: http://mon-domaine.fr). Vide = localhost uniquement."},
    {"key": "LUMENA_RATE_EXPENSIVE", "label": "Rate limit requêtes lourdes (req/min)", "group": "Serveur", "type": "number", "default": "20", "min": 1, "max": 10000,
     "hint": "Max requêtes/minute pour /api/chat et /api/upload. 20 par défaut."},
    {"key": "LUMENA_RATE_DEFAULT", "label": "Rate limit requêtes standard (req/min)", "group": "Serveur", "type": "number", "default": "200", "min": 1, "max": 10000,
     "hint": "Max requêtes/minute pour la plupart des endpoints API. 200 par défaut."},
    {"key": "LUMENA_RATE_HEALTH", "label": "Rate limit health/status (req/min)", "group": "Serveur", "type": "number", "default": "600", "min": 1, "max": 10000,
     "hint": "Max requêtes/minute pour /api/health et /api/status. 600 par défaut."},
    # ── Instance multi-Lumena ────────────────────────────────────────────────
    {"key": "LUMENA_INSTANCE_ID", "label": "Instance ID", "group": "Instance", "type": "text", "default": "",
     "hint": "Identifiant unique de cette instance Lumena. Auto-généré si vide. Utile pour distinguer plusieurs instances."},
    {"key": "LUMENA_INSTANCE_NAME", "label": "Nom de l'instance", "group": "Instance", "type": "text", "default": "Lumena",
     "hint": "Nom affiché pour cette instance (ex: Lumena-Pro, Lumena-Home). Utilisé dans les logs et le panel."},
    {"key": "LUMENA_DATA_DIR", "label": "Dossier data", "group": "Instance", "type": "text", "default": "./data",
     "restart": True,
     "hint": "Chemin absolu ou relatif du dossier data. Utile pour isoler plusieurs instances. Nécessite un redémarrage."},
    {"key": "LUMENA_WORKSPACE_DIR", "label": "Dossier workspace", "group": "Instance", "type": "text", "default": "./workspace",
     "restart": True,
     "hint": "Dossier où Lumena crée les projets. Nécessite un redémarrage."},
    {"key": "LUMENA_PUBLIC_BASE_URL", "label": "URL publique de l'instance", "group": "Instance", "type": "text", "default": "",
     "hint": "URL publique complète (ex: http://localhost:8080). Auto-dérivée de LUMENA_PORT si vide. Utile pour multi-instance."},
    {"key": "LUMENA_UPLOADS_DIR", "label": "Dossier uploads/documents reçus", "group": "Instance", "type": "text", "default": "",
     "restart": True,
     "hint": "Dossier de réception des fichiers (images, documents). Défaut : data/received_documents. Nécessite un redémarrage."},
    # ── Ollama ───────────────────────────────────────────────────────────────
    {"key": "LUMENA_OLLAMA_HOST", "label": "URL Ollama", "group": "LLM", "type": "text", "default": "http://localhost:11434",
     "hint": "URL du serveur Ollama local. Changer si Ollama tourne sur un autre port ou une autre machine."},
    # ── Browser ──────────────────────────────────────────────────────────────
    {"key": "LUMENA_BROWSER_HEADLESS", "label": "Browser headless", "group": "Browser", "type": "bool", "default": "1",
     "hint": "1 = navigateur invisible (recommandé). 0 = afficher la fenêtre (debug)."},
    {"key": "LUMENA_BROWSER_LOCALE", "label": "Locale navigateur", "group": "Browser", "type": "text", "default": "fr_FR",
     "hint": "Locale du navigateur Playwright (ex: fr_FR, en_US). Influence la langue des sites visités."},
    {"key": "LUMENA_BROWSER_MAX_TABS", "label": "Max onglets simultanés", "group": "Browser", "type": "number", "default": "5", "min": 1, "max": 20,
     "hint": "Nombre maximum d'onglets Playwright ouverts en parallèle. 5 par défaut."},
    # ── Computer Use ─────────────────────────────────────────────────────────
    {"key": "LUMENA_EXECUTION_MODE", "label": "Mode d'exécution CU", "group": "Computer Use",
     "type": "select", "default": "hybrid",
     "options": ["cloud", "hybrid", "local"],
     "hint": "cloud = providers cloud prioritaires, hybrid = cloud + local en fallback, local = sans providers cloud (LLM local Ollama + OCR uniquement, navigation web toujours possible)."},
    {"key": "LUMENA_CU_VISION_ORDER", "label": "Ordre vision CU", "group": "Computer Use",
     "type": "text", "default": "",
     "hint": "Override de la policy vision (ex: anthropic,google,openai). Vide = policy défaut selon le mode."},
    {"key": "LUMENA_CU_OLLAMA_VISION", "label": "Vision Ollama en mode cloud/hybrid", "group": "Computer Use",
     "type": "bool", "default": "0",
     "hint": "Ajoute Ollama en queue de la cascade vision même en mode cloud ou hybrid. Toujours actif en local."},
    {"key": "LUMENA_CU_MAX_ITERATIONS", "label": "Max itérations agent CU", "group": "Computer Use",
     "type": "number", "default": "30", "min": 5, "max": 100,
     "hint": "Nombre maximum de tours de boucle pour l'agent Computer Use autonome."},
    {"key": "LUMENA_CU_TIMEOUT_SEC", "label": "Timeout agent CU (secondes)", "group": "Computer Use",
     "type": "number", "default": "600", "min": 60, "max": 3600,
     "hint": "Timeout global de l'agent Computer Use en secondes (600 = 10 min)."},
    {"key": "REMOTION_LICENSE_KEY", "label": "Remotion License Key", "group": "Vidéo", "type": "secret", "default": "",
     "hint": "Optionnel. Gratuit pour individus et orgas ≤3 personnes. Requis pour entreprises >3. Obtenir sur remotion.pro. Laisser vide = mode gratuit."},
    {"key": "LUMENA_VIDEO_DOCKER_IMAGE", "label": "Image Docker vidéo", "group": "Vidéo", "type": "text", "default": "node:20-slim",
     "hint": "Image Docker pour la génération vidéo Remotion. Doit contenir Node.js ≥16 et npm."},
    {"key": "LUMENA_VIDEO_RENDER_TIMEOUT", "label": "Timeout rendu vidéo (sec)", "group": "Vidéo", "type": "number", "default": "300",
     "hint": "Timeout maximum pour le rendu d'une vidéo. Augmenter pour les vidéos longues (>60s)."},
    {"key": "LUMENA_VIDEO_GPU", "label": "Accélération GPU vidéo", "group": "Vidéo", "type": "bool", "default": "0",
     "hint": "Activer le rendu GPU (NVIDIA). Nécessite nvidia-container-toolkit + Docker runtime nvidia. Accélère le rendu 3-5×."},
    # ── WhatsApp (Meta Cloud API) ──
    {"key": "WHATSAPP_ACCESS_TOKEN", "label": "WhatsApp Access Token", "type": "secret", "group": "WhatsApp",
     "hint": "Token permanent (System User recommandé). Depuis developers.facebook.com", "default": ""},
    {"key": "WHATSAPP_PHONE_NUMBER_ID", "label": "WhatsApp Phone Number ID", "type": "text", "group": "WhatsApp",
     "hint": "ID du numéro Business (pas le numéro lui-même). Visible dans le dashboard Meta.", "default": ""},
    {"key": "WHATSAPP_VERIFY_TOKEN", "label": "WhatsApp Verify Token", "type": "text", "group": "WhatsApp",
     "hint": "Token arbitraire choisi par toi pour la vérification webhook Meta.", "default": "lumena_wa_verify"},
    {"key": "WHATSAPP_APP_SECRET", "label": "WhatsApp App Secret", "type": "secret", "group": "WhatsApp",
     "hint": "App Secret (optionnel mais recommandé pour HMAC signature). Settings > Basic dans l'app Facebook.", "default": ""},
    {"key": "WHATSAPP_OWNER_PHONE", "label": "Numéro owner WhatsApp", "type": "text", "group": "WhatsApp",
     "hint": "Numéro du propriétaire au format international (ex: 33612345678). Pour les alertes proactives.", "default": ""},
    {"key": "LUMENA_DISABLE_WHATSAPP", "label": "Désactiver WhatsApp", "type": "bool", "group": "WhatsApp",
     "hint": "Mettre à 1 pour désactiver le channel WhatsApp (désactivé par défaut — credentials Meta requis).", "default": "1"},
    # ── IONOS (Hébergement) ──
    {"key": "LUMENA_IONOS_DEFAULT_SITE", "label": "Site IONOS par défaut",
     "group": "IONOS (Hébergement)", "type": "text", "default": "",
     "hint": "Domaine par défaut pour les déploiements (ex: lumena.fr). Vide = demander à chaque fois."},
    {"key": "LUMENA_IONOS_BACKUP_BEFORE_DEPLOY", "label": "Backup avant déploiement",
     "group": "IONOS (Hébergement)", "type": "bool", "default": "1",
     "hint": "Télécharge les fichiers existants en backup local avant chaque déploiement."},
    {"key": "LUMENA_IONOS_MAX_UPLOAD_MB", "label": "Taille max upload (Mo)",
     "group": "IONOS (Hébergement)", "type": "number", "default": "100", "min": 1, "max": 5000,
     "hint": "Taille maximale totale d'un déploiement en Mo. Protège contre les uploads accidentels."},
]

# ── P3.3 restart flags manquants ─────────────────────────────────────────────
for _e in _CONFIG_SCHEMA:
    if _e["key"] in {"LUMENA_STT_MODEL", "LUMENA_TTS_MODE", "LUMENA_BROWSER_HEADLESS"}:
        _e["restart"] = True

# ── P3.4 niveaux simple / avancé / expert ────────────────────────────────────
_SIMPLE_KEYS: frozenset = frozenset({
    "LUMENA_DEFAULT_MODEL",
    "LUMENA_BRAIN_VISION", "LUMENA_BRAIN_CODE", "LUMENA_BRAIN_WEB", "LUMENA_BRAIN_IMAGE_GEN",
    "LUMENA_AUTONOMY_EXECUTE_ACTIONS", "LUMENA_AUTONOMY_PROGRESSIVE_MODE",
    "LUMENA_AUTONOMY_ALLOWED_ACTIONS", "LUMENA_AUTONOMY_MAX_ACTIONS_PER_HOUR",
    "LUMENA_VOICE_AUTO", "LUMENA_TTS_AUTO", "LUMENA_TTS_MODE", "LUMENA_STT_MODEL",
    "LUMENA_DISABLE_TELEGRAM", "LUMENA_DISABLE_WHATSAPP", "LUMENA_WEB_ONLY",
    "LUMENA_CRITICAL_ALERTS_ENABLED",
    "LUMENA_IDENTITY_LEARNING",
    "LUMENA_USE_EMOJIS", "LUMENA_DEFAULT_MOOD",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY", "XAI_API_KEY", "NVIDIA_API_KEY", "MINIMAX_API_KEY",
    "TELEGRAM_TOKEN", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET", "DISCORD_TOKEN", "GITHUB_TOKEN",
    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
    "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET",
    "NOTION_API_KEY", "BRAVE_SEARCH_API_KEY",
    "LUMENA_EMAIL", "LUMENA_EMAIL_PASSWORD", "LUMENA_USER_EMAIL",
    "STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_MODE", "STRIPE_CLI_AUTO",
    "N8N_BASE_URL", "N8N_API_KEY", "N8N_AUTO_START",
})
_EXPERT_KEYS: frozenset = frozenset({
    "LUMENA_ARCHIVE_MAX_AGE_DAYS", "LUMENA_ARCHIVE_MAX_SIZE_GB", "LUMENA_OPS_MEMORY_PURGE_ENABLED",
    "LUMENA_SLO_WORKSPACE_ERRORS_MAX",
    "LUMENA_IDENTITY_HINT_COOLDOWN",
    "LUMENA_DATA_DIR", "LUMENA_WORKSPACE_DIR", "LUMENA_PUBLIC_BASE_URL", "LUMENA_UPLOADS_DIR",
    "LUMENA_INSTANCE_ID",
    "LUMENA_SETUP_COMPLETE", "LUMENA_SANDBOX_MODE",
})
for _e in _CONFIG_SCHEMA:
    if _e["key"] in _SIMPLE_KEYS:
        _e["level"] = "simple"
    elif _e["key"] in _EXPERT_KEYS:
        _e["level"] = "expert"
    else:
        _e["level"] = "avancé"


def _read_env_file() -> dict[str, str]:
    env_path = _PROJECT_ROOT / ".env"
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        # Strip quotes added by _dotenv_quote / python-dotenv
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        result[key.strip()] = val
    return result


def _read_rules_fallbacks() -> dict[str, str]:
    """Lit .lumena_rules et data/ pour pré-remplir les champs manquants du .env."""
    fallbacks: dict[str, str] = {}
    rules_path = _PROJECT_ROOT / ".lumena_rules"
    if rules_path.exists():
        import re
        text = rules_path.read_text(encoding="utf-8", errors="replace")
        # Email de Lumena
        m = re.search(r"email:\s*[\"']?([\w.\-+]+@[\w.\-]+\.[a-z]{2,})[\"']?", text)
        if m:
            fallbacks["LUMENA_EMAIL"] = m.group(1)
        # Discord guild_id
        m = re.search(r"guild_id=[\"']?(\d{15,20})[\"']?", text)
        if m:
            fallbacks.setdefault("DISCORD_GUILD_ID", m.group(1))

    # DISCORD_MAIN_CHANNEL_ID depuis DISCORD_GLOBAL_CHANNEL_ID si présent dans .env
    env = _read_env_file()
    if not env.get("DISCORD_MAIN_CHANNEL_ID") and env.get("DISCORD_GLOBAL_CHANNEL_ID"):
        fallbacks["DISCORD_MAIN_CHANNEL_ID"] = env["DISCORD_GLOBAL_CHANNEL_ID"]

    # LUMENA_EMAIL_PASSWORD depuis GMAIL_APP_PASSWORD si présent dans .env
    if not env.get("LUMENA_EMAIL_PASSWORD") and env.get("GMAIL_APP_PASSWORD"):
        fallbacks["LUMENA_EMAIL_PASSWORD"] = env["GMAIL_APP_PASSWORD"]

    # Email utilisateur depuis data/memory ou instincts
    if not env.get("LUMENA_USER_EMAIL"):
        import re
        for candidate in [
            IDENTITY_JSON,
            MEMORY_MD,
        ]:
            if candidate.exists():
                txt = candidate.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"([\w.+\-]+@[\w.\-]+\.[a-z]{2,})", txt, re.IGNORECASE)
                if m:
                    fallbacks["LUMENA_USER_EMAIL"] = m.group(1)
                    break

    return fallbacks


def _dotenv_quote(val: str) -> str:
    """Quote a value for .env if it contains chars that python-dotenv misinterprets."""
    if not val:
        return val
    needs = any(c in val for c in '#\\\'"\n\r\t') or val != val.strip()
    if not needs:
        return val
    escaped = val.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
    return f'"{escaped}"'


def _write_env_values(updates: dict[str, str]) -> None:
    import shutil
    env_path = _PROJECT_ROOT / ".env"
    tmp_path    = env_path.parent / (env_path.name + ".tmp")
    _ENV_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    _ENV_FILE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with _ENV_WRITE_LOCK, FileLock(str(_ENV_FILE_LOCK), timeout=10):
        if not env_path.exists():
            env_path.touch()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = _ENV_BACKUP_DIR / f".env.{timestamp}"
        shutil.copy2(env_path, backup_path)
        try:
            lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
            remaining = dict(updates)
            new_lines: list[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in remaining:
                        new_lines.append(f"{key}={_dotenv_quote(remaining.pop(key))}")
                        continue
                elif stripped.startswith("#") and "=" in stripped:
                    # Match commented-out lines like "# KEY=" or "# KEY=value"
                    uncommented = stripped.lstrip("#").strip()
                    key = uncommented.split("=", 1)[0].strip()
                    if key in remaining:
                        new_lines.append(f"{key}={_dotenv_quote(remaining.pop(key))}")
                        continue
                new_lines.append(line)
            for key, val in remaining.items():
                new_lines.append(f"{key}={_dotenv_quote(val)}")
            content = "\n".join(new_lines) + "\n"
            if not content.strip():
                raise ValueError("Contenu .env vide après merge — abandon")
            tmp_path.write_text(content, encoding="utf-8")
            written = tmp_path.read_text(encoding="utf-8")
            for k in updates:
                if k not in written:
                    raise ValueError(f"Vérification échouée : clé {k} absente du fichier temporaire")
            tmp_path.replace(env_path)
            existing = sorted(_ENV_BACKUP_DIR.glob(".env.*"), key=lambda p: p.stat().st_mtime)
            for old in existing[:-10]:
                old.unlink(missing_ok=True)
        except Exception:
            if backup_path.exists():
                shutil.copy2(backup_path, env_path)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise


@router.get("/api/config", dependencies=[Depends(verify_admin_token)])
async def get_config():
    env_vals = _read_env_file()
    fallbacks = _read_rules_fallbacks()
    # Merge: .env prime sur fallbacks
    merged = {**fallbacks, **env_vals}
    items = []
    for schema in _CONFIG_SCHEMA:
        raw = merged.get(schema["key"], schema["default"])
        if schema["type"] == "secret" and raw:
            masked = "*" * max(0, len(raw) - 4) + raw[-4:] if len(raw) > 4 else "*" * len(raw)
            items.append({**schema, "value": masked, "has_value": True, "length": len(raw)})
        else:
            items.append({**schema, "value": raw, "has_value": bool(raw)})
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(item["group"], []).append(item)
    return {"success": True, "items": items, "groups": groups}


@router.get("/api/config/reveal", dependencies=[Depends(verify_admin_token)])
async def reveal_config_secret(key: str):
    allowed = {s["key"] for s in _CONFIG_SCHEMA if s["type"] == "secret"}
    if key not in allowed:
        raise HTTPException(status_code=403, detail="Clé non autorisée")
    env_vals = _read_env_file()
    return {"success": True, "key": key, "value": env_vals.get(key, "")}


@router.put("/api/config", dependencies=[Depends(verify_admin_token)])
async def update_config(request: Request):
    body = await request.json()
    updates: dict[str, str] = body.get("updates", {})
    if not updates:
        return {"success": False, "error": "Aucune mise à jour fournie"}
    allowed_keys = {s["key"] for s in _CONFIG_SCHEMA}
    filtered = {k: str(v) for k, v in updates.items() if k in allowed_keys}
    if not filtered:
        return {"success": False, "error": "Aucune clé valide trouvée"}
    # P3.1 — Validation min/max pour les champs number
    schema_map = {s["key"]: s for s in _CONFIG_SCHEMA}
    errors: list[str] = []
    for k, v in filtered.items():
        entry = schema_map.get(k, {})
        if entry.get("type") == "number" and "min" in entry:
            try:
                val = int(v)
                if not (entry["min"] <= val <= entry["max"]):
                    errors.append(f"{k}: valeur hors limites ({entry['min']}–{entry['max']})")
            except (ValueError, TypeError):
                errors.append(f"{k}: valeur non numérique")
    if errors:
        return {"success": False, "error": " | ".join(errors)}
    try:
        _write_env_values(filtered)
    except Exception as exc:
        logger.error(f"[config] Erreur écriture .env (rollback effectué) : {exc}")
        return {"success": False, "error": f"Erreur écriture .env : {exc}"}
    for k, v in filtered.items():
        os.environ[k] = v
    # Si l'email Lumena a été modifié, régénérer accounts.json (SMTP/IMAP auto-detect)
    if "LUMENA_EMAIL" in filtered and filtered["LUMENA_EMAIL"].strip():
        try:
            from web.routes.setup import _write_email_account
            _write_email_account(filtered["LUMENA_EMAIL"].strip())
        except Exception as e:
            logger.warning(f"[config] accounts.json non mis à jour: {e}")
    # P3.3 — Signaler si un redémarrage est requis
    needs_restart = any(schema_map.get(k, {}).get("restart", False) for k in filtered)
    note = "Redémarrage requis pour appliquer certains changements." if needs_restart else "Changements appliqués."
    return {"success": True, "updated": list(filtered.keys()), "needs_restart": needs_restart, "note": note}



@router.get("/api/alerts", dependencies=[Depends(verify_admin_token)])
async def get_alerts(limit: int = 50):
    _data_dir = DATA_DIR
    alerts = []
    alert_path = ALERTS_DIR / "critical_alerts.log"
    if alert_path.exists():
        for line in alert_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                a = _json.loads(line)
                a["source"] = "alert_log"
                alerts.append(a)
            except Exception:
                pass
    ops_state_path = _data_dir / "ops" / "ops_state.json"
    if ops_state_path.exists():
        try:
            ops_state = _json.loads(ops_state_path.read_text(encoding="utf-8", errors="replace"))
            for inc in ops_state.get("incidents_today", []):
                alerts.append({
                    "ts": inc.get("time", ""),
                    "severity": inc.get("status", "warning"),
                    "channel": "daemon",
                    "message": " | ".join(inc.get("alerts", [])),
                    "ok": False,
                    "source": "daemon_incident",
                })
        except Exception as e:
            logger.warning("alerts: ops_state read error: {}", e)
    alerts.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return {"success": True, "alerts": alerts[:limit], "total": len(alerts)}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
