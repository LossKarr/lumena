"""

🌟 LUMENA ULTIME - Version Complète avec Fine-Tuning LoRA

Combine :
- Le système complet (LumenaCore, mémoire, émotions, skills)
- Le modèle LoRA fine-tuné
- CLI interactive avec toutes les commandes
- Multi-Provider LLM (GPT, Claude, Gemini, Kimi)
"""

import asyncio
import torch
import json
import sys
import os
from pathlib import Path
from typing import Optional

# 🔧 Configuration UTF-8 pour Windows
if sys.platform == 'win32':
    # Forcer l'encodage UTF-8 sur Windows
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# Charger les variables d'environnement depuis .env
try:
    from dotenv import load_dotenv
    # Chercher le .env dans le dossier courant ou parent
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✅ Fichier .env chargé depuis {env_path}")
    else:
        # Essayer aussi le dossier parent
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
except ImportError:
    print("⚠️ python-dotenv non installé. Install avec: pip install python-dotenv")

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
    from rich.theme import Theme
    from rich.style import Style
    from rich.text import Text
    from rich.box import ROUNDED, DOUBLE
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("⚠️ 'rich' non installé. Install avec: pip install rich")

# 🌅 THÈME SOLEIL - Palette Lumena
LUMENA_THEME = Theme({
    "lumena.gold": "bold #F6C453",           # Or brillant - titres
    "lumena.amber": "#D4A84B",               # Ambre - bordures
    "lumena.text": "#E8D5A3",                # Jaune doré - texte
    "lumena.dim": "#8B7355",                 # Brun doré - texte secondaire
    "lumena.orange": "#E67E22",              # Orange coucher - accents
    "lumena.sun": "bold #FFB347",            # Soleil - Lumena parle
    "lumena.user": "#C49A4A",                # Orange brûlé - utilisateur
    "lumena.success": "#DAA520",             # Or foncé - succès
    "lumena.error": "#CD853F",               # Peru - erreurs
    "lumena.header": "bold #F6C453 on #1A1510",  # Header
})

console = Console(theme=LUMENA_THEME) if RICH_AVAILABLE else None

# Import du système complet
try:
    from src.core import LumenaCore, get_lumena
    from src.personality import Mood
    from src.autonomy.curiosity import _write_last_interaction, _read_last_interaction
    SYSTEM_AVAILABLE = True
except ImportError as e:
    SYSTEM_AVAILABLE = False
    _read_last_interaction = lambda: None  # No-op si pas disponible
    _write_last_interaction = lambda: None  # No-op si pas disponible
    print(f"⚠️ Système complet non disponible: {e}")

# Import système multi-provider
try:
    from src.llm.providers import (
        AVAILABLE_MODELS, get_model_config, check_api_key,
        ProviderType
    )
    from src.llm.multi_provider import MultiProviderLLM
    MULTI_PROVIDER_AVAILABLE = True
except ImportError:
    MULTI_PROVIDER_AVAILABLE = False

# Import des modules daemon (intégrés dans le CLI)
try:
    from src.autonomy.curiosity import CuriosityModule, get_curiosity_module
    from src.autonomy.heartbeat import HeartbeatSystem, get_heartbeat
    from src.autonomy.scheduler import LumenaScheduler
    from src.autonomy.goals import GoalManager, get_goal_manager
    from src.emotion import get_emotion_manager
    from loguru import logger
    DAEMON_MODULES_AVAILABLE = True
except ImportError as e:
    DAEMON_MODULES_AVAILABLE = False
    logger = None
    print(f"⚠️ Modules daemon non disponibles: {e}")



