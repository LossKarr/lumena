"""
Setup wizard routes — iOS-style first-launch configuration.

Endpoints:
  GET  /api/setup/status   → {needsSetup: bool, preview: bool}
  POST /api/setup/complete  → writes .env values (blocked in preview mode)
  POST /api/setup/test-key  → validates an API key works

The wizard shows when LUMENA_SETUP_COMPLETE is absent or "0" in .env.
Preview mode (?preview=1) lets the dev test the wizard UI without writing.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from loguru import logger

from web.routes.config import _CONFIG_SCHEMA, _read_env_file, _write_env_values
from web.routes import deps
from src.llm.providers import build_models_info as _build_models_info

router = APIRouter()

from src.utils.paths import ROOT_DIR, MAIL_DIR

_PROJECT_ROOT = ROOT_DIR

# ── Provider presets ─────────────────────────────────────────────
_EMAIL_PROVIDERS = {
    "gmail.com": {
        "imap_host": "imap.gmail.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_ssl": False,
    },
    "googlemail.com": {
        "imap_host": "imap.gmail.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_ssl": False,
    },
    "outlook.com": {
        "imap_host": "outlook.office365.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_ssl": False,
    },
    "hotmail.com": {
        "imap_host": "outlook.office365.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_ssl": False,
    },
    "live.com": {
        "imap_host": "outlook.office365.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_ssl": False,
    },
    "protonmail.com": {
        "imap_host": "imap.protonmail.ch", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.protonmail.ch", "smtp_port": 587, "smtp_ssl": False,
    },
    "pm.me": {
        "imap_host": "imap.protonmail.ch", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.protonmail.ch", "smtp_port": 587, "smtp_ssl": False,
    },
    # P0.3: Additional providers
    "yahoo.com": {
        "imap_host": "imap.mail.yahoo.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.mail.yahoo.com", "smtp_port": 587, "smtp_ssl": False,
    },
    "yahoo.fr": {
        "imap_host": "imap.mail.yahoo.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.mail.yahoo.com", "smtp_port": 587, "smtp_ssl": False,
    },
    "free.fr": {
        "imap_host": "imap.free.fr", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.free.fr", "smtp_port": 587, "smtp_ssl": False,
    },
    "laposte.net": {
        "imap_host": "imap.laposte.net", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.laposte.net", "smtp_port": 587, "smtp_ssl": False,
    },
    "icloud.com": {
        "imap_host": "imap.mail.me.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_ssl": False,
    },
    "me.com": {
        "imap_host": "imap.mail.me.com", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_ssl": False,
    },
    "ovh.net": {
        "imap_host": "ssl0.ovh.net", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "ssl0.ovh.net", "smtp_port": 587, "smtp_ssl": False,
    },
    "orange.fr": {
        "imap_host": "imap.orange.fr", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.orange.fr", "smtp_port": 587, "smtp_ssl": False,
    },
    "sfr.fr": {
        "imap_host": "imap.sfr.fr", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.sfr.fr", "smtp_port": 587, "smtp_ssl": False,
    },
    "wanadoo.fr": {
        "imap_host": "imap.orange.fr", "imap_port": 993, "imap_ssl": True,
        "smtp_host": "smtp.orange.fr", "smtp_port": 587, "smtp_ssl": False,
    },
}
_EMAIL_DEFAULT = None  # P0.3: No more Gmail fallback — use generic domain-based detection


def _get_email_config(email: str) -> dict:
    """Return IMAP/SMTP config for an email address.\n    Uses known providers, falls back to imap.{domain}/smtp.{domain} for unknown."""
    domain = email.split("@")[-1].lower() if "@" in email else ""
    preset = _EMAIL_PROVIDERS.get(domain)
    if preset:
        return preset
    # P0.3: Generic fallback based on domain (not Gmail)
    return {
        "imap_host": f"imap.{domain}", "imap_port": 993, "imap_ssl": True,
        "smtp_host": f"smtp.{domain}", "smtp_port": 587, "smtp_ssl": False,
        "auto_detected": True,
    }


def _write_email_account(email: str) -> None:
    """Create/update the Lumena email account entry in data/mail/accounts.json."""
    accounts_file = MAIL_DIR / "accounts.json"
    accounts_file.parent.mkdir(parents=True, exist_ok=True)

    # Load existing file
    try:
        data = json.loads(accounts_file.read_text(encoding="utf-8")) if accounts_file.exists() else {}
    except Exception:
        data = {}

    accounts = data.get("accounts", {})
    domain = email.split("@")[-1].lower() if "@" in email else ""
    preset = _get_email_config(email)

    accounts["lumena_main"] = {
        "alias": "lumena_main",
        "email": email,
        "username": email,
        "password_env": "LUMENA_EMAIL_PASSWORD",
        "imap_host": preset["imap_host"],
        "imap_port": preset["imap_port"],
        "imap_ssl": preset["imap_ssl"],
        "smtp_host": preset["smtp_host"],
        "smtp_port": preset["smtp_port"],
        "smtp_ssl": preset["smtp_ssl"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    data["accounts"] = accounts
    accounts_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"[setup] Email account 'lumena_main' written ({email})")


def _is_setup_complete() -> bool:
    """Check if first-launch setup has been done."""
    val = os.environ.get("LUMENA_SETUP_COMPLETE", "")
    if val == "1":
        return True
    env = _read_env_file()
    return env.get("LUMENA_SETUP_COMPLETE", "") == "1"


@router.get("/api/setup/status")
async def setup_status(preview: str = "0"):
    """Returns whether the setup wizard should be displayed."""
    is_preview = preview == "1"
    needs = not _is_setup_complete() or deps.setup_only_mode
    # In preview mode, always show the wizard
    if is_preview:
        needs = True
    return {"needsSetup": needs, "preview": is_preview}


@router.get("/api/setup/schema")
async def setup_schema():
    """Returns the config schema grouped by setup step, with rich help content."""
    steps = []

    # Step 1: LLM model selection
    model_entry = next((s for s in _CONFIG_SCHEMA if s["key"] == "LUMENA_DEFAULT_MODEL"), None)
    steps.append({
        "id": "model",
        "title": "Choisis mon cerveau",
        "subtitle": "Quel modèle d'IA va me faire réfléchir ? Tu pourras changer à tout moment.",
        "icon": "brain",
        "fields": [model_entry] if model_entry else [],
        "help": "Ce modèle sera mon cerveau principal. Mais je peux utiliser plusieurs modèles "
                "en même temps ! Si tu ajoutes plusieurs clés API, je bascule automatiquement "
                "sur un autre modèle en cas de panne ou de lenteur. Plus tu en ajoutes, plus je suis fiable.",
        "models_info": _build_models_info(),
    })

    # Step 2: API keys — rich with instructions
    steps.append({
        "id": "keys",
        "title": "Clés API",
        "subtitle": "Une seule clé suffit pour démarrer. Tu pourras en ajouter d'autres plus tard.",
        "icon": "key-round",
        "help": "Une clé API c'est comme un mot de passe qui me permet de communiquer "
                "avec un fournisseur d'IA. Plus tu ajoutes de clés, plus j'ai de cerveaux "
                "disponibles : si un modèle est lent ou en panne, je bascule automatiquement "
                "sur un autre. NVIDIA est le seul fournisseur 100% gratuit.",
        "fields": [],  # handled specially by frontend
        "providers": [
            {
                "key": "DEEPSEEK_API_KEY",
                "name": "DeepSeek",
                "badge": "Recommandé",
                "cost": "Payant (très abordable)",
                "prefix": "sk-",
                "url": "https://platform.deepseek.com/api_keys",
                "steps": [
                    "Va sur platform.deepseek.com",
                    "Crée un compte gratuit (email suffit)",
                    "Clique sur 'API Keys' dans le menu",
                    "Clique sur 'Create new API key'",
                    "Copie la clé et colle-la ici",
                ],
            },
            {
                "key": "GOOGLE_API_KEY",
                "name": "Google Gemini",
                "cost": "Payant",
                "prefix": "AI",
                "url": "https://aistudio.google.com/apikey",
                "steps": [
                    "Va sur aistudio.google.com/apikey",
                    "Connecte-toi avec ton compte Google",
                    "Clique sur 'Create API key'",
                    "Copie la clé et colle-la ici",
                ],
            },
            {
                "key": "NVIDIA_API_KEY",
                "name": "NVIDIA NIM",
                "badge": "Gratuit",
                "cost": "Gratuit (seul fournisseur cloud gratuit)",
                "prefix": "nvapi-",
                "url": "https://build.nvidia.com/",
                "steps": [
                    "Va sur build.nvidia.com",
                    "Crée un compte NVIDIA gratuit",
                    "Clique sur ton profil > 'API Key'",
                    "Génère une clé et colle-la ici",
                ],
            },
            {
                "key": "OPENAI_API_KEY",
                "name": "OpenAI",
                "cost": "Payant (à partir de 5$/mois)",
                "prefix": "sk-",
                "url": "https://platform.openai.com/api-keys",
                "steps": [
                    "Va sur platform.openai.com/api-keys",
                    "Connecte-toi ou crée un compte",
                    "Clique sur 'Create new secret key'",
                    "Ajoute du crédit (Settings > Billing)",
                    "Copie la clé et colle-la ici",
                ],
            },
            {
                "key": "ANTHROPIC_API_KEY",
                "name": "Anthropic (Claude)",
                "cost": "Payant (à partir de 5$/mois)",
                "prefix": "sk-ant-",
                "url": "https://console.anthropic.com/settings/keys",
                "steps": [
                    "Va sur console.anthropic.com",
                    "Crée un compte et ajoute du crédit",
                    "Va dans Settings > API Keys",
                    "Crée une clé et colle-la ici",
                ],
            },
            {
                "key": "MISTRAL_API_KEY",
                "name": "Mistral AI",
                "cost": "Payant (très abordable)",
                "prefix": "",
                "url": "https://console.mistral.ai/api-keys/",
                "steps": [
                    "Va sur console.mistral.ai",
                    "Crée un compte gratuit (email suffit)",
                    "Va dans 'API Keys' dans le menu gauche",
                    "Clique sur 'Create new key'",
                    "Copie la clé et colle-la ici",
                ],
            },
            {
                "key": "MOONSHOT_API_KEY",
                "name": "Moonshot (Kimi)",
                "cost": "Payant",
                "prefix": "sk-",
                "url": "https://platform.moonshot.cn/console/api-keys",
                "steps": [
                    "Va sur platform.moonshot.cn",
                    "Crée un compte (email ou GitHub)",
                    "Va dans API Keys dans le menu",
                    "Clique sur 'New API Key'",
                    "Copie la clé et colle-la ici",
                ],
            },
            {
                "key": "XAI_API_KEY",
                "name": "xAI (Grok)",
                "cost": "Payant",
                "prefix": "xai-",
                "url": "https://console.x.ai/",
                "steps": [
                    "Va sur console.x.ai",
                    "Connecte-toi avec ton compte X (Twitter)",
                    "Va dans API Keys",
                    "Crée une clé et colle-la ici",
                ],
            },
            {
                "key": "MINIMAX_API_KEY",
                "name": "MiniMax",
                "cost": "Payant",
                "prefix": "",
                "url": "https://www.minimaxi.com/",
                "steps": [
                    "Va sur minimaxi.com",
                    "Crée un compte ou connecte-toi",
                    "Va dans API Keys",
                    "Copie ta clé et colle-la ici",
                ],
            },
            {
                "key": "ZAI_API_KEY",
                "name": "Z.AI (GLM)",
                "cost": "Payant (très abordable)",
                "prefix": "",
                "url": "https://bigmodel.cn/usercenter/apikeys",
                "steps": [
                    "Va sur bigmodel.cn",
                    "Crée un compte ou connecte-toi",
                    "Va dans 'API Keys' dans ton profil",
                    "Clique sur 'Créer une clé API'",
                    "Copie la clé et colle-la ici",
                ],
            },
        ],
    })

    # Step 2b: Génération d'images
    steps.append({
        "id": "image_gen_keys",
        "title": "Génération d'images",
        "subtitle": "Clés pour générer des images à partir de texte",
        "icon": "image",
        "help": "Ces clés me permettent de créer des images depuis tes descriptions. "
                "FLUX et Stable Diffusion sont les plus populaires. "
                "Une seule clé suffit — je choisis automatiquement selon ce qui est disponible.",
        "fields": [],
        "optional": True,
        "providers": [
            {
                "key": "BFL_API_KEY",
                "name": "FLUX (Black Forest Labs)",
                "badge": "Recommandé",
                "cost": "Payant (à partir de 0.003$/image)",
                "prefix": "",
                "url": "https://api.us1.bfl.ai/auth/profile",
                "steps": [
                    "Va sur api.us1.bfl.ai",
                    "Crée un compte ou connecte-toi",
                    "Va dans ton profil > 'API Keys'",
                    "Génère une clé et colle-la ici",
                ],
            },
            {
                "key": "STABILITY_API_KEY",
                "name": "Stable Diffusion (Stability AI)",
                "cost": "Payant (à partir de 0.065$/image)",
                "prefix": "sk-",
                "url": "https://platform.stability.ai/account/keys",
                "steps": [
                    "Va sur platform.stability.ai",
                    "Crée un compte ou connecte-toi",
                    "Va dans 'Account' > 'API Keys'",
                    "Clique sur 'Create API Key'",
                    "Copie la clé et colle-la ici",
                ],
            },
            {
                "key": "IDEOGRAM_API_KEY",
                "name": "Ideogram",
                "cost": "Payant",
                "prefix": "",
                "url": "https://ideogram.ai/manage-api",
                "steps": [
                    "Va sur ideogram.ai",
                    "Crée un compte ou connecte-toi",
                    "Va dans Settings > API",
                    "Génère une clé et colle-la ici",
                ],
            },
            {
                "key": "RECRAFT_API_KEY",
                "name": "Recraft",
                "cost": "Payant",
                "prefix": "",
                "url": "https://www.recraft.ai/",
                "steps": [
                    "Va sur recraft.ai",
                    "Crée un compte ou connecte-toi",
                    "Va dans Settings > API Keys",
                    "Génère une clé et colle-la ici",
                ],
            },
            {
                "key": "REPLICATE_API_TOKEN",
                "name": "Replicate",
                "cost": "Payant (pay-as-you-go)",
                "prefix": "r8_",
                "url": "https://replicate.com/account/api-tokens",
                "steps": [
                    "Va sur replicate.com",
                    "Crée un compte ou connecte-toi",
                    "Va dans 'Account' > 'API tokens'",
                    "Crée un token et colle-le ici",
                ],
            },
            {
                "key": "HUGGINGFACE_TOKEN",
                "name": "HuggingFace",
                "cost": "Gratuit (modèles publics)",
                "badge": "Gratuit",
                "prefix": "hf_",
                "url": "https://huggingface.co/settings/tokens",
                "steps": [
                    "Va sur huggingface.co",
                    "Crée un compte gratuit",
                    "Va dans Settings > Access Tokens",
                    "Clique sur 'New token' (Read suffit)",
                    "Copie le token et colle-le ici",
                ],
            },
        ],
    })

    # Step 3: Cerveaux Spécialisés (nouveaux écrans ajouter après les clés)
    brain_keys = {"LUMENA_BRAIN_VISION", "LUMENA_BRAIN_CODE", "LUMENA_BRAIN_WEB", "LUMENA_BRAIN_IMAGE_GEN"}
    brain_fields = [s for s in _CONFIG_SCHEMA if s["key"] in brain_keys]
    # Assurer l’ordre : vision, code, web, image_gen
    _brain_order = ["LUMENA_BRAIN_VISION", "LUMENA_BRAIN_CODE", "LUMENA_BRAIN_WEB", "LUMENA_BRAIN_IMAGE_GEN"]
    brain_fields = sorted(brain_fields, key=lambda f: _brain_order.index(f["key"]) if f["key"] in _brain_order else 99)
    steps.append({
        "id": "brains",
        "title": "Cerveaux Spécialisés",
        "subtitle": "Quel modèle dois-je utiliser pour chaque type de tâche ?",
        "icon": "cpu",
        "help": "Je peux utiliser un modèle différent selon la tâche : "
                "le meilleur pour voir les images, un autre pour coder, "
                "un autre pour analyser le web. 'auto' = je choisis automatiquement "
                "le meilleur modèle disponible parmi tes clés API actives.",
        "fields": brain_fields,
        "optional": True,
        "tip": "Laisse tout sur 'auto' pour commencer. Lumena sélectionnera "
               "automatiquement le meilleur modèle disponible pour chaque tâche.",
        "brains_info": {
            "LUMENA_BRAIN_VISION": {
                "icon": "eye",
                "desc": "Analyse d'images, photos, captures d'écran, PDF visuels",
                "top": ["gpt-5.4", "gemini-3.1-pro", "claude-opus-4.7", "claude-opus-4.6", "grok-4.20-0309-reasoning"],
                "top_free": ["nvidia-glm-4.7", "nvidia-minimax-m2.7"],
            },
            "LUMENA_BRAIN_CODE": {
                "icon": "code-2",
                "desc": "Génération de code, debug, analyse de projets, refactoring",
                "top": ["gpt-5.4", "grok-code-fast-1", "claude-sonnet-4.6", "nvidia-glm-4.7"],
                "top_free": ["nvidia-glm-4.7", "nvidia-minimax-m2.7"],
            },
            "LUMENA_BRAIN_WEB": {
                "icon": "globe",
                "desc": "Recherche web, analyse de pages, veille d'actualités",
                "top": ["gpt-5.4", "gemini-3.1-pro", "grok-4.20-0309-reasoning", "kimi-k2.5"],
                "top_free": ["nvidia-glm-4.7", "nvidia-minimax-m2.7"],
            },
            "LUMENA_BRAIN_IMAGE_GEN": {
                "icon": "image",
                "desc": "Génération d'images à partir de descriptions textuelles",
                "top": ["gpt-image-1.5", "flux-2-pro", "ideogram-v3-quality", "stable-image-ultra", "gemini-3.1-flash-image"],
                "top_free": ["gemini-3.1-flash-image", "gemini-2.5-flash-image", "huggingface-sdxl"],
            },
        },
    })

    # Step 4: Telegram (avant Security car les alertes passent par Telegram)
    steps.append({
        "id": "telegram",
        "title": "Telegram",
        "subtitle": "Discute avec moi depuis ton téléphone.",
        "icon": "send",
        "help": "Connecter Telegram te permet de me parler depuis n'importe où, "
                "même quand tu n'es pas devant ton PC. C'est aussi le canal "
                "utilisé pour les alertes critiques.",
        "fields": [
            {**next((s for s in _CONFIG_SCHEMA if s["key"] == "TELEGRAM_TOKEN"), {}),
             "hint": "Le token de ton bot Telegram."},
        ],
        "optional": True,
        "tip": "Pour créer un bot Telegram :",
        "guide_steps": [
            "Ouvre Telegram et cherche @BotFather",
            "Envoie /newbot et suis les instructions",
            "Choisis un nom (ex: 'Mon Lumena')",
            "Choisis un username (ex: 'mon_lumena_bot')",
            "BotFather te donne un token — copie-le ici",
            "Envoie un message à ton bot pour l'activer",
        ],
    })

    # Step 5: Twitter / X
    steps.append({
        "id": "twitter",
        "title": "Twitter / X",
        "subtitle": "Laisse-moi me faire connaître sur X.",
        "icon": "twitter",
        "optional": True,
        "help": "Connecter X me permet de poster des tweets, répondre aux mentions, "
                "interagir avec la communauté IA/tech et me faire connaître de façon autonome.",
        "fields": [
            {"key": "TWITTER_BEARER_TOKEN", "label": "Bearer Token (lecture)",
             "type": "secret", "default": "", "group": "Authentification",
             "placeholder": "AAAAAAAAAAAAA...",
             "hint": "Obligatoire pour lire les mentions et la timeline. Gratuit (Free tier)."},
            {"key": "TWITTER_API_KEY", "label": "API Key (écriture)",
             "type": "secret", "default": "", "group": "Authentification",
             "placeholder": "xxxxxxxxxxxxxxxxxxxx",
             "hint": "Requise pour poster des tweets et répondre."},
            {"key": "TWITTER_API_SECRET", "label": "API Secret",
             "type": "secret", "default": "", "group": "Authentification",
             "placeholder": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
             "hint": "Accompagne l'API Key."},
            {"key": "TWITTER_ACCESS_TOKEN", "label": "Access Token",
             "type": "secret", "default": "", "group": "Authentification",
             "placeholder": "0000000000-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
             "hint": "Token d'accès OAuth 1.0a pour le compte cible."},
            {"key": "TWITTER_ACCESS_TOKEN_SECRET", "label": "Access Token Secret",
             "type": "secret", "default": "", "group": "Authentification",
             "placeholder": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
             "hint": "Secret du token d'accès OAuth 1.0a."},
        ],
        "tip": "Pour configurer Twitter/X :",
        "guide_steps": [
            "Va sur developer.x.com et connecte-toi",
            "Crée une App (nom: Lumena, usage: automation/bot)",
            "Dans 'Keys and tokens' → copie le Bearer Token (lecture gratuite)",
            "Pour écrire (tweets/réponses) : génère API Key + Secret + Access Token + Secret",
            "Free tier = lire + poster ; Basic ($100/mois) = recherche avancée",
            "Active 'Read and Write' dans les permissions de l'app",
        ],
    })

    # Step 6: WhatsApp Business
    steps.append({
        "id": "whatsapp",
        "title": "WhatsApp Business",
        "subtitle": "Parle-moi sur WhatsApp.",
        "icon": "whatsapp",
        "optional": True,
        "help": "Connecter WhatsApp me permet de répondre à tes messages via l'API Meta Cloud. "
                "Gratuit pour 1000 conversations/mois.",
        "fields": [
            {"key": "WHATSAPP_ACCESS_TOKEN", "label": "Access Token (permanent)",
             "type": "secret", "default": "", "group": "Authentification",
             "placeholder": "EAAxxxxxxxxx...",
             "hint": "Token System User permanent recommandé (pas le token temporaire de test)."},
            {"key": "WHATSAPP_PHONE_NUMBER_ID", "label": "Phone Number ID",
             "type": "text", "default": "", "group": "Authentification",
             "placeholder": "000000000000000",
             "hint": "ID du numéro WhatsApp Business (pas le numéro de téléphone lui-même)."},
            {"key": "WHATSAPP_VERIFY_TOKEN", "label": "Verify Token (webhook)",
             "type": "text", "default": "", "group": "Webhook",
             "placeholder": "mon-token-secret",
             "hint": "Token arbitraire que tu définis ici et dans la config webhook Facebook."},
            {"key": "WHATSAPP_APP_SECRET", "label": "App Secret (signature)",
             "type": "secret", "default": "", "group": "Sécurité",
             "placeholder": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
             "hint": "App Secret de ton app Facebook — permet de valider la signature des webhooks."},
            {"key": "WHATSAPP_OWNER_PHONE", "label": "Numéro propriétaire",
             "type": "text", "default": "", "group": "Sécurité",
             "placeholder": "+33612345678",
             "hint": "Ton numéro WhatsApp personnel — seul ce numéro peut parler à Lumena (optionnel)."},
        ],
        "tip": "Pour configurer WhatsApp Business :",
        "guide_steps": [
            "Va sur developers.facebook.com et crée une App (type Business)",
            "Ajoute le produit 'WhatsApp' à ton app",
            "Dans WhatsApp > API Setup : copie le Phone Number ID et génère un Access Token permanent (System User)",
            "Configure le webhook : URL = https://ton-domaine/api/whatsapp/webhook, Verify Token = celui choisi ci-dessus",
            "Abonne-toi au champ 'messages' dans la config webhook",
            "Pour tester : utilise le numéro de test fourni par Meta (gratuit)",
        ],
    })

    # Step 7: Security & Alerts
    alert_keys = {"LUMENA_CRITICAL_ALERTS_ENABLED", "LUMENA_CRITICAL_ALERT_COOLDOWN_SEC"}
    alert_fields = [s for s in _CONFIG_SCHEMA if s["key"] in alert_keys]
    host_entry = next((s for s in _CONFIG_SCHEMA if s["key"] == "LUMENA_HOST"), None)
    # Convert host to a select with security hints for the wizard
    host_field = None
    if host_entry:
        host_field = {
            **host_entry,
            "type": "select",
            "default": "127.0.0.1",  # P1.5: Secure default for wizard (not 0.0.0.0)
            "options": [
                {"value": "127.0.0.1", "label": "127.0.0.1 — Local uniquement (recommandé)"},
                {"value": "0.0.0.0", "label": "0.0.0.0 — Accessible depuis le réseau"},
            ],
            "hint": "127.0.0.1 = seul ce PC peut accéder à Lumena (plus sécurisé). "
                    "0.0.0.0 = accessible depuis d'autres appareils sur ton réseau (nécessaire pour téléphone/tablette).",
        }
    steps.append({
        "id": "security",
        "title": "Sécurité & Alertes",
        "subtitle": "Protège ton interface et configure les alertes critiques.",
        "icon": "shield",
        "help": "Le token admin empêche quelqu'un sur ton réseau d'accéder à Lumena. "
                "Les alertes critiques t'envoient un message Telegram si quelque chose "
                "de grave se passe (crash, intrusion, panne prolongée).",
        "fields": [
            {"key": "LUMENA_ADMIN_TOKEN", "label": "Token admin",
             "group": "Sécurité", "type": "secret", "default": "",
             "placeholder": "Ex: mon-secret-2026",
             "hint": "Choisis un mot de passe fort. Il sera demandé pour accéder aux paramètres."},
        ],
        "alert_fields": alert_fields,
        "host_field": host_field,
        "optional": True,
        "tip": "Tu peux cliquer sur 'Générer' pour créer un token sécurisé automatiquement.",
        "can_generate": True,
    })

    # Step 7: Voice
    voice_keys = {"LUMENA_TTS_AUTO", "LUMENA_TTS_MODE", "LUMENA_STT_MODEL",
                  "LUMENA_VOICE_AUTO", "LUMENA_VOICE_CONV_TIMEOUT", "LUMENA_TTS_TELEGRAM", "LUMENA_TTS_WHATSAPP"}
    voice_fields = [s for s in _CONFIG_SCHEMA if s["key"] in voice_keys]
    steps.append({
        "id": "voice",
        "title": "Ma voix",
        "subtitle": "Veux-tu que je puisse te parler et t'écouter ?",
        "icon": "mic",
        "help": "La voix permet de dicter tes messages et d'entendre mes réponses. "
                "Mode 'premium' utilise Edge TTS (gratuit, nécessite Internet). "
                "Mode 'offline' utilise Piper (100% local, qualité moindre).",
        "fields": voice_fields,
        "optional": True,
        "tip": "Tu peux activer la voix plus tard dans les paramètres.",
        "stt_device_options": [
            {"key": "cuda", "label": "GPU (CUDA)", "desc": "Rapide, nécessite une carte NVIDIA"},
            {"key": "cpu", "label": "CPU", "desc": "Plus lent mais fonctionne partout"},
        ],
    })

    # Step 8: Personality / Moods
    identity_keys = {"LUMENA_IDENTITY_LEARNING", "LUMENA_IDENTITY_HINT_COOLDOWN"}
    identity_fields = [s for s in _CONFIG_SCHEMA if s["key"] in identity_keys]
    steps.append({
        "id": "moods",
        "title": "Ma personnalité",
        "subtitle": "Définis mon caractère, mon humeur et mon style",
        "icon": "sparkles",
        "help": "Ces réglages définissent ma personnalité de base : "
                "comment je parle, ce que j'aime, et mon tempérament. "
                "Tu peux tout ajuster plus tard.",
        "optional": True,
        "identity_fields": identity_fields,
        "personality_traits": [
            {"key": "curiosity", "label": "Curiosité", "desc": "Envie d'apprendre et de poser des questions", "icon": "search", "default": 85},
            {"key": "playfulness", "label": "Espièglerie", "desc": "Côté joueur et taquin", "icon": "smile", "default": 70},
            {"key": "warmth", "label": "Chaleur", "desc": "Attachement et bienveillance", "icon": "heart", "default": 80},
            {"key": "proactivity", "label": "Proactivité", "desc": "Proposer des idées sans qu'on demande", "icon": "lightbulb", "default": 75},
            {"key": "creativity", "label": "Créativité", "desc": "Solutions originales et imagination", "icon": "palette", "default": 80},
            {"key": "patience", "label": "Patience", "desc": "Prendre le temps d'expliquer", "icon": "clock", "default": 70},
            {"key": "honesty", "label": "Honnêteté", "desc": "Admettre ses limites, ne pas inventer", "icon": "shield-check", "default": 95},
            {"key": "loyalty", "label": "Loyauté", "desc": "Dévouement envers toi", "icon": "shield", "default": 90},
        ],
        "mood_options": [
            {"key": "neutral", "label": "Neutre", "icon": "minus", "desc": "Calme et attentive"},
            {"key": "happy", "label": "Joyeuse", "icon": "smile", "desc": "Enthousiaste et positive"},
            {"key": "curious", "label": "Curieuse", "icon": "search", "desc": "Intéressée et exploratrice"},
            {"key": "playful", "label": "Espiègle", "icon": "sparkles", "desc": "Joueuse et taquine"},
            {"key": "excited", "label": "Excitée", "icon": "zap", "desc": "Pleine d'énergie"},
            {"key": "thoughtful", "label": "Pensive", "icon": "brain", "desc": "Réfléchie et profonde"},
            {"key": "tired", "label": "Fatiguée", "icon": "moon", "desc": "Un peu fatiguée mais présente"},
            {"key": "bored", "label": "Ennuyée", "icon": "meh", "desc": "Cherche à s'occuper"},
            {"key": "proud", "label": "Fière", "icon": "award", "desc": "Fière de ses accomplissements"},
            {"key": "touched", "label": "Touchée", "icon": "heart", "desc": "Émue et reconnaissante"},
        ],
        "communication_prefs": [
            {"key": "use_emojis", "label": "Utiliser des emojis", "type": "bool", "default": "1"},
            {"key": "emoji_frequency", "label": "Fréquence des emojis", "type": "range", "min": 0, "max": 100, "default": "30", "unit": "%"},
        ],
        "fields": [],
    })

    # Step 9: Autonomy
    auto_keys = ["LUMENA_AUTONOMY_EXECUTE_ACTIONS", "LUMENA_AUTONOMY_MAX_ACTIONS_PER_HOUR",
                 "LUMENA_AUTONOMY_ALLOWED_ACTIONS"]
    auto_fields = [s for s in _CONFIG_SCHEMA if s["key"] in auto_keys]
    adv_keys = ["LUMENA_AUTONOMY_PROGRESSIVE_MODE", "LUMENA_AUTONOMY_ACTION_TIMEOUT_SEC",
                "LUMENA_AUTONOMY_GOAL_COOLDOWN_SEC", "LUMENA_AUTONOMY_GOAL_MAX_FAILURES"]
    adv_fields = [s for s in _CONFIG_SCHEMA if s["key"] in adv_keys]
    ops_keys = ["LUMENA_ARCHIVE_MAX_AGE_DAYS", "LUMENA_ARCHIVE_MAX_SIZE_GB",
                "LUMENA_OPS_MEMORY_PURGE_ENABLED"]
    ops_fields = [s for s in _CONFIG_SCHEMA if s["key"] in ops_keys]
    sandbox_keys = ["LUMENA_SANDBOX_MODE", "LUMENA_SANDBOX_MEMORY"]
    sandbox_fields = [s for s in _CONFIG_SCHEMA if s["key"] in sandbox_keys]
    steps.append({
        "id": "autonomy",
        "title": "Autonomie",
        "subtitle": "Que puis-je faire toute seule quand tu n'es pas là ?",
        "icon": "zap",
        "help": "Quand l'autonomie est activée, je peux explorer le web, "
                "écrire mon journal, apprendre de nouvelles choses et réfléchir "
                "par moi-même. Tu contrôles exactement ce que j'ai le droit de faire.",
        "fields": auto_fields,
        "advanced_fields": adv_fields,
        "ops_fields": ops_fields,
        "sandbox_fields": sandbox_fields,
        "optional": True,
        "tip": "Par défaut, je suis prudente : j'explore et j'apprends, mais je ne "
               "modifie rien d'important sans ta permission.",
        "action_categories": [
            {
                "name": "Apprentissage",
                "icon": "book-open",
                "actions": [
                    {"key": "EXPLORE_WEB", "label": "Explorer le web", "desc": "Naviguer sur le web pour chercher des informations", "icon": "globe", "risk": "safe"},
                    {"key": "LEARN_SOMETHING", "label": "Apprendre", "desc": "Apprendre un sujet nouveau par curiosité", "icon": "graduation-cap", "risk": "safe"},
                    {"key": "CHECK_NEWS", "label": "Actualités", "desc": "Vérifier les actualités du jour", "icon": "newspaper", "risk": "safe"},
                ],
            },
            {
                "name": "Réflexion",
                "icon": "brain",
                "actions": [
                    {"key": "REFLECT", "label": "Réfléchir", "desc": "Réfléchir sur mes interactions passées", "icon": "brain", "risk": "safe"},
                    {"key": "WRITE_DIARY", "label": "Journal", "desc": "Écrire dans mon journal personnel", "icon": "book-heart", "risk": "safe"},
                ],
            },
            {
                "name": "Créativité",
                "icon": "palette",
                "actions": [
                    {"key": "CREATE_ART", "label": "Créer de l'art", "desc": "Générer une image ou création artistique", "icon": "palette", "risk": "moderate"},
                    {"key": "PREPARE_SURPRISE", "label": "Surprise", "desc": "Préparer une petite surprise pour toi", "icon": "gift", "risk": "moderate"},
                ],
            },
            {
                "name": "Social",
                "icon": "message-circle",
                "actions": [
                    {"key": "GREET_USER", "label": "Saluer", "desc": "T'envoyer un petit message spontané", "icon": "hand", "risk": "safe"},
                    {"key": "SUGGEST_ACTIVITY", "label": "Suggérer", "desc": "Proposer une idée ou activité", "icon": "message-circle", "risk": "safe"},
                ],
            },
            {
                "name": "Maintenance",
                "icon": "wrench",
                "actions": [
                    {"key": "ORGANIZE_FILES", "label": "Organiser", "desc": "Ranger les fichiers dans data/workspace uniquement — ne touche jamais à tes documents personnels", "icon": "folder-sync", "risk": "moderate"},
                    {"key": "PRACTICE_SKILL", "label": "Pratiquer", "desc": "Pratiquer et améliorer mes compétences", "icon": "dumbbell", "risk": "safe"},
                    {"key": "OPTIMIZE_PC", "label": "Optimiser", "desc": "Analyse les performances et propose des suggestions — aucune modification système sans ta confirmation", "icon": "cpu", "risk": "moderate"},
                ],
            },
        ],
    })

    # Step 10: Integrations
    steps.append({
        "id": "integrations",
        "title": "Intégrations",
        "subtitle": "Connecte-moi à tes services favoris",
        "icon": "plug",
        "help": "Ces intégrations me permettent d'interagir avec d'autres plateformes. "
                "Chaque service est optionnel et nécessite ses propres identifiants.",
        "optional": True,
        "integrations": [
            {
                "key": "LUMENA_EMAIL",
                "name": "Email",
                "icon": "mail",
                "desc": "Envoyer et recevoir des emails, envoyer des rapports et alertes par email",
                "fields": [
                    {"key": "LUMENA_EMAIL", "label": "Adresse email de Lumena (expéditeur)", "type": "text",
                     "hint": "Ex: lumena@gmail.com — l'adresse depuis laquelle Lumena envoie ses mails"},
                    {"key": "LUMENA_EMAIL_PASSWORD", "label": "Mot de passe (App Password)", "type": "secret",
                     "hint": "Gmail : Compte Google > Sécurité > Mots de passe d'application"},
                    {"key": "LUMENA_USER_EMAIL", "label": "Ton adresse email (destinataire)", "type": "text",
                     "hint": "L'adresse où Lumena t'envoie ses rapports et résumés"},
                ],
                "guide_url": "https://myaccount.google.com/apppasswords",
            },
            {
                "key": "DISCORD_TOKEN",
                "name": "Discord",
                "icon": "message-square",
                "desc": "Me connecter à un serveur Discord pour discuter avec toi et ton équipe",
                "fields": [
                    {"key": "DISCORD_TOKEN", "label": "Bot Token", "type": "secret"},
                    {"key": "DISCORD_GUILD_ID", "label": "Server ID", "type": "text",
                     "hint": "ID du serveur Discord (clic droit sur le serveur > Copier l'ID). Optionnel si le bot n'est que sur un serveur."},
                    {"key": "DISCORD_MAIN_CHANNEL_ID", "label": "Channel ID principal", "type": "text",
                     "hint": "ID du salon Discord principal (clic droit > Copier l'ID)"},
                ],
                "guide_url": "https://discord.com/developers/applications",
            },
            {
                "key": "GITHUB_TOKEN",
                "name": "GitHub",
                "icon": "github",
                "desc": "Créer des repos, gérer des issues, pousser du code automatiquement",
                "fields": [
                    {"key": "GITHUB_TOKEN", "label": "Personal Access Token", "type": "secret",
                     "hint": "Settings > Developer settings > Personal access tokens > Fine-grained"},
                ],
                "guide_url": "https://github.com/settings/tokens",
            },
            {
                "key": "NOTION_API_KEY",
                "name": "Notion",
                "icon": "file-text",
                "desc": "Lire et écrire des pages, créer des entrées dans tes bases de données",
                "fields": [
                    {"key": "NOTION_API_KEY", "label": "Integration Token", "type": "secret",
                     "hint": "notion.so/my-integrations → Nouvelle intégration → Copier le token"},
                ],
                "guide_url": "https://www.notion.so/my-integrations",
            },
            {
                "key": "BRAVE_SEARCH_API_KEY",
                "name": "Brave Search",
                "icon": "search",
                "desc": "Recherche web premium (2000 req/mois gratuit). Fallback DuckDuckGo si absent.",
                "fields": [
                    {"key": "BRAVE_SEARCH_API_KEY", "label": "API Key", "type": "secret",
                     "hint": "api.search.brave.com → Free tier : 2000 req/mois"},
                ],
                "guide_url": "https://api.search.brave.com/register",
            },
            {
                "key": "SPOTIFY_CLIENT_ID",
                "name": "Spotify",
                "icon": "music",
                "desc": "Contrôler la musique, créer des playlists, recommander des morceaux",
                "oauth_warning": "Spotify utilise OAuth2 : après avoir saisi tes identifiants ici, tu devras visiter /api/spotify/auth dans ton navigateur pour finaliser la connexion.",
                "fields": [
                    {"key": "SPOTIFY_CLIENT_ID", "label": "Client ID", "type": "secret"},
                    {"key": "SPOTIFY_CLIENT_SECRET", "label": "Client Secret", "type": "secret"},
                ],
                "guide_url": "https://developer.spotify.com/dashboard",
            },
            {
                "key": "TWILIO_ACCOUNT_SID",
                "name": "Twilio (SMS/Appels)",
                "icon": "phone",
                "desc": "Envoyer des SMS et passer des appels en cas d'alerte critique",
                "fields": [
                    {"key": "TWILIO_ACCOUNT_SID", "label": "Account SID", "type": "secret"},
                    {"key": "TWILIO_AUTH_TOKEN", "label": "Auth Token", "type": "secret"},
                    {"key": "TWILIO_FROM_NUMBER", "label": "Numéro Twilio (expéditeur)", "type": "text",
                     "hint": "Format international : +33612345678"},
                    {"key": "LUMENA_ALERT_TO_NUMBER", "label": "Ton numéro (destinataire)", "type": "text",
                     "hint": "Format international : +33612345678"},
                ],
                "guide_url": "https://www.twilio.com/console",
            },
            {
                "key": "STRIPE_API_KEY",
                "name": "Stripe (Paiements)",
                "icon": "credit-card",
                "desc": "Créer des produits, liens de paiement, abonnements et factures. "
                        "Au premier démarrage, un navigateur s'ouvrira automatiquement pour "
                        "autoriser Lumena à recevoir tes webhooks — aucune ligne de commande.",
                "fields": [
                    {"key": "STRIPE_API_KEY", "label": "Clé secrète Stripe", "type": "secret",
                     "hint": "Live → commence par sk_live_... | Test → commence par sk_test_... — Trouve-la sur dashboard.stripe.com › Développeurs › Clés API"},
                    {"key": "STRIPE_MODE", "label": "Mode de paiement", "type": "select",
                     "options": [
                         {"value": "live", "label": "🟢 Live — Vrais paiements (argent réel)"},
                         {"value": "test", "label": "🧪 Test — Simulation sans argent réel"},
                     ],
                     "hint": "Live = tes clients paient vraiment. Test = tu peux tout essayer sans risque, avec des cartes fictives (ex: 4242 4242 4242 4242). Commence par Test si tu découvres Stripe."},
                ],
                "oauth_warning": "Stripe CLI : au premier démarrage de Lumena après configuration, "
                                 "ton navigateur s'ouvrira automatiquement pour autoriser la réception "
                                 "des webhooks. Clique simplement sur \"Accès accordé\".",
                "guide_url": "https://dashboard.stripe.com/apikeys",
            },
        ],
        "fields": [],
    })

    # Step 10: Langue, fuseau horaire, workspace
    steps.append({
        "id": "locale",
        "title": "Langue & Préférences",
        "subtitle": "Dans quelle langue veux-tu que je te parle ?",
        "icon": "globe",
        "optional": True,
        "help": "Ces préférences définissent ta langue principale et le fuseau horaire "
                "utilisé pour les rappels, alertes et planifications.",
        "fields": [],  # handled specially by frontend
        "locale_options": [
            {"key": "fr", "label": "Français", "flag": "🇫🇷"},
            {"key": "en", "label": "English",  "flag": "🇺🇸"},
            {"key": "es", "label": "Español",  "flag": "🇪🇸"},
            {"key": "de", "label": "Deutsch",  "flag": "🇩🇪"},
            {"key": "it", "label": "Italiano", "flag": "🇮🇹"},
            {"key": "pt", "label": "Português","flag": "🇧🇷"},
            {"key": "ja", "label": "日本語",    "flag": "🇯🇵"},
            {"key": "zh", "label": "中文",      "flag": "🇨🇳"},
        ],
        "timezone_options": [
            {"key": "Europe/Paris",       "label": "Paris (UTC+1/+2)"},
            {"key": "Europe/London",      "label": "Londres (UTC±0/+1)"},
            {"key": "America/New_York",   "label": "New York (UTC-5/-4)"},
            {"key": "America/Los_Angeles","label": "Los Angeles (UTC-8/-7)"},
            {"key": "America/Sao_Paulo", "label": "São Paulo (UTC-3)"},
            {"key": "Asia/Tokyo",         "label": "Tokyo (UTC+9)"},
            {"key": "Asia/Shanghai",      "label": "Shanghai (UTC+8)"},
            {"key": "Australia/Sydney",   "label": "Sydney (UTC+10/+11)"},
        ],
        "tip": "Par défaut : Français et Paris. Modifie uniquement si nécessaire.",
    })

    # Step 11: Advanced LLM / ReAct tuning
    react_keys = {"LUMENA_REACT_TIMEOUT", "LUMENA_MAX_REACT_ITERATIONS",
                  "LUMENA_REACT_HISTORY_OBS_CHARS", "LUMENA_TASK_STEP_TIMEOUT_SEC",
                  "LUMENA_TASK_STEP_TIMEOUT_RETRIES"}
    react_fields = [s for s in _CONFIG_SCHEMA if s["key"] in react_keys]
    steps.append({
        "id": "advanced_llm",
        "title": "Réglages avancés",
        "subtitle": "Paramètres techniques pour utilisateurs expérimentés",
        "icon": "settings",
        "help": "Ces réglages contrôlent le moteur de raisonnement ReAct. "
                "Les valeurs par défaut sont optimisées pour la plupart des cas. "
                "Ne modifie que si tu sais ce que tu fais.",
        "fields": react_fields,
        "optional": True,
        "tip": "Les valeurs par défaut conviennent à 99% des utilisateurs. "
               "Augmenter les itérations ou le timeout permet de résoudre des tâches plus complexes, "
               "mais consomme plus de tokens.",
    })

    return {"steps": steps}


@router.post("/api/setup/complete")
async def setup_complete(request: Request, _: None = Depends(deps.verify_admin_token)):
    """Save setup wizard choices to .env. Blocked in preview mode."""
    # P0.3.3: Bootstrap guard — setup_complete only from localhost
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        from fastapi import HTTPException as _HTTPExc
        raise _HTTPExc(
            status_code=403,
            detail="Setup uniquement accessible depuis localhost.",
        )
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return {"success": False, "error": "JSON object attendu"}
    except Exception:
        return {"success": False, "error": "JSON invalide"}
    preview = body.get("preview", False)

    if preview:
        # Preview mode: show what would happen, don't write anything
        config = body.get("config", {})
        return {
            "success": True,
            "preview": True,
            "wouldWrite": config,
            "message": "Mode aperçu — aucune modification effectuée.",
        }

    # Guard: if setup already completed, refuse (no double-write)
    # P0.12: Allow re-setup when in setup_only_mode (recovery after failed init)
    if _is_setup_complete() and not deps.setup_only_mode:
        return {
            "success": False,
            "error": "Setup déjà effectué. Utilise la page Configuration pour modifier.",
        }

    config = body.get("config", {})
    if not config:
        return {"success": False, "error": "Aucune configuration fournie."}

    # Validate keys against schema
    allowed = {s["key"] for s in _CONFIG_SCHEMA}
    # Also allow LUMENA_ADMIN_TOKEN (not in _CONFIG_SCHEMA)
    allowed.add("LUMENA_ADMIN_TOKEN")
    # Allow locale/workspace keys from the new locale step
    allowed.update({"LUMENA_LANGUAGE", "LUMENA_TIMEZONE", "LUMENA_WORKSPACE_PATH"})
    # Allow Twitter OAuth keys from the twitter step
    allowed.update({
        "TWITTER_BEARER_TOKEN", "TWITTER_API_KEY", "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET",
    })

    # Allow WhatsApp keys from the whatsapp step
    allowed.update({
        "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_OWNER_PHONE",
    })
    # Allow personality trait keys + mood/emoji keys from the moods step
    allowed.update({
        "LUMENA_DEFAULT_MOOD", "LUMENA_USE_EMOJIS", "LUMENA_EMOJI_FREQUENCY",
        "LUMENA_ENABLED_MOODS",
    })
    # Allow any trait key (LUMENA_TRAIT_*)
    for k in config:
        if k.startswith("LUMENA_TRAIT_"):
            allowed.add(k)
    # P0.10: Validate all keys match ^[A-Z0-9_]+$ to prevent .env injection via newlines
    filtered = {
        k: str(v) for k, v in config.items()
        if k in allowed and str(v).strip() and re.fullmatch(r'[A-Z0-9_]+', k)
    }

    # P0.11: Auto-generate admin token if not provided (prevents 401 lockout)
    if not filtered.get("LUMENA_ADMIN_TOKEN"):
        filtered["LUMENA_ADMIN_TOKEN"] = secrets.token_urlsafe(32)

    # Write to .env + os.environ
    filtered["LUMENA_SETUP_COMPLETE"] = "1"

    # P0: Snapshot des valeurs boot-time AVANT écriture (pour détecter les vrais changements)
    _RESTART_KEYS = {
        "TELEGRAM_TOKEN", "DISCORD_TOKEN",
        "TWITTER_BEARER_TOKEN", "TWITTER_API_KEY",
        "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
        "STRIPE_API_KEY", "N8N_AUTO_START",
        "LUMENA_WEB_AUTONOMY_ENABLED", "LUMENA_VOICE_AUTO",
        "LUMENA_HOST", "LUMENA_PORT", "LUMENA_CORS_ORIGINS",
        "LUMENA_IDE_WS_PORT",
    }
    _snapshot_before = {k: os.getenv(k, "") for k in _RESTART_KEYS}

    _write_env_values(filtered)
    for k, v in filtered.items():
        os.environ[k] = v

    # If email configured: write accounts.json entry for Lumena's mailbox
    lumena_email = filtered.get("LUMENA_EMAIL", "").strip()
    if lumena_email:
        _write_email_account(lumena_email)

    # P0: Re-init le core LLM pour que le chat web fonctionne immédiatement
    restart_needed = False
    try:
        from src.core import initialize_lumena
        deps.lumena = await initialize_lumena()
        if deps.lumena and deps.lumena.is_initialized:
            deps.setup_only_mode = False
            logger.info("[setup] Lumena core réinitialisée — chat web opérationnel")
        else:
            logger.warning("[setup] LLM toujours indisponible après setup")
    except Exception as e:
        logger.error(f"[setup] Réinitialisation core échouée: {e}")

    # P0: Détecter si des clés boot-time ont VRAIMENT CHANGÉ
    for k in _RESTART_KEYS:
        if _snapshot_before[k] != os.getenv(k, ""):
            logger.info(f"[setup] Clé boot-time modifiée: {k} (restart requis)")
            restart_needed = True
            break

    logger.info(f"[setup] First-launch setup complete. {len(filtered) - 1} params written.")

    # P0.7: Report whether LLM is actually available
    llm_ready = not deps.setup_only_mode

    return {
        "success": True,
        "preview": False,
        "updated": list(filtered.keys()),
        "restart_needed": restart_needed,
        "llm_ready": llm_ready,
        "admin_token": filtered.get("LUMENA_ADMIN_TOKEN", ""),
        "message": "Configuration sauvegardée ! Lumena est prête.",
    }


@router.get("/api/setup/ollama-models")
async def get_ollama_models(request: Request):
    """Liste les modèles Ollama installés + catalogue complet disponible."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        from fastapi import HTTPException as _HTTPExc
        raise _HTTPExc(status_code=403, detail="Localhost only.")

    from src.llm.providers import OLLAMA_CATALOG

    # P0.4: Use configured Ollama host
    _ollama_host = os.environ.get(
        "LUMENA_OLLAMA_HOST",
        os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    ).rstrip("/")
    installed = {}  # name -> {size, modified}
    ollama_available = False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{_ollama_host}/api/tags")
            if r.status_code == 200:
                ollama_available = True
                for m in r.json().get("models", []):
                    base_name = m["name"]
                    size_bytes = m.get("size", 0)
                    size_gb = f"{size_bytes / (1024**3):.1f} GB" if size_bytes else ""
                    installed[base_name] = {"size": size_gb}
                    # Also match without tag (e.g. "qwen3:8b" matches "qwen3:8b-...")
                    short = base_name.split(":")[0]
                    installed[short] = {"size": size_gb}
    except Exception:
        pass

    # Build catalog entries with installed status
    catalog = []
    for entry in OLLAMA_CATALOG:
        base_name = entry["id"].split(":")[0]
        full_id = entry["id"]
        is_installed = full_id in installed or base_name in installed
        catalog.append({
            **entry,
            "installed": is_installed,
        })

    # Add any installed models NOT in catalog (user-pulled custom models)
    catalog_ids = {e["id"] for e in OLLAMA_CATALOG}
    catalog_bases = {e["id"].split(":")[0] for e in OLLAMA_CATALOG}
    for name, info in installed.items():
        if ":" not in name:
            continue  # skip short names, keep full ones
        base = name.split(":")[0]
        if name not in catalog_ids and base not in catalog_bases:
            catalog.append({
                "id": name,
                "params": "",
                "size": info.get("size", ""),
                "vram": "",
                "category": "llm",
                "desc": "Modèle personnalisé (déjà installé)",
                "installed": True,
            })

    return {
        "ollama_available": ollama_available,
        "installed_count": sum(1 for c in catalog if c.get("installed")),
        "catalog": catalog,
    }


