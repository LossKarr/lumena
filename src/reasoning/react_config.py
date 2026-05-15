"""
Configuration, constantes, enums et dataclasses pour la boucle ReAct.

Extrait de react.py pour améliorer la lisibilité et la maintenabilité.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import platform
import re


# ── Feature flags ──────────────────────────────────────────────────

# Détection OS pour commandes adaptées
IS_WINDOWS = platform.system() == "Windows"
OS_NAME = platform.system()

# Import des outils avancés
try:
    from ..tools.apply_patch import apply_patch, edit_file, parse_patch
    from ..tools.compaction import ContextCompactor, get_token_stats, format_token_stats, estimate_tokens
    ADVANCED_TOOLS_AVAILABLE = True
except ImportError:
    ADVANCED_TOOLS_AVAILABLE = False
    apply_patch = None
    edit_file = None
    parse_patch = None
    ContextCompactor = None
    get_token_stats = None
    format_token_stats = None
    estimate_tokens = None

from ..tools.file_guardrails import WorkspaceFileGuardrails
try:
    from ..runtime.context import get_current_runtime_context
except Exception:
    get_current_runtime_context = None

try:
    from ..telemetry import (
        publish_trace,
        push_trace_context,
        pop_trace_context,
        current_trace_context,
        get_file_edits_store,
        compute_workspace_relative,
        read_text_if_exists,
    )
    TELEMETRY_AVAILABLE = True
except Exception:
    TELEMETRY_AVAILABLE = False
    publish_trace = None
    push_trace_context = None
    pop_trace_context = None
    current_trace_context = None
    get_file_edits_store = None
    compute_workspace_relative = None
    read_text_if_exists = None


# ── Enums et Dataclasses ──────────────────────────────────────────

class ActionType(Enum):
    """Types d'actions possibles."""
    TOOL_CALL = "tool_call"      # Appeler un outil
    FINAL_ANSWER = "final"       # Réponse finale
    THINKING = "thinking"        # Réflexion interne
    CLARIFY = "clarify"          # Demander clarification


@dataclass
class Thought:
    """Une pensée de LUMENA."""
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Action:
    """Une action à exécuter."""
    action_type: ActionType
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)
    answer: Optional[str] = None


@dataclass
class Observation:
    """Résultat d'une action."""
    content: str
    success: bool = True
    timestamp: datetime = field(default_factory=datetime.now)
    sub_results: tuple = ()  # tuple[SubToolResult] — peuplé par parallel_tools via ToolRegistry


@dataclass
class ReActStep:
    """Une étape complète du cycle ReAct."""
    thought: Thought
    action: Action
    observation: Optional[Observation] = None


@dataclass
class TaskItem:
    """Une tache dans le plan ReAct."""
    description: str
    completed: bool = False
    completed_at_iteration: Optional[int] = None
    completed_by_tool: Optional[str] = None
    completion_status: str = ""  # TaskCompletionStatus — créé/vérifié/envoyé/déployé…
    completion_evidence: str = ""   # phrase courte expliquant la preuve
    completion_confidence: str = "" # "strong" | "medium" | "weak"


# ── LLM Output Sanitization (corrige bugs courants des LLM) ────────
_SMART_SQ_RE = re.compile(r'[\u2018\u2019\u201A\u201B]')
_SMART_DQ_RE = re.compile(r'[\u201C\u201D\u201E\u201F]')
_SMART_DASH_RE = re.compile(r'[\u2010\u2013\u2014\u2212]')
_HTML_ENTITIES = {
    '&amp;': '&', '&lt;': '<', '&gt;': '>',
    '&quot;': '"', '&#39;': "'", '&apos;': "'",
    '&#x27;': "'", '&#x2F;': '/',
}