class LumenaUltime:
    """
    Lumena avec :
    - Modèle LoRA fine-tuné
    - Système complet (mémoire, émotions, skills)
    - Daemon intégré (curiosité, heartbeat, scheduler)
    """
    
    def __init__(self):
        self.lora_model = None
        self.lora_tokenizer = None
        self.lumena_core = None
        self.use_lora = False  # Si LoRA disponible
        
        # Modules daemon intégrés (pas de processus séparé)
        self.curiosity = None
        self.heartbeat = None
        self.scheduler = None
        self.goals = None
        self.emotions = None
        
        # État daemon
        self._daemon_running = False
        self._daemon_task = None
        self._telegram_task = None  # Task Telegram en arrière-plan
        self.telegram = None
        self._whatsapp_task = None  # Task WhatsApp en arrière-plan
        self.whatsapp = None
        self._discord_task = None   # Task Discord en arrière-plan
        self.discord_channel = None
        self.data_dir = Path(__file__).parent / "data"

        # Contrôles autonomie (même logique que daemon principal)
        self.enable_action_execution = os.getenv("LUMENA_AUTONOMY_EXECUTE_ACTIONS", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.autonomy_action_timeout_seconds = int(os.getenv("LUMENA_AUTONOMY_ACTION_TIMEOUT_SEC", "120"))
        self.progressive_mode_enabled = os.getenv("LUMENA_AUTONOMY_PROGRESSIVE_MODE", "1").strip().lower() in {"1", "true", "yes", "on"}
        self.allowed_action_types = self._parse_allowed_action_types(
            os.getenv("LUMENA_AUTONOMY_ALLOWED_ACTIONS", "EXPLORE_WEB,LEARN_SOMETHING,REFLECT,WRITE_DIARY,CHECK_NEWS")
        )
        self.user_idle_threshold_minutes = int(os.getenv("LUMENA_AUTONOMY_USER_IDLE_MINUTES", "10"))

    def _parse_allowed_action_types(self, raw: str) -> set[str]:
        allowed: set[str] = set()
        for item in (raw or "").split(","):
            value = item.strip().upper()
            if value:
                allowed.add(value)
        return allowed

    def _is_user_present_for_autonomy(self) -> bool:
        last = _read_last_interaction()
        if not last:
            return False
        idle_minutes = (datetime.now() - last).total_seconds() / 60
        return idle_minutes < max(1, self.user_idle_threshold_minutes)

    async def _execute_autonomous_action(self, action) -> bool:
        if not self.enable_action_execution:
            return False
        if self.progressive_mode_enabled and self.allowed_action_types:
            action_key = getattr(getattr(action, "action_type", None), "name", "").upper()
            if action_key and action_key not in self.allowed_action_types:
                if logger:
                    logger.debug(f"Action autonome bloquée par allowlist: {action_key}")
                return False

        action_type = getattr(getattr(action, "action_type", None), "value", "")
        metadata = getattr(action, "metadata", {}) or {}
        topic = str(metadata.get("topic", "un sujet utile"))

        prompt = None
        if action_type == "explore_web":
            prompt = f"Explore brièvement le web sur {topic} et résume les points clés."
        elif action_type == "learn_something":
            prompt = f"Apprends quelque chose sur {topic} puis note un résumé actionnable."
        elif action_type == "reflect":
            prompt = "Fais une réflexion courte sur les dernières interactions et propose une amélioration concrète."
        elif action_type == "write_diary":
            prompt = "Écris une note de journal concise sur ce que tu as appris récemment."
        elif action_type == "check_news":
            prompt = "Fais un check d'actualités général et extrais 3 éléments pertinents."

        if not prompt or not self.lumena_core:
            return False

        try:
            response = await asyncio.wait_for(
                self.lumena_core.think_and_act(prompt),
                timeout=max(10, self.autonomy_action_timeout_seconds),
            )
            if logger:
                logger.info(f"✅ Action autonome exécutée ({action_type}): {str(response)[:140]}")
            return True
        except asyncio.TimeoutError:
            if logger:
                logger.warning(f"⏱️ Timeout action autonome: {action_type}")
            return False
        except Exception as e:
            if logger:
                logger.error(f"Erreur action autonome {action_type}: {e}")
            return False
        
    def load_lora_model(self) -> bool:
        """Charge le modèle LoRA si disponible."""
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
            from peft import PeftModel
            
            if console:
                console.print("\n[cyan]📦 Chargement du modèle LoRA...[/cyan]")
            
            base_model_name = "Qwen/Qwen2.5-3B-Instruct"
            lora_path = Path(__file__).parent / "models" / "lumena-lora"
            
            if not lora_path.exists():
                if console:
                    console.print("[yellow]⚠️ Modèle LoRA non trouvé, utilisation d'Ollama[/yellow]")
                return False
            
            # Config 4-bit
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
            )
            
            # Charger modèle de base
            self.lora_tokenizer = AutoTokenizer.from_pretrained(base_model_name, trust_remote_code=True)
            base_model = AutoModelForCausalLM.from_pretrained(
                base_model_name,
                quantization_config=bnb_config,
                device_map="auto",
                trust_remote_code=True,
            )
            
            # Charger adaptateurs LoRA
            if console:
                console.print("[cyan]🔧 Chargement des adaptateurs LoRA...[/cyan]")
            self.lora_model = PeftModel.from_pretrained(base_model, str(lora_path))
            self.lora_model.eval()
            
            if console:
                console.print("[green]✅ Modèle LoRA chargé ![/green]")
            
            self.use_lora = True
            return True
            
        except Exception as e:
            if console:
                console.print(f"[yellow]⚠️ LoRA non disponible: {e}[/yellow]")
            return False
    
    @staticmethod
    def _check_requirements() -> None:
        """Vérifie la présence des paquets critiques au démarrage et affiche un avertissement si manquants."""
        _critical = {
            "httpx": "httpx",
            "chromadb": "chromadb",
            "aiohttp": "aiohttp",
            "loguru": "loguru",
            "telegram": "python-telegram-bot",
            "pydantic": "pydantic",
        }
        missing = []
        for module_name, pkg_name in _critical.items():
            try:
                __import__(module_name)
            except ImportError:
                missing.append(pkg_name)
        if missing:
            msg = f"⚠️ Paquets manquants: {', '.join(missing)}\n   → pip install {' '.join(missing)}"
            if console:
                console.print(f"[yellow]{msg}[/yellow]")
            else:
                print(msg)

    async def initialize(self) -> bool:
        """Initialise le système complet."""
        self._check_requirements()
        if console:
            console.print("\n[cyan]🚀 Initialisation de LUMENA ULTIME...[/cyan]")
        
        # 1. Charger le système complet (LumenaCore)
        if SYSTEM_AVAILABLE:
            try:
                # Ne pas recréer si déjà configuré (provider cloud)
                if not self.lumena_core:
                    self.lumena_core = get_lumena()
                await self.lumena_core.initialize()
                if console:
                    console.print("[green]✅ Système complet initialisé (mémoire, émotions, skills)[/green]")
            except Exception as e:
                if console:
                    console.print(f"[yellow]⚠️ Système complet non disponible: {e}[/yellow]")
        
        # 2. Charger le modèle LoRA
        self.load_lora_model()
        
        # 3. Initialiser les modules daemon INTÉGRÉS (un seul processus)
        if DAEMON_MODULES_AVAILABLE:
            try:
                # Curiosité
                self.curiosity = get_curiosity_module()
                
                # Émotions
                self.emotions = get_emotion_manager()
                
                # Goals
                self.goals = get_goal_manager(self.data_dir / "goals")
                
                # Scheduler
                self.scheduler = LumenaScheduler(self.data_dir / "scheduler")
                self.scheduler.setup_default_tasks()
                await self.scheduler.start()
                
                # Heartbeat
                self.heartbeat = get_heartbeat(
                    workspace_dir=self.data_dir.parent,
                    on_task_callback=self._handle_heartbeat_task
                )
                await self.heartbeat.start()
                
                if console:
                    console.print("[green]🧠 Daemon intégré démarré (curiosité, heartbeat, scheduler)[/green]")
                    
                # Démarrer la boucle daemon en arrière-plan
                self._daemon_running = True
                self._daemon_task = asyncio.create_task(self._daemon_loop())
                
            except Exception as e:
                if console:
                    console.print(f"[yellow]⚠️ Modules daemon non initialisés: {e}[/yellow]")
        
        # 5. Démarrer Telegram en arrière-plan (si configuré)
        try:
            from src.channels.telegram_channel import TelegramChannel
            
            self.telegram = TelegramChannel()
            if self.telegram.is_available:
                # Créer le callback pour traiter les messages Telegram
                async def telegram_callback(msg):
                    response = await self.chat(msg.content)
                    return response
                
                self.telegram.set_message_callback(telegram_callback)
                
                # Démarrer le bot Telegram en arrière-plan
                self._telegram_task = asyncio.create_task(self._run_telegram())
                
                if console:
                    console.print("[green]📱 Bot Telegram démarré en arrière-plan[/green]")
            else:
                if console:
                    console.print("[lumena.dim]📱 Telegram non configuré (TELEGRAM_TOKEN manquant dans .env)[/lumena.dim]")
        except ImportError:
            if console:
                console.print("[lumena.dim]📱 Module Telegram non disponible[/lumena.dim]")
        except Exception as e:
            if console:
                console.print(f"[yellow]⚠️ Telegram non démarré: {e}[/yellow]")
        
        # 5b. Démarrer WhatsApp en arrière-plan (si configuré)
        try:
            from src.channels.whatsapp_channel import WhatsAppChannel
            
            self.whatsapp = WhatsAppChannel()
            if self.whatsapp.is_available:
                async def whatsapp_callback(msg):
                    response = await self.chat(msg.content)
                    return response
                
                self.whatsapp.set_message_callback(whatsapp_callback)
                self._whatsapp_task = asyncio.create_task(self._run_whatsapp())
                
                if console:
                    console.print("[green]📱 WhatsApp démarré en arrière-plan[/green]")
            else:
                if console:
                    console.print("[lumena.dim]📱 WhatsApp non configuré (WHATSAPP_ACCESS_TOKEN manquant dans .env)[/lumena.dim]")
        except ImportError:
            if console:
                console.print("[lumena.dim]📱 Module WhatsApp non disponible[/lumena.dim]")
        except Exception as e:
            if console:
                console.print(f"[yellow]⚠️ WhatsApp non démarré: {e}[/yellow]")
        
        # 6. Démarrer Discord en arrière-plan (si configuré)
        try:
            from src.channels.discord_channel import DiscordChannel
            
            self.discord_channel = DiscordChannel()
            if self.discord_channel.is_available:
                _lumena_core = self.lumena_core
                
                async def discord_stream_callback(msg):
                    async for token in _lumena_core.chat_stream(
                        msg.content,
                        source_channel="discord",
                        channel_id=msg.channel_id,
                        user_id=msg.user_id,
                        username=msg.username,
                        is_admin=msg.metadata.get("is_discord_admin", False),
                        channel_name=msg.metadata.get("channel_name"),
                        channel_topic=msg.metadata.get("channel_topic"),
                        available_channels=msg.metadata.get("available_channels"),
                        active_users=msg.metadata.get("active_users_in_channel"),
                        image_paths=msg.metadata.get("discord_image_paths"),
                    ):
                        yield token
                
                self.discord_channel.set_stream_callback(discord_stream_callback)
                self._discord_task = asyncio.create_task(self._run_discord())
                
                if console:
                    console.print("[green]🎮 Bot Discord démarré en arrière-plan[/green]")
            else:
                if console:
                    console.print("[lumena.dim]🎮 Discord non configuré (DISCORD_TOKEN manquant dans .env)[/lumena.dim]")
        except ImportError:
            if console:
                console.print("[lumena.dim]🎮 Module Discord non disponible[/lumena.dim]")
        except Exception as e:
            if console:
                console.print(f"[yellow]⚠️ Discord non démarré: {e}[/yellow]")
        
        return True
    
    async def _daemon_loop(self):
        """Boucle daemon intégrée - s'exécute en arrière-plan pendant le CLI."""
        if logger:
            logger.info("🔄 Boucle daemon intégrée démarrée")
        
        while self._daemon_running:
            try:
                # Mettre à jour la curiosité (sans bloquer l'interaction)
                if self.curiosity:
                    user_present = self._is_user_present_for_autonomy()
                    action = self.curiosity.update(user_present=user_present)
                    if action and not user_present:
                        await self._execute_autonomous_action(action)
                
                # Mettre à jour les émotions passivement
                if self.emotions:
                    mood_change = self.emotions.update_passive()
                    if mood_change and logger:
                        logger.debug(f"🎭 {mood_change}")
                
                # Attendre 30 secondes avant le prochain check
                await asyncio.sleep(30)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                if logger:
                    logger.error(f"Erreur boucle daemon: {e}")
                await asyncio.sleep(5)
        
        if logger:
            logger.info("🛑 Boucle daemon arrêtée")
    
    def _handle_heartbeat_task(self, task):
        """Callback pour les tâches heartbeat."""
        if logger:
            logger.info(f"💓 Tâche heartbeat: {task.get('id', 'unknown')}")
    
    async def _run_telegram(self):
        """Exécute le bot Telegram en arrière-plan."""
        try:
            if self.telegram:
                success = await self.telegram.start()
                if success:
                    while self.telegram and self.telegram.is_running:
                        await asyncio.sleep(1)
        except asyncio.CancelledError:
            if self.telegram:
                await self.telegram.stop()
        except Exception as e:
            if logger:
                logger.error(f"Erreur Telegram: {e}")

    async def _run_whatsapp(self):
        """Exécute le canal WhatsApp en arrière-plan."""
        try:
            if self.whatsapp:
                success = await self.whatsapp.start()
                if success:
                    while self.whatsapp and self.whatsapp.is_running:
                        await asyncio.sleep(1)
        except asyncio.CancelledError:
            if self.whatsapp:
                await self.whatsapp.stop()
        except Exception as e:
            if logger:
                logger.error(f"Erreur WhatsApp: {e}")

    async def _run_discord(self):
        """Exécute le bot Discord en arrière-plan."""
        try:
            if self.discord_channel:
                success = await self.discord_channel.start()
                if success:
                    while self.discord_channel and self.discord_channel.is_running:
                        await asyncio.sleep(1)
        except asyncio.CancelledError:
            if self.discord_channel:
                await self.discord_channel.stop()
        except Exception as e:
            if logger:
                logger.error(f"Erreur Discord: {e}")
    
    async def shutdown(self):
        """Arrête proprement tous les modules."""
        self._daemon_running = False
        
        if self._daemon_task:
            self._daemon_task.cancel()
            try:
                await self._daemon_task
            except asyncio.CancelledError:
                pass
        
        if self.heartbeat:
            await self.heartbeat.stop()
        
        if self.scheduler:
            await self.scheduler.stop()
        
        # Arrêter Telegram
        if self._telegram_task:
            self._telegram_task.cancel()
            try:
                await self._telegram_task
            except asyncio.CancelledError:
                pass
        
        if self.telegram:
            await self.telegram.stop()
        
        # Arrêter WhatsApp
        if self._whatsapp_task:
            self._whatsapp_task.cancel()
            try:
                await self._whatsapp_task
            except asyncio.CancelledError:
                pass
        
        if self.whatsapp:
            await self.whatsapp.stop()
        
        # Arrêter Discord
        if self._discord_task:
            self._discord_task.cancel()
            try:
                await self._discord_task
            except asyncio.CancelledError:
                pass
        
        if self.discord_channel:
            await self.discord_channel.stop()
        
        if console:
            console.print("[yellow]👋 LUMENA arrêtée proprement[/yellow]")
    
    def generate_with_lora(self, message: str) -> str:
        """Génère une réponse avec le modèle LoRA."""
        if not self.lora_model:
            return None
            
        # Format Qwen
        messages = [{"role": "user", "content": message}]
        text = self.lora_tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.lora_tokenizer(text, return_tensors="pt").to(self.lora_model.device)
        
        with torch.no_grad():
            outputs = self.lora_model.generate(
                **inputs,
                max_new_tokens=200,
                temperature=0.7,
                do_sample=True,
                top_p=0.9,
                repetition_penalty=1.2,
                pad_token_id=self.lora_tokenizer.eos_token_id,
            )
        
        response = self.lora_tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
        
        # Nettoyer
        response = response.replace("Qwen", "Lumena").replace("Alibaba", "Lumena")
        lines = response.split('\n')
        return lines[0].strip() if lines else response.strip()
    
    async def chat(self, message: str) -> str:
        """
        Génère une réponse en utilisant :
        1. Le système complet (mémoire, émotions) si disponible
        2. Le modèle LoRA si disponible
        3. Ollama sinon
        """
        response = None
        
        # Notifier le daemon qu'il y a une interaction CLI (sync inter-process)
        _write_last_interaction()
        
        # Si système complet disponible, l'utiliser pour la mémoire et les émotions
        if self.lumena_core:
            # Le système complet gère mémoire, émotions, contexte
            response = await self.lumena_core.chat(message)
        elif self.use_lora:
            # Sinon, utiliser LoRA directement
            response = self.generate_with_lora(message)
        else:
            response = "Je ne suis pas encore complètement initialisée..."
        
        return response
    
    async def think_and_act(self, query: str) -> str:
        """Mode agent avec skills."""
        if self.lumena_core:
            response = await self.lumena_core.think_and_act(query)
        else:
            response = "Mode agent non disponible sans le système complet."
        
        return response
    
    def get_status(self) -> dict:
        """Retourne l'état de LUMENA."""
        status = {
            "lora": self.use_lora,
            "system": self.lumena_core is not None,
        }
        
        if self.lumena_core:
            status.update(self.lumena_core.get_emotion_stats())
        
        return status