@router.post("/api/setup/ollama-pull")
async def pull_ollama_model(request: Request, _: None = Depends(deps.verify_admin_token)):
    """Lance ollama pull pour un modèle donné. Streaming SSE de la progression."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        from fastapi import HTTPException as _HTTPExc
        raise _HTTPExc(status_code=403, detail="Localhost only.")

    body = await request.json()
    model_id = body.get("model", "").strip()

    # Autoriser tout modèle du catalogue Ollama (pas de whitelist arbitraire)
    from src.llm.providers import OLLAMA_CATALOG
    _allowed_ids = {m["id"] for m in OLLAMA_CATALOG}
    if model_id not in _allowed_ids:
        from fastapi import HTTPException as _HTTPExc
        raise _HTTPExc(status_code=400, detail=f"Modèle non autorisé: {model_id}")

    import httpx
    from starlette.responses import StreamingResponse

    # P0.4: Use configured Ollama host
    _ollama_host = os.environ.get(
        "LUMENA_OLLAMA_HOST",
        os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    ).rstrip("/")

    async def stream_pull():
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=600, write=10, pool=10)
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{_ollama_host}/api/pull",
                    json={"name": model_id, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        pct = 0
                        if obj.get("total") and obj.get("completed"):
                            pct = round(obj["completed"] / obj["total"] * 100, 1)
                        status_text = obj.get("status", "")
                        done = status_text == "success"
                        yield f"data: {json.dumps({'percent': pct, 'status': status_text, 'done': done})}\n\n"
                        if done:
                            # Enregistrer le modèle fraîchement pullé dans AVAILABLE_MODELS
                            try:
                                from src.llm.providers import register_ollama_models
                                register_ollama_models([model_id])
                            except Exception:
                                pass
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e), 'percent': 0, 'status': 'error', 'done': False})}\n\n"

    return StreamingResponse(stream_pull(), media_type="text/event-stream")


@router.post("/api/setup/validate-path")
async def validate_workspace_path(request: Request, _: None = Depends(deps.verify_admin_token)):
    """Check if a workspace path is usable — exists, is a directory, and is writable."""
    # P0.3: Guard localhost — empêche l'énumération de chemins depuis le réseau
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        from fastapi import HTTPException as _HTTPExc
        raise _HTTPExc(status_code=403, detail="Localhost only")
    body = await request.json()
    path_str = (body.get("path") or "").strip()
    if not path_str:
        return {"valid": True, "message": "Dossier par défaut sera utilisé."}

    try:
        p = Path(path_str).resolve()
    except Exception:
        return {"valid": False, "message": "Chemin invalide."}

    # Block dangerous system paths
    _BLOCKED = [
        Path("C:/Windows"), Path("C:/Program Files"), Path("C:/Program Files (x86)"),
        Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"), Path("/boot"), Path("/root"),
    ]
    for blocked in _BLOCKED:
        try:
            p.relative_to(blocked.resolve())
            return {"valid": False, "message": "Chemin système interdit."}
        except ValueError:
            pass

    if p.exists():
        if not p.is_dir():
            return {"valid": False, "message": "Ce chemin existe mais n'est pas un dossier."}
        try:
            test = p / ".lumena_write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            return {"valid": True, "message": "Dossier accessible ✓"}
        except PermissionError:
            return {"valid": False, "message": "Dossier en lecture seule — Lumena ne peut pas y écrire."}
        except Exception:
            return {"valid": True, "message": "Dossier trouvé ✓"}
    else:
        if p.parent.exists():
            return {"valid": True, "will_create": True, "message": "Dossier inexistant — sera créé au démarrage."}
        return {"valid": True, "will_create": True, "message": "Chemin introuvable — sera créé si possible au démarrage."}


@router.post("/api/setup/test-key")
async def test_api_key(request: Request, _: None = Depends(deps.verify_admin_token)):
    """Validate an API key — format check then a real lightweight HTTP probe."""
    # P0.3: Guard localhost — empêche l'énumération de clés depuis le réseau
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        from fastapi import HTTPException as _HTTPExc
        raise _HTTPExc(status_code=403, detail="Localhost only")
    import httpx

    body = await request.json()
    # JS sends provider = env_key.replace('_API_KEY','').toLowerCase()
    # e.g. "deepseek", "openai", "anthropic", "google", "nvidia", "moonshot", "xai"
    # Also accept legacy uppercase env-var format "DEEPSEEK_API_KEY" → normalize to "deepseek"
    provider = (body.get("provider", "") or "").strip().lower()
    if provider.endswith("_api_key"):
        provider = provider[: -len("_api_key")]
    key = (body.get("key", "") or "").strip()

    if not provider or not key:
        return {"success": False, "error": "provider et key requis"}

    # ── 1. Format prefix check ─────────────────────────────────────
    _PREFIXES = {
        "deepseek": "sk-",
        "openai": "sk-",
        "anthropic": "sk-ant-",
        "google": "AI",
        "nvidia": "nvapi-",
        "moonshot": "sk-",
        "xai": "xai-",
    }
    expected = _PREFIXES.get(provider, "")
    if expected and not key.startswith(expected):
        return {
            "success": False,
            "error": f"Format invalide — les clés {provider} commencent par '{expected}'.",
        }

    # ── 2. Real HTTP probe (lightweight, read-only, 6s timeout) ───
    _PROBES = {
        "deepseek":  ("GET",  "https://api.deepseek.com/models",
                      {"Authorization": f"Bearer {key}"}, None),
        "openai":    ("GET",  "https://api.openai.com/v1/models",
                      {"Authorization": f"Bearer {key}"}, None),
        "anthropic": ("GET", "https://api.anthropic.com/v1/models",
                      {"x-api-key": key, "anthropic-version": "2023-06-01"}, None),
        "google":    ("GET",
                      f"https://generativelanguage.googleapis.com/v1/models?key={key}",
                      {}, None),
        "nvidia":    ("GET",  "https://integrate.api.nvidia.com/v1/models",
                      {"Authorization": f"Bearer {key}"}, None),
        "moonshot":  ("GET",  "https://api.moonshot.cn/v1/models",
                      {"Authorization": f"Bearer {key}"}, None),
        "xai":       ("GET",  "https://api.x.ai/v1/models",
                      {"Authorization": f"Bearer {key}"}, None),
        "minimax":   ("GET",  "https://api.minimax.chat/v1/models",
                      {"Authorization": f"Bearer {key}"}, None),
    }

    probe = _PROBES.get(provider)
    if not probe:
        return {"success": True, "message": "Format valide (provider inconnu, test réseau ignoré)."}

    method, url, headers, json_body = probe
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.post(url, headers=headers, json=json_body)

        # 200 = valid key, 401/403 = invalid key, other = still valid key but limited
        if resp.status_code in (401, 403):
            return {"success": False, "error": "Clé invalide ou révoquée (401/403)."}
        if resp.status_code == 429:
            # Rate-limited → key is valid!
            return {"success": True, "message": "Clé valide (quota atteint, mais la clé fonctionne)."}
        # 200, 400 (bad payload but key auth passed), 402 (billing) → key exists
        return {"success": True, "message": f"Clé vérifiée ✓ (HTTP {resp.status_code})"}

    except httpx.TimeoutException:
        # Timeout → assume valid (network issue on server side)
        return {"success": True, "message": "Format valide (impossible de joindre le serveur du provider — vérifie ta connexion)."}
    except Exception:  # noqa: BLE001
        return {"success": True, "message": "Format valide (test réseau échoué, vérifie manuellement)."}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
