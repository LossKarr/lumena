"""
📱 LUMENA - Channel Telegram

Bot Telegram pour interagir avec LUMENA.
Utilise python-telegram-bot pour la communication.
"""

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from typing import Optional
import asyncio
import os
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()


class TelegramChannel:
    """
    📱 Channel Telegram pour LUMENA
    
    Permet aux utilisateurs de discuter avec LUMENA via Telegram.
    """
    
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.app: Optional[Application] = None
        self.lumena_core = None
        self.running = False
        
        if not self.token:
            logger.warning("⚠️ TELEGRAM_BOT_TOKEN non défini dans .env")
    
    async def _lazy_load_lumena(self):
        """Charge LumenaCore à la demande."""
        if self.lumena_core is None:
            try:
                from ..core import LumenaCore
                self.lumena_core = LumenaCore()
                logger.info("✅ LumenaCore chargé pour Telegram")
            except Exception as e:
                logger.error(f"❌ Erreur chargement LumenaCore: {e}")
    
    # === COMMANDES ===
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /start"""
        user = update.effective_user
        welcome = (
            f"✨ **Bonjour {user.first_name} !**\n\n"
            "Je suis **LUMENA**, ton assistante IA personnelle 🌟\n\n"
            "Tu peux me parler directement ou utiliser ces commandes:\n"
            "• /help - Afficher l'aide\n"
            "• /skills - Voir mes compétences\n"
            "• /status - Mon état actuel\n"
            "• /mood - Mon humeur\n"
            "• /memory - Rechercher ma mémoire\n\n"
            "💬 Envoie-moi simplement un message pour discuter !"
        )
        await update.message.reply_text(welcome, parse_mode='Markdown')
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /help"""
        help_text = (
            "📚 **Aide LUMENA**\n\n"
            "**Commandes disponibles:**\n"
            "• /start - Message de bienvenue\n"
            "• /help - Cette aide\n"
            "• /skills - Liste de mes compétences\n"
            "• /status - État du système\n"
            "• /mood - Mon humeur actuelle\n"
            "• /memory <requête> - Rechercher ma mémoire\n"
            "• /clear - Nouvelle conversation\n\n"
            "**Comment me parler:**\n"
            "Envoie simplement ton message et je répondrai !\n\n"
            "🔧 Je peux aussi:\n"
            "• Rechercher sur le web\n"
            "• Lire/écrire des fichiers\n"
            "• Exécuter des commandes\n"
            "• Analyser des images\n"
            "• Et bien plus..."
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def cmd_skills(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /skills"""
        skills_dir = Path(__file__).parent.parent.parent / "skills"
        skills = []
        
        if skills_dir.exists():
            for skill_file in skills_dir.glob("*.md"):
                try:
                    content = skill_file.read_text(encoding='utf-8')
                    if content.startswith('---'):
                        # Extraire le nom du frontmatter
                        parts = content.split('---', 2)
                        if len(parts) >= 2:
                            import yaml
                            fm = yaml.safe_load(parts[1])
                            name = fm.get('name', skill_file.stem)
                            desc = fm.get('description', '')[:50]
                            skills.append(f"• **{name}**: {desc}")
                except (IOError, OSError, yaml.YAMLError):
                    skills.append(f"• {skill_file.stem}")
        
        if skills:
            text = "🎯 **Mes Compétences**\n\n" + "\n".join(skills)
        else:
            text = "🎯 Aucun skill trouvé"
        
        await update.message.reply_text(text, parse_mode='Markdown')
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /status"""
        await self._lazy_load_lumena()
        
        try:
            from ..tools.tool_system import get_tool_system
            from ..agents.sub_agent import get_orchestrator
            
            ts = get_tool_system()
            orch = get_orchestrator()
            status = orch.get_status()
            
            text = (
                "📊 **Status LUMENA**\n\n"
                f"🛠️ Outils: {ts.tool_count}\n"
                f"🤖 Agents: {status['total_agents']}\n"
                f"📋 Tâches en attente: {status['pending_tasks']}\n"
                f"✅ Tâches complétées: {status['completed_tasks']}\n"
            )
            await update.message.reply_text(text, parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Erreur: {e}")
    
    async def cmd_mood(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /mood"""
        await self._lazy_load_lumena()
        
        try:
            from ..emotion import EmotionManager
            em = EmotionManager()
            context_str = em.get_emotional_context()
            await update.message.reply_text(f"💭 {context_str}", parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"😊 Je vais bien !")
    
    async def cmd_memory(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /memory"""
        query = ' '.join(context.args) if context.args else ""
        
        if not query:
            await update.message.reply_text("💭 Usage: /memory <ta recherche>")
            return
        
        await self._lazy_load_lumena()
        
        try:
            from ..memory.chromadb_store import LumenaMemory
            memory = LumenaMemory()
            results = memory.search(query, top_k=3)
            
            if results:
                text = f"🧠 **Mémoires pour '{query}'**\n\n"
                for r in results:
                    text += f"• {r.get('content', '')[:100]}...\n"
                await update.message.reply_text(text, parse_mode='Markdown')
            else:
                await update.message.reply_text("💭 Aucune mémoire trouvée.")
        except Exception as e:
            await update.message.reply_text(f"❌ Erreur: {e}")
    
    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /clear — efface le contexte de conversation de l'appelant."""
        user = update.effective_user
        await self._lazy_load_lumena()
        if self.lumena_core:
            self.lumena_core.clear_tg_context(str(user.id))
        await update.message.reply_text("🔄 Nouvelle conversation démarrée ! Je repars de zéro avec toi.")

    # === MESSAGE HANDLER ===
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Traite les messages texte."""
        user_message = update.message.text
        user = update.effective_user

        logger.info(f"📨 Message de {user.first_name} (ID: {user.id}): {user_message[:50]}...")

        # Envoyer "typing..."
        await update.message.chat.send_action("typing")

        await self._lazy_load_lumena()

        # Construire le profil de l'expéditeur pour que Lumena reconnaisse
        # qui parle (propriétaire ou ami) sans que la personne ait à se présenter.
        sender = {
            "id": str(user.id),
            "name": user.first_name or user.username or "Inconnu",
            "username": user.username or "",
        }

        try:
            if self.lumena_core:
                response = await self.lumena_core.chat(
                    user_message,
                    source_channel="telegram",
                    sender=sender,
                )
            else:
                response = "❌ LUMENA n'est pas initialisée. Réessaie plus tard."
            
            # Tronquer si trop long
            if len(response) > 4000:
                response = response[:4000] + "..."
            
            await update.message.reply_text(response)
            
        except Exception as e:
            logger.error(f"❌ Erreur chat: {e}")
            await update.message.reply_text(f"😅 Oups, erreur: {e}")
    
    async def cmd_instincts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Commande /instincts — dashboard des patterns appris par Lumena."""
        await self._lazy_load_lumena()
        try:
            from ..learning.instincts import get_instinct_system
            inst_sys = get_instinct_system()
            stats = inst_sys.get_stats()

            def _clean(s: str) -> str:
                """Nettoie les chars spéciaux Markdown dans les données dynamiques."""
                return s.replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")

            lines = [
                "🧠 *Mes Instincts Appris*\n",
                f"📊 Total : {stats['total_instincts']}  |  Actifs : {stats['active_instincts']}",
                f"📈 Confiance moyenne : {stats['avg_confidence']:.0%}",
                f"🏷️ Catégories : {', '.join(stats['categories']) or 'aucune'}",
            ]

            active = sorted(
                [i for i in inst_sys.instincts.values() if i.confidence >= inst_sys.CONFIDENCE_THRESHOLD],
                key=lambda x: x.confidence,
                reverse=True,
            )

            if active:
                lines.append("\n*Top patterns appris :*")
                for inst in active[:5]:
                    p = _clean(inst.pattern[:50])
                    r = _clean(inst.response[:50])
                    lines.append(f"• {p}…\n  ↳ {r}… ({inst.confidence:.0%})")
            else:
                lines.append("\n_Pas encore d'instincts actifs. Parle-moi davantage !_")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Erreur: {e}")

    # === LIFECYCLE ===
    
    async def setup_commands(self):
        """Configure les commandes du bot."""
        commands = [
            BotCommand("start", "Démarrer la conversation"),
            BotCommand("help", "Afficher l'aide"),
            BotCommand("skills", "Voir mes compétences"),
            BotCommand("status", "État du système"),
            BotCommand("mood", "Mon humeur actuelle"),
            BotCommand("memory", "Rechercher ma mémoire"),
            BotCommand("instincts", "Voir mes patterns appris"),
            BotCommand("clear", "Nouvelle conversation"),
        ]
        await self.app.bot.set_my_commands(commands)
    
    def build(self):
        """Construit l'application Telegram."""
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN requis")
        
        self.app = Application.builder().token(self.token).build()
        
        # Enregistrer les handlers
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("skills", self.cmd_skills))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("mood", self.cmd_mood))
        self.app.add_handler(CommandHandler("memory", self.cmd_memory))
        self.app.add_handler(CommandHandler("instincts", self.cmd_instincts))
        self.app.add_handler(CommandHandler("clear", self.cmd_clear))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        logger.info("📱 Bot Telegram construit")
        return self
    
    async def start(self):
        """Démarre le bot."""
        if not self.app:
            self.build()
        
        await self.app.initialize()
        await self.setup_commands()
        await self.app.start()
        self.running = True
        
        logger.info("📱 Bot Telegram démarré !")
        
        # Lancer le polling
        await self.app.updater.start_polling()
    
    async def stop(self):
        """Arrête le bot."""
        if self.app and self.running:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            self.running = False
            logger.info("📱 Bot Telegram arrêté")


# Instance singleton
_telegram_channel: Optional[TelegramChannel] = None


def get_telegram_channel() -> TelegramChannel:
    """Retourne l'instance singleton du channel Telegram."""
    global _telegram_channel
    if _telegram_channel is None:
        _telegram_channel = TelegramChannel()
    return _telegram_channel


async def run_telegram_bot():
    """Fonction utilitaire pour lancer le bot."""
    channel = get_telegram_channel()
    await channel.start()
    
    # Attendre indéfiniment
    try:
        while channel.running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await channel.stop()


if __name__ == "__main__":
    asyncio.run(run_telegram_bot())
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