async def run_cli(lumena: LumenaUltime):
    """Lance la CLI interactive."""
    
    if console:
        # 🌅 Panel de bienvenue thème Soleil
        console.print(Panel.fit(
            "[lumena.gold]☀️ LUMENA ULTIME[/lumena.gold]\n"
            "[lumena.dim]Version complète avec Fine-Tuning LoRA[/lumena.dim]\n\n"
            "[lumena.text]Commandes:[/lumena.text]\n"
            "  /help   - Aide\n"
            "  /status - État de LUMENA\n"
            "  /mood   - Humeur actuelle\n"
            "  /quit   - Quitter\n\n"
            "[lumena.text]Mode agent:[/lumena.text] préfixe avec ! (ex: !ouvre notepad)",
            border_style="lumena.amber"
        ))
    
    # Salutation
    greet = await lumena.chat("Bonjour !")
    if console:
        console.print(f"\n[lumena.sun]☀️ Lumena:[/lumena.sun] {greet}\n")
    else:
        print(f"\nLumena: {greet}\n")
    
    while True:
        try:
            if console:
                user_input = Prompt.ask("[lumena.user]☀ Toi[/lumena.user]")
            else:
                user_input = input("Vous: ")
            
            user_input = user_input.strip()
            
            if not user_input:
                continue
            
            # Commandes
            if user_input.lower() in ["/quit", "/exit", "/q"]:
                if console:
                    console.print("[lumena.text]👋 Au revoir ![/lumena.text]")
                break
            
            if user_input.lower() == "/help":
                help_text = """
[lumena.gold]📋 COMMANDES DE BASE:[/lumena.gold]
  /help    - Affiche cette aide
  /status  - État de LUMENA (LoRA, Unity, etc.)
  /mood    - Humeur actuelle
  /clear   - Efface la conversation
  /quit    - Quitter

[lumena.gold]📋 COMMANDES MÉMOIRE:[/lumena.gold]
  /memory        - Statistiques mémoire
  /recall <mot>  - Cherche dans les souvenirs
  /skills        - Voir les compétences

[lumena.gold]📋 COMMANDES OUTILS:[/lumena.gold]
  /tools   - Liste des 30 outils
  /voice   - Mode vocal (TTS)

[lumena.gold]🤖 MODE AGENT (préfixe !):[/lumena.gold]
  !quelle heure est-il ?
  !ouvre le bloc-notes
  !cherche Python sur internet
  !prends une capture d'écran

[lumena.gold]💾 MÉMOIRE PERSISTANTE:[/lumena.gold]
  Dites "n'oublie pas que..." pour mémoriser
  Lumena se souvient même après redémarrage !
"""
                if console:
                    console.print(Panel(help_text, title="☀️ Aide LUMENA", border_style="lumena.amber"))
                else:
                    print(help_text)
                continue
            
            if user_input.lower() == "/tools":
                tools_text = """
[lumena.gold]🛠️ 30 OUTILS DISPONIBLES:[/lumena.gold]

[lumena.orange]📁 Fichiers:[/lumena.orange]
  • read_file     - Lit un fichier
  • write_file    - Écrit dans un fichier
  • run_command   - Exécute une commande shell

[lumena.orange]🌐 Web:[/lumena.orange]
  • web_search    - Recherche Google
  • web_fetch     - Récupère contenu d'une page
  • open_url      - Ouvre une URL

[lumena.orange]💾 Mémoire:[/lumena.orange]
  • memory_search - Recherche dans les souvenirs
  • memory_get    - Récupère des souvenirs

[lumena.orange]🖱️ Souris:[/lumena.orange]
  • click         - Clic à une position
  • double_click  - Double-clic
  • move_mouse    - Déplace la souris
  • scroll        - Scroll haut/bas
  • drag          - Glisser-déposer

[lumena.orange]⌨️ Clavier:[/lumena.orange]
  • type_text     - Tape du texte
  • press_key     - Appuie sur une touche
  • hotkey        - Raccourci (ctrl+c, etc.)

[lumena.orange]🪟 Fenêtres:[/lumena.orange]
  • get_active_window - Fenêtre active
  • list_windows      - Liste les fenêtres
  • close_window      - Ferme la fenêtre
  • open_app          - Ouvre une application

[lumena.orange]📌 Autres:[/lumena.orange]
  • get_time      - Heure actuelle
  • screenshot    - Capture d'écran
"""
                if console:
                    console.print(Panel(tools_text, title="🛠️ Outils", border_style="lumena.amber"))
                else:
                    print(tools_text)
                continue
            
            if user_input.lower() == "/status":
                status = lumena.get_status()
                status_text = f"""
[bold]État de LUMENA:[/bold]
  • Modèle LoRA: {'✅' if status.get('lora') else '❌'}
  • Système complet: {'✅' if status.get('system') else '❌'}
  • WebSocket Unity: {'✅' if status.get('unity') else '❌'}
  • Humeur: {status.get('mood', 'neutral')}
  • Énergie: {status.get('energy', 'medium')}
"""
                if console:
                    console.print(Panel(status_text, title="Status", border_style="magenta"))
                else:
                    print("\nÉtat de LUMENA:")
                    print(f"  • Modèle LoRA: {'✅' if status.get('lora') else '❌'}")
                    print(f"  • Système complet: {'✅' if status.get('system') else '❌'}")
                    print(f"  • WebSocket Unity: {'✅' if status.get('unity') else '❌'}")
                    print(f"  • Humeur: {status.get('mood', 'neutral')}")
                    print(f"  • Énergie: {status.get('energy', 'medium')}\n")
                continue
            
            if user_input.lower() == "/mood":
                status = lumena.get_status()
                mood = status.get('mood', 'neutral')
                if console:
                    console.print(f"\n[cyan]Humeur actuelle:[/cyan] {mood}\n")
                continue
            
            if user_input.lower() == "/clear":
                if lumena.lumena_core:
                    lumena.lumena_core.clear_context()
                if console:
                    console.print("[green]✓ Conversation effacée[/green]")
                continue
            
            # Commande /memory
            if user_input.lower() == "/memory":
                if lumena.lumena_core and hasattr(lumena.lumena_core, 'memory') and lumena.lumena_core.memory:
                    stats = lumena.lumena_core.memory.get_stats()
                    memory_text = f"""
[bold]💾 Mémoire de Lumena:[/bold]

📊 **Statistiques:**
  • Total souvenirs: {stats.get('count', 0)}
  • Sémantiques: {stats.get('semantic', 0)}
  • Épisodiques: {stats.get('episodic', 0)}
  • Procéduraux: {stats.get('procedural', 0)}

📂 Fichiers: data/memory/journal/
💡 Utilisez /recall <mot> pour chercher
"""
                    if console:
                        console.print(Panel(memory_text, title="💾 Mémoire", border_style="magenta"))
                else:
                    if console:
                        console.print("[yellow]Mémoire non disponible[/yellow]")
                continue
            
            # Commande /memory search
            if user_input.lower().startswith("/memory search "):
                query = user_input[15:].strip()
                if query and lumena.lumena_core and lumena.lumena_core.memory:
                    memories = lumena.lumena_core.memory.recall(query, limit=5)
                    if memories:
                        result = f"[bold]🔍 Résultats pour '{query}':[/bold]\n\n"
                        for i, mem in enumerate(memories, 1):
                            content = mem.content[:200] + "..." if len(mem.content) > 200 else mem.content
                            result += f"{i}. {content}\n\n"
                        if console:
                            console.print(Panel(result, title="Recherche", border_style="cyan"))
                    else:
                        if console:
                            console.print(f"[yellow]Aucun souvenir pour '{query}'[/yellow]")
                continue
            
            # Commande /recall (raccourci)
            if user_input.lower().startswith("/recall "):
                query = user_input[8:].strip()
                if query and lumena.lumena_core and lumena.lumena_core.memory:
                    memories = lumena.lumena_core.memory.recall(query, limit=5)
                    if memories:
                        result = f"[bold]🧠 Je me souviens de '{query}':[/bold]\n\n"
                        for i, mem in enumerate(memories, 1):
                            content = mem.content[:200] + "..." if len(mem.content) > 200 else mem.content
                            result += f"{i}. {content}\n\n"
                        if console:
                            console.print(Panel(result, title="💭 Souvenirs", border_style="lumena.amber"))
                    else:
                        if console:
                            console.print(f"[lumena.dim]Je n'ai pas de souvenir de '{query}'...[/lumena.dim]")
                else:
                    if console:
                        console.print("[lumena.dim]Usage: /recall <ce que tu cherches>[/lumena.dim]")
                continue
            
            # Commande /skills
            if user_input.lower() == "/skills":
                if lumena.lumena_core and hasattr(lumena.lumena_core, '_skills') and lumena.lumena_core._skills:
                    skills_text = "[bold]🎯 Compétences de Lumena:[/bold]\n\n"
                    for name, content in lumena.lumena_core._skills.items():
                        desc = "Pas de description"
                        if "description:" in content:
                            for line in content.split("\n"):
                                if line.startswith("description:"):
                                    desc = line.replace("description:", "").strip()
                                    break
                        skills_text += f"• [lumena.orange]{name}[/lumena.orange] - {desc}\n"
                    skills_text += "\n[dim]Ces skills guident mes réponses automatiquement ![/dim]"
                    if console:
                        console.print(Panel(skills_text, title="🎯 Skills", border_style="lumena.amber"))
                else:
                    if console:
                        console.print("[lumena.dim]Aucun skill chargé[/lumena.dim]")
                continue
            
            # Mode agent
            if user_input.startswith("!"):
                query = user_input[1:].strip()
                if query:
                    if console:
                        console.print("[lumena.dim]Mode agent activé...[/lumena.dim]")
                    response = await lumena.think_and_act(query)
                    if console:
                        console.print(f"\n[lumena.sun]☀️ Lumena:[/lumena.sun] {response}\n")
                    else:
                        print(f"\nLumena: {response}\n")
                continue
            
            # Chat normal
            if console:
                with console.status("[lumena.text]Lumena réfléchit...[/lumena.text]", spinner="dots"):
                    response = await lumena.chat(user_input)
                console.print(f"\n[lumena.sun]☀️ Lumena:[/lumena.sun] {response}\n")
            else:
                print("Lumena réfléchit...")
                response = await lumena.chat(user_input)
                print(f"\nLumena: {response}\n")
            
        except KeyboardInterrupt:
            if console:
                console.print("\n[lumena.text]👋 Au revoir ![/lumena.text]")
            break
        except Exception as e:
            if console:
                console.print(f"[lumena.error]Erreur: {e}[/lumena.error]")
            else:
                print(f"Erreur: {e}")