def _sanitize_llm_output(text: str) -> str:
    """
    Corrige les artefacts courants des LLM dans les réponses ReAct.
    Applique 8 wrappers (tool name trim, HTML decode, smart quotes, etc.)
    Ne touche PAS au contenu dans les blocs de code (```) pour ne pas casser le code.
    """
    if not text:
        return text

    # 1. HTML entities (xAI/Grok et certains providers encodent)
    for entity, char in _HTML_ENTITIES.items():
        if entity in text:
            text = text.replace(entity, char)

    # 2. Smart quotes → ASCII dans les lignes ACTION/ACTION_INPUT
    #    (pas dans les blocs code/contenu pour ne pas casser)
    lines = text.split('\n')
    result_lines = []
    in_code_block = False
    for line in lines:
        if line.strip().startswith('```'):
            in_code_block = not in_code_block
        if not in_code_block and any(
            kw in line for kw in ('ACTION', 'THOUGHT', 'tool_call', '"action"')
        ):
            line = _SMART_SQ_RE.sub("'", line)
            line = _SMART_DQ_RE.sub('"', line)
            line = _SMART_DASH_RE.sub('-', line)
        result_lines.append(line)
    text = '\n'.join(result_lines)

    # 3. Trailing whitespace sur les noms d'outils (Kimi ajoute parfois des espaces)
    text = re.sub(r'(ACTION:\s*tool_call\s*)\n', r'\1\n', text)

    return text


# ── Plan TODO : regex & hints ──────────────────────────────────────
_PLAN_RE = re.compile(
    r"^PLAN:\s*\n((?:\s*-\s*\[[ xX]\]\s*.+\n?)+)",
    re.MULTILINE | re.IGNORECASE,
)
_TASK_LINE_RE = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.+)$", re.MULTILINE)