def select_model_menu() -> str:
    """
    Affiche le menu de sélection de modèle au démarrage.
    
    Returns:
        Nom du modèle sélectionné
    """
    if not MULTI_PROVIDER_AVAILABLE:
        return "qwen3-8b"  # Fallback
    
    # Organiser les modèles par catégorie
    local_models = []
    free_cloud_models = []
    paid_cloud_models = []
    
    for name, config in AVAILABLE_MODELS.items():
        if config.provider == ProviderType.OLLAMA:
            local_models.append((name, config))
        elif config.cost_per_million_tokens == 0:
            free_cloud_models.append((name, config))
        else:
            paid_cloud_models.append((name, config))
    
    if console and RICH_AVAILABLE:
        console.print()
        console.print(Panel.fit(
            "[lumena.gold]☀️ SÉLECTION DU MODÈLE LLM[/lumena.gold]\n"
            "[lumena.dim]Choisissez le cerveau de Lumena pour cette session[/lumena.dim]",
            border_style="lumena.amber"
        ))
        
        # Créer le tableau avec thème Soleil
        table = Table(show_header=True, header_style="lumena.gold")
        table.add_column("#", style="lumena.dim", width=3)
        table.add_column("Modèle", style="lumena.text")
        table.add_column("Provider", style="lumena.orange")
        table.add_column("Contexte", style="lumena.amber")
        table.add_column("Coût", style="lumena.dim")
        table.add_column("Status", style="lumena.success")
        
        idx = 1
        model_map = {}
        
        # Section Local
        table.add_row("[bold]", "[bold]━━━ LOCAL (Gratuit, Offline) ━━━", "", "", "", "")
        for name, config in local_models:
            status = "✅ Prêt" 
            table.add_row(
                str(idx),
                config.display_name,
                config.provider.value,
                f"{config.context_window // 1000}K",
                "Gratuit",
                status
            )
            model_map[str(idx)] = name
            idx += 1
        
        # Section Cloud Gratuit
        table.add_row("[bold]", "[bold]━━━ CLOUD (Gratuit avec limites) ━━━", "", "", "", "")
        for name, config in free_cloud_models:
            has_key = check_api_key(config.provider)
            status = "✅ Clé OK" if has_key else "⚠️ Clé manquante"
            table.add_row(
                str(idx),
                config.display_name,
                config.provider.value,
                f"{config.context_window // 1000}K",
                "Gratuit*",
                status
            )
            model_map[str(idx)] = name
            idx += 1
        
        # Section Cloud Payant
        table.add_row("[bold]", "[bold]━━━ CLOUD (Payant, Premium) ━━━", "", "", "", "")
        for name, config in paid_cloud_models:
            has_key = check_api_key(config.provider)
            status = "✅ Clé OK" if has_key else "⚠️ Clé manquante"
            table.add_row(
                str(idx),
                config.display_name,
                config.provider.value,
                f"{config.context_window // 1000}K",
                f"~${config.cost_per_million_tokens}/M",
                status
            )
            model_map[str(idx)] = name
            idx += 1
        
        console.print(table)
        console.print()
        console.print("[dim]* Les modèles cloud gratuits ont des limites de requêtes[/dim]")
        console.print("[dim]⚠️ Pour les modèles cloud, configurez les clés API dans .env[/dim]")
        console.print()
        
        # Demander le choix
        choice = Prompt.ask(
            "[lumena.gold]☀ Votre choix[/lumena.gold]",
            default="1",
            console=console
        )
        
        selected = model_map.get(choice, "qwen3-8b")
        config = get_model_config(selected)
        
        # Vérifier si le modèle cloud a sa clé
        if config and not config.is_local() and not check_api_key(config.provider):
            console.print(f"\n[lumena.orange]⚠️ Clé API manquante pour {config.provider.value}![/lumena.orange]")
            console.print(f"[lumena.dim]Configurez {config.provider.value.upper()}_API_KEY dans votre .env[/lumena.dim]")
            console.print("[lumena.dim]Fallback vers Qwen 3 8B (local)[/lumena.dim]\n")
            return "qwen3-8b"
        
        console.print(f"\n[lumena.success]✔ Modèle sélectionné: {config.display_name if config else selected}[/lumena.success]\n")
        return selected
    
    else:
        # Version sans Rich
        print("\n🧠 SÉLECTION DU MODÈLE:")
        print("1. Qwen 3 8B (Local)")
        print("2. Gemini 3 Pro (Google)")
        print("3. GPT-4o (OpenAI)")
        print("4. Claude Sonnet 4 (Anthropic)")
        print("5. Kimi K2.5 (Moonshot)")
        
        choice = input("\nVotre choix [1]: ").strip() or "1"
        
        choices = {
            "1": "qwen3-8b",
            "2": "gemini-3-pro",
            "3": "gpt-4o",
            "4": "claude-sonnet-4",
            "5": "kimi-k2.5"
        }
        
        return choices.get(choice, "qwen3-8b")