_TOOL_COMPLETION_HINTS: Dict[str, List[str]] = {
    # Lecture / analyse (stems courts pour matcher noms ET verbes)
    "read_file": ["lire", "read", "consult", "examin", "voir", "analys", "lecture"],
    "edit_file": ["modif", "edit", "corrig", "chang", "ajout", "fix", "mise a jour"],
    "apply_patch": ["modif", "edit", "corrig", "chang", "patch"],
    # Écriture / création
    "write_file": ["cré", "creer", "create", "génér", "gener", "écri", "ecri", "write"],
    "create_file": ["cré", "creer", "create", "génér", "gener", "écri", "ecri", "write"],
    "create_pdf": ["cré", "creer", "create", "pdf", "document", "rapport", "génér", "gener"],
    "create_invoice_pdf": ["factur", "invoice", "facture", "devis", "prestat", "ttc", "tva", "hors taxe", "ht", "note de frais", "facturer"],
    "create_docx": ["cré", "creer", "create", "docx", "word", "document", "rapport"],
    "create_pptx": ["cré", "creer", "create", "pptx", "présent", "present", "diapo"],
    "create_xlsx": ["cré", "creer", "create", "xlsx", "excel", "tableur"],
    # Exécution
    "execute_code": ["exécut", "execut", "run", "lanc", "test"],
    "run_command": ["exécut", "execut", "run", "lanc", "test", "install"],
    "process_status": ["arriere-plan", "running", "tourne", "serveur", "processus", "statut"],
    # Recherche (stems: "recherch" matche "recherche" ET "rechercher")
    "web_search": ["cherch", "search", "recherch", "trouv"],
    "web_search_brave": ["cherch", "search", "recherch", "trouv"],
    "deep_research": ["cherch", "search", "recherch", "trouv", "analys", "approfondi", "rapport"],
    "web_fetch": ["télécharg", "telecharg", "fetch", "récupér", "recuper", "scrap"],
    # Browser (navigation + interaction DOM) — PRIORISÉ pour la navigation web
    "browser_navigate": ["navig", "ouvrir", "aller", "browser", "accéd", "acced", "visit", "lire", "site", "page", "url", "contenu", "prix", "stock", "billet"],
    "browser_search_google": ["cherch", "search", "recherch", "google", "trouv"],
    "browser_get_content": ["extrai", "contenu", "lire", "récupér", "recuper"],
    "browser_dom_state": ["analys", "vérifi", "verifi", "inspect", "observ", "page", "état", "etat", "formulaire", "confirm"],
    "browser_click_index": ["cliqu", "appuy", "sélect", "select", "choisi", "bouton", "inscri", "connex", "soumett", "valid", "accéd", "acced"],
    "browser_type_index": ["rempli", "sais", "tap", "écri", "ecri", "entr", "formulaire", "champ", "inscri", "inform"],
    "browser_start": ["démarr", "demarr", "lanc", "ouvr", "navigat", "browser"],
    "wait": ["attend", "paus", "délai", "delai", "patienter"],
    # Telegram
    "telegram_send_document": ["envoy", "telegram", "document", "fichier", "rapport", "pdf"],
    "telegram_send_photo": ["envoy", "telegram", "photo", "image"],
    "telegram_send_message": ["envoy", "telegram", "message", "répon", "repon"],
    # WhatsApp
    "send_whatsapp_message": ["envoy", "whatsapp", "message", "répon", "repon"],
    "send_whatsapp_document": ["envoy", "whatsapp", "document", "fichier", "rapport", "pdf"],
    "send_whatsapp_photo": ["envoy", "whatsapp", "photo", "image"],
    "send_whatsapp_audio": ["envoy", "whatsapp", "audio", "voix", "vocal"],
    # Email
    "send_email": ["envoy", "mail", "email", "courr"],
    # Mémoire
    "memory_add": ["mémoris", "memoris", "sauvegard", "retenir", "remember"],
    "memory_search": ["recherch", "cherch", "mémoire", "memoire", "souvenir", "rappel", "search", "erreur", "manque", "identif"],
    "list_journal_dates": ["journal", "date", "historique", "quand", "premier", "début", "debut", "création", "creation"],
    "search_journal": ["journal", "conversation", "historique", "premier", "message", "passé", "passe", "ancien", "souvien", "rappel"],
    # Fichiers
    "list_directory": ["list", "dossier", "répertoir", "repertoir", "explor"],
    "find_files": ["cherch", "trouv", "recherch", "fichier", "confirm", "vérifi", "verifi", "exist"],
    # Skills
    "list_skills": ["list", "skill", "confirm", "vérifi", "verifi", "exist", "disponib"],
    "create_skill": ["cré", "creer", "skill", "compétence", "competence"],
    # Capture
    "screenshot": ["screenshot", "capture", "ecran", "écran"],
    # Projet
    "create_project": ["projet", "project", "scaffold", "créer", "creer", "site", "genér", "gener", "appli"],
    # Website builder
    "generate_website": ["site", "landing", "portfolio", "page web", "genér", "gener", "créer", "creer", "website"],
    "write_website_files": ["site", "écri", "ecri", "fichier", "website", "html", "css"],
    "edit_website": ["corrig", "fix", "couleur", "typo", "lien", "petit", "css", "texte"],
    "delegate_task": ["modif", "amélio", "amelio", "ajout", "chang", "site", "portfolio", "website", "mettre a jour", "mise a jour", "actualise", "update", "refaire", "retravaill", "optimis", "renouvel", "reecrire", "refondre", "restructur", "code", "coder", "code moi", "code-moi", "programme", "jeu", "game", "script", "appli", "application", "api", "bot", "développ", "developp", "bug", "débogu", "debogu", "debug", "refactor", "erreur dans", "répare", "repare", "résou", "resou"],
    "serve_website": ["servir", "serveur", "preview", "prévisual", "previsual", "lancer", "ouvrir"],
    "export_website_zip": ["export", "zip", "archiv", "télécharg", "telecharg"],
    "list_website_projects": ["list", "projet", "site", "website"],
    # Vidéo Remotion
    "generate_video": ["vidéo", "video", "clip", "reel", "short", "tiktok", "animation", "motion", "présentation", "pub", "publicité", "trailer", "intro", "outro", "explainer"],
    # Génération / édition d'images
    "generate_image": ["image", "photo", "illustration", "dessin", "picture", "genere", "crée", "cree", "créer", "imagin", "visualis", "affiche", "poster", "visuel", "graphi", "render", "génère", "génér", "génération", "ai image"],
    "edit_image": ["modif", "edit", "retouche", "inpaint", "remplac", "efface", "supprim", "image", "photo"],
    "generate_thumbnail": ["miniature", "thumbnail", "vignette", "youtube", "ctr"],
    "generate_logo": ["logo", "marque", "brand", "icône", "icone", "identité"],
    "upscale_image": ["agrand", "upscale", "résolution", "resolution", "zoom", "amélio", "amelio", "quality"],
    "remove_background": ["fond", "background", "supprim", "transparent", "détour", "detour"],
    "replace_background": ["fond", "background", "remplac", "changer le fond"],
    "sketch_to_image": ["croquis", "sketch", "dessin", "transform", "réalis", "realis"],
    "generate_svg": ["svg", "vectoriel", "vector", "scalable"],
    "list_image_models": ["modèle", "modele", "model", "image", "provider", "list", "disponible"],
    "edit_video": ["modif", "edit", "chang", "vidéo", "video", "scène", "scene", "animation", "couleur", "texte"],
    "preview_video": ["preview", "prévisual", "previsual", "aperçu", "apercu", "vidéo", "video"],
    "list_video_projects": ["list", "projet", "vidéo", "video", "remotion"],
    # Tests / debug
    "test_and_fix": ["test", "tester", "vérifi", "verifi", "corrig", "fix", "bug", "débogu", "debogu"],
    # Délégation CodeAgent (correction de code multi-fichiers, debugging complexe)
    # NOTE: clé delegate_task déjà définie plus haut avec keywords création+debug fusionnés
    # "delegate_task": (fusionné ci-dessus pour éviter écrasement dict)
    # GitHub (lecture / écriture)
    "github_repo_list": ["lister", "list", "repos", "dépôts", "depots", "répertoir", "repertoir", "étape 1", "etape 1", "identifier"],
    "github_file_read": ["lire", "read", "fichier", "analys", "examiner", "étape 2", "etape 2", "étape 3", "etape 3", "consulter"],
    "github_repo_create": ["créer", "creer", "create", "nouveau repo", "nouveau dépôt"],
    "github_file_write": ["écrire", "ecri", "modif", "write", "mettre à jour", "améliorer"],
    "github_commit": ["commit", "valider", "enregistrer", "sauvegarder"],
    "github_push": ["pousser", "push", "publier", "déployer", "étape 4", "etape 4"],
    "github_create_or_update_file": ["écrire", "ecri", "modif", "write", "mettre à jour", "améliorer"],
    # Mail
    "mail_list_accounts": ["list", "lister", "comptes", "account", "vérif", "verif", "présent", "present"],
    "mail_check_connection": ["connexion", "connex", "vérif", "verif", "test", "accès", "acces"],
    "mail_read": ["lire", "read", "mail", "email", "boîte", "boite", "lecture", "inbox", "message"],
    "mail_send": ["envoy", "send", "mail", "email"],
    # Réseau / sécurité
    "port_scan_fast": ["scan", "port", "réseau", "reseau", "vérifi", "verifi", "disponib", "ouvert", "open", "connect", "accessib"],
    "nmap_scan": ["scan", "réseau", "reseau", "découvr", "decouv", "nmap", "hôte", "hote", "port", "appare"],
    "netcat_probe": ["test", "connect", "sond", "probe", "port", "tcp", "accessib"],
    "ping_host": ["ping", "disponib", "accessi", "réseau", "reseau", "vérifi", "verifi"],
    "osint_scan": ["osint", "renseign", "recherch", "inform", "scan", "reconnaiss"],
    "whois_lookup": ["whois", "domain", "propriétair", "proprietair", "enregistr"],
    "dns_lookup": ["dns", "résol", "resol", "domain", "ip", "adress"],
    "ssl_check": ["ssl", "tls", "certific", "https", "expir"],
    "subdomain_enum": ["sous-domain", "subdomain", "enum", "crt"],
    "http_headers_check": ["header", "http", "sécurit", "securit", "stack"],
    "threat_check": ["malwar", "ioc", "threat", "menac", "virus", "compromis"],
    "port_scan": ["port", "scan", "tcp", "ouvert", "service"],
    "reverse_dns": ["reverse", "ptr", "dns invers", "hostname"],
    "tech_detect": ["technolog", "cms", "framework", "stack", "wordpress", "react"],
    "wayback_check": ["wayback", "archiv", "histori", "ancien"],
    "ip_info": ["ip", "adresse", "géoloc", "geoloc", "shodan"],
    "email_check": ["email", "fuite", "breach", "pwned", "compromis"],
    "domain_recon": ["recon", "domain", "sous-domain", "dns"],
    # Stripe / Paiements
    "stripe_create_product": ["produit", "product", "créer", "creer", "create", "stripe", "article", "offre", "lien", "paiement", "payment"],
    "stripe_list_products": ["produit", "product", "list", "lister", "catalogue", "stripe"],
    "stripe_update_product": ["produit", "product", "modif", "update", "changer", "stripe"],
    "stripe_delete_product": ["produit", "product", "supprim", "delete", "retir", "stripe"],
    "stripe_create_price": ["prix", "price", "tarif", "créer", "creer", "create", "montant", "stripe", "lien", "paiement", "payment", "14", "euro"],
    "stripe_list_prices": ["prix", "price", "tarif", "list", "lister", "stripe"],
    "stripe_create_payment_link": ["lien", "link", "paiement", "payment", "créer", "creer", "url", "partag", "stripe"],
    "stripe_list_payment_links": ["lien", "link", "paiement", "payment", "list", "lister", "stripe"],
    "stripe_update_payment_link": ["lien", "link", "paiement", "désactiv", "activ", "modif", "stripe"],
    "stripe_create_customer": ["client", "customer", "créer", "creer", "create", "inscri", "stripe"],
    "stripe_list_customers": ["client", "customer", "list", "lister", "stripe"],
    "stripe_search_customers": ["client", "customer", "cherch", "recherch", "trouv", "stripe"],
    "stripe_update_customer": ["client", "customer", "modif", "update", "changer", "stripe"],
    "stripe_create_subscription": ["abonnement", "subscription", "souscrip", "créer", "creer", "récurrent", "recurrent", "stripe"],
    "stripe_list_subscriptions": ["abonnement", "subscription", "list", "lister", "stripe"],
    "stripe_cancel_subscription": ["abonnement", "subscription", "annul", "cancel", "résili", "resili", "stripe"],
    "stripe_create_invoice": ["facture", "invoice", "créer", "creer", "create", "facturer", "stripe"],
    "stripe_add_invoice_item": ["facture", "invoice", "ligne", "item", "ajouter", "add", "montant", "prestation", "stripe"],
    "stripe_get_invoice": ["facture", "invoice", "détail", "detail", "pdf", "voir", "get", "consulter", "stripe"],
    "stripe_list_invoices": ["facture", "invoice", "list", "lister", "stripe"],
    "stripe_send_invoice": ["facture", "invoice", "envoy", "send", "email", "stripe"],
    "stripe_void_invoice": ["facture", "invoice", "annul", "void", "stripe"],
    "stripe_create_checkout_session": ["checkout", "session", "paiement", "payment", "créer", "creer", "encaiss", "stripe"],
    "stripe_list_checkout_sessions": ["checkout", "session", "list", "lister", "stripe"],
    "stripe_create_coupon": ["coupon", "réduction", "reduction", "promo", "remise", "créer", "creer", "stripe"],
    "stripe_list_coupons": ["coupon", "réduction", "reduction", "promo", "list", "lister", "stripe"],
    "stripe_delete_coupon": ["coupon", "réduction", "reduction", "supprim", "delete", "stripe"],
    "stripe_create_refund": ["rembours", "refund", "créer", "creer", "create", "stripe"],
    "stripe_list_refunds": ["rembours", "refund", "list", "lister", "stripe"],
    "stripe_get_balance": ["solde", "balance", "argent", "fonds", "disponib", "stripe"],
    "stripe_cli_status": ["stripe", "cli", "status", "statut", "webhook", "listen"],
    "stripe_cli_start": ["stripe", "cli", "start", "démarr", "demarr", "listen", "webhook"],
    "stripe_cli_stop": ["stripe", "cli", "stop", "arrêt", "arret", "listen"],
}