async def main():
    if console:
        console.print("\n")
        # 🌅 HEADER THÈME SOLEIL
        console.print(Panel.fit(
            "[lumena.gold]╔═══════════════════════════════════════════════════╗[/lumena.gold]\n"
            "[lumena.gold]║    ☀️  Lumena v1.0.0 - Beta 2026 - Daemon Intégré    ║[/lumena.gold]\n"
            "[lumena.gold]╚═══════════════════════════════════════════════════╝[/lumena.gold]\n\n"
            "[lumena.text]• Modèle LoRA fine-tuné[/lumena.text]\n"
            "[lumena.text]• Mémoire ChromaDB persistante[/lumena.text]\n"
            "[lumena.text]• Émotions & Personnalité[/lumena.text]\n"
            "[lumena.text]• Mode Agent (30 outils)[/lumena.text]\n"
            "[lumena.text]• WebSocket Unity[/lumena.text]\n"
            "[lumena.text]• Multi-Provider (GPT, Claude, Gemini, Kimi)[/lumena.text]",
            border_style="lumena.amber",
            box=DOUBLE
        ))
    
    # 🧠 MENU DE SÉLECTION DU MODÈLE
    selected_model = select_model_menu()
    
    # ⚠️ IMPORTANT: Configurer le provider AVANT de créer LumenaCore
    # Pour que get_lumena() utilise déjà le bon modèle
    if MULTI_PROVIDER_AVAILABLE and selected_model != "qwen3-8b":
        # Créer et configurer le MultiProviderLLM globalement
        try:
            from src.core import _lumena_instance
            # Forcer la création avec le bon modèle
            if console:
                config = get_model_config(selected_model)
                console.print(f"[lumena.text]☀ Cerveau sélectionné: {config.display_name if config else selected_model}[/lumena.text]")
        except ImportError:
            pass
    
    lumena = LumenaUltime()
    lumena.selected_model = selected_model
    
    # Appliquer le provider AVANT l'initialisation
    if MULTI_PROVIDER_AVAILABLE and selected_model != "qwen3-8b":
        try:
            # Créer le LumenaCore avec le bon modèle
            from src.core import LumenaCore
            lumena.lumena_core = LumenaCore()
            lumena.lumena_core.llm = MultiProviderLLM(model_name=selected_model)
            if console:
                console.print(f"[lumena.success]✔ Provider {lumena.lumena_core.llm.provider.value} configuré[/lumena.success]")
        except Exception as e:
            if console:
                console.print(f"[lumena.orange]⚠️ Erreur provider: {e}. Fallback par défaut.[/lumena.orange]")
    
    # Maintenant initialiser
    await lumena.initialize()
    
    await run_cli(lumena)
    
    # Cleanup - arrêter le daemon intégré et tous les modules
    await lumena.shutdown()
    if lumena.lumena_core:
        await lumena.lumena_core.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