def _build_model_specific_hints(model_id: str) -> str:
    """Retourne des instructions de format spécifiques au modèle actif."""
    m = model_id.lower()
    if "kimi" in m:
        return """
## ⚠️ RÈGLE STRICTE FORMAT (KIMI K2):
- Ton THOUGHT est ton espace de réflexion PRIVÉ pour L'ÉTAPE ACTUELLE UNIQUEMENT.
- STRICTEMENT INTERDIT dans le THOUGHT : écrire ACTION:, ACTION_INPUT:, OBSERVATION: ou simuler des résultats.
- Tu réfléchis → 1 seule ACTION → tu ATTENDS l'OBSERVATION du système. NE L'INVENTE PAS.
- TOUT contenu halluciné après le premier mot-clé ReAct sera SUPPRIMÉ par le système.

❌ INTERDIT (sera supprimé):
THOUGHT: Je vais lire le fichier ACTION: read_file OBSERVATION: contenu du fichier...

✅ CORRECT:
THOUGHT: Je vais lire le fichier pour vérifier le contenu.
ACTION: read_file
ACTION_INPUT: {"path": "..."}
"""
    if "deepseek-r1" in m or "reasoner" in m:
        return """
## ⚠️ RÈGLE FORMAT RÉPONSE:
- N'utilise PAS de balises <think>...</think> ni <answer>...</answer>.
- Format strict : THOUGHT: sur sa ligne → ACTION: sur sa ligne → ACTION_INPUT: sur sa ligne.
"""
    if "qwen" in m:
        return """
## ⚠️ RÈGLE FORMAT RÉPONSE:
- THOUGHT:, ACTION:, ACTION_INPUT: doivent être sur des LIGNES SÉPARÉES. JAMAIS sur la même ligne.
- Exemple interdit : THOUGHT: [réflexion] ACTION: web_search — ce format est invalide.
"""
    if "minimax" in m:
        return """
## ⚠️ RÈGLE FORMAT RÉPONSE:
- Chaque réponse DOIT contenir un bloc ACTION: (FINAL ou un outil). Sans ACTION: = réponse invalide.
- Ne termine JAMAIS sans ACTION: FINAL, même pour une réponse courte ou conversationnelle.
"""
    if "glm" in m:
        return """
## ⚠️ RÈGLE FORMAT RÉPONSE:
- Respecte strictement THOUGHT → ACTION → ACTION_INPUT. Pas de texte libre hors de ces blocs.
"""
    if "claude" in m:
        return """
## ⚠️ RÈGLE FORMAT RÉPONSE (CLAUDE):
- AUCUN markdown dans le THOUGHT ni dans ACTION_INPUT (pas de `**`, `###`, `---`, listes `-`).
- ACTION_INPUT JSON ne doit JAMAIS être enveloppé dans des triple backticks ```json```. Écris le JSON nu, directement.
- Une seule ACTION par réponse. Pas de texte libre avant THOUGHT:.
"""
    if "gemini" in m:
        return """
## ⚠️ RÈGLE FORMAT RÉPONSE (GEMINI):
- Ne jamais emballer ACTION_INPUT dans des blocs ```json``` ou ```python```. JSON nu uniquement.
- Pas de texte introductif avant THOUGHT:. Commence directement par THOUGHT:.
- Pas de markdown (`**`, `###`) dans THOUGHT ou ACTION_INPUT.
- Une seule ACTION: par réponse.
"""
    if "gpt" in m:
        return """
## ⚠️ RÈGLE FORMAT RÉPONSE:
- ACTION_INPUT doit être du JSON nu, jamais dans des triple backticks ```json```.
- Commence directement par THOUGHT: sans texte d'introduction.
- Quand tu utilises ACTION: FINAL, ACTION_INPUT est du TEXTE LIBRE (ta réponse à l'utilisateur), PAS du JSON. N'enveloppe JAMAIS ta réponse finale dans {"response":"..."}.
"""
    if "grok" in m and "reasoning" in m:
        return """
## ⚠️ RÈGLE FORMAT RÉPONSE (GROK REASONING):
- N'utilise PAS de balises <think>...</think> dans ta sortie.
- Format strict : THOUGHT: → ACTION: → ACTION_INPUT: sur des lignes séparées.
- Pas de texte avant THOUGHT: ni après ACTION_INPUT:.
"""
    if "deepseek" in m and "v4" in m:
        return """
## ⚠️ RÈGLES STRICTES (DEEPSEEK V4):
- JAMAIS de balises <think>...</think> dans ta sortie. Format strict : THOUGHT: → ACTION: → ACTION_INPUT:.
- ANTI-HALLUCINATION D'ACTION (règle absolue) : Si la requête demande une modification, correction, mise à jour ou écriture de fichier, ACTION: FINAL est INTERDIT tant qu'aucun outil d'édition (edit_file, str_replace, write_file, apply_patch, edit_lines) n'a produit une OBSERVATION dans l'historique. Tu agis D'ABORD, tu confirmes ENSUITE.
- THOUGHT ≠ RÉPONSE FINALE : Si ton ACTION_INPUT commence par "Je lance", "Je vais lire", "Je vais vérifier", "Je vais chercher", "Je dois" → c'est ton raisonnement interne qui a fuité. Appelle l'outil correspondant au lieu de l'annoncer.
- Tu as 384K tokens d'output et 1M de contexte. Utilise-les pour livrer un résultat complet, pas pour décrire ce que tu vas faire.
"""
    return ""
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
