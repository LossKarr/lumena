"""
🎮 LUMENA - Channel Discord

Bot Discord pour interagir avec LUMENA.
Utilise discord.py pour la communication.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import asyncio
import os
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()


class DiscordChannel(commands.Bot):
    """
    🎮 Channel Discord pour LUMENA
    
    Permet aux utilisateurs de discuter avec LUMENA via Discord.
    """
    
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("DISCORD_BOT_TOKEN")
        
        # Configuration des intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            description="LUMENA - Assistante IA Autonome"
        )
        
        self.lumena_core = None
        
        if not self.token:
            logger.warning("⚠️ DISCORD_BOT_TOKEN non défini dans .env")
    
    async def _lazy_load_lumena(self):
        """Charge LumenaCore à la demande."""
        if self.lumena_core is None:
            try:
                from ..core import LumenaCore
                self.lumena_core = LumenaCore()
                logger.info("✅ LumenaCore chargé pour Discord")
            except Exception as e:
                logger.error(f"❌ Erreur chargement LumenaCore: {e}")
    
    async def setup_hook(self):
        """Configuration initiale du bot."""
        await self._register_commands()
        logger.info("🎮 Setup Discord terminé")
    
    async def _register_commands(self):
        """Enregistre les slash commands."""
        
        @self.tree.command(name="lumena", description="Parler à LUMENA")
        async def lumena_cmd(interaction: discord.Interaction, message: str):
            await interaction.response.defer()
            await self._lazy_load_lumena()
            
            try:
                if self.lumena_core:
                    response = await self.lumena_core.chat(message)
                else:
                    response = "❌ LUMENA n'est pas initialisée."
                
                # Tronquer si trop long
                if len(response) > 2000:
                    response = response[:1997] + "..."
                
                await interaction.followup.send(response)
            except Exception as e:
                await interaction.followup.send(f"❌ Erreur: {e}")
        
        @self.tree.command(name="skills", description="Voir les compétences de LUMENA")
        async def skills_cmd(interaction: discord.Interaction):
            skills_dir = Path(__file__).parent.parent.parent / "skills"
            skills = []
            
            if skills_dir.exists():
                for skill_file in skills_dir.glob("*.md"):
                    try:
                        content = skill_file.read_text(encoding='utf-8')
                        if content.startswith('---'):
                            parts = content.split('---', 2)
                            if len(parts) >= 2:
                                import yaml
                                fm = yaml.safe_load(parts[1])
                                name = fm.get('name', skill_file.stem)
                                skills.append(f"• **{name}**")
                    except (IOError, OSError, yaml.YAMLError):
                        skills.append(f"• {skill_file.stem}")
            
            embed = discord.Embed(
                title="🎯 Compétences LUMENA",
                description="\n".join(skills) if skills else "Aucun skill",
                color=discord.Color.gold()
            )
            await interaction.response.send_message(embed=embed)
        
        @self.tree.command(name="status", description="État du système LUMENA")
        async def status_cmd(interaction: discord.Interaction):
            await self._lazy_load_lumena()
            
            try:
                from ..tools.tool_system import get_tool_system
                from ..agents.sub_agent import get_orchestrator
                
                ts = get_tool_system()
                orch = get_orchestrator()
                status = orch.get_status()
                
                embed = discord.Embed(
                    title="📊 Status LUMENA",
                    color=discord.Color.green()
                )
                embed.add_field(name="🛠️ Outils", value=str(ts.tool_count), inline=True)
                embed.add_field(name="🤖 Agents", value=str(status['total_agents']), inline=True)
                embed.add_field(name="📋 Tâches", value=str(status['pending_tasks']), inline=True)
                
                await interaction.response.send_message(embed=embed)
            except Exception as e:
                await interaction.response.send_message(f"❌ Erreur: {e}")
        
        @self.tree.command(name="mood", description="Humeur de LUMENA")
        async def mood_cmd(interaction: discord.Interaction):
            await self._lazy_load_lumena()
            
            try:
                from ..emotion import EmotionManager
                em = EmotionManager()
                mood = em.get_mood()
                
                embed = discord.Embed(
                    title="💭 Humeur de LUMENA",
                    description=f"Je suis {mood.value} 😊",
                    color=discord.Color.purple()
                )
                await interaction.response.send_message(embed=embed)
            except Exception:
                await interaction.response.send_message("😊 Je vais bien !")  # fallback si embed échoue
        
        # Synchroniser les commandes
        try:
            synced = await self.tree.sync()
            logger.info(f"🎮 {len(synced)} commandes synchronisées")
        except Exception as e:
            logger.error(f"❌ Erreur sync commandes: {e}")
    
    async def on_ready(self):
        """Appelé quand le bot est prêt."""
        logger.info(f"🎮 Discord bot connecté: {self.user}")
        
        # Phase 4.10: Synchroniser les slash commands
        try:
            synced = await self.tree.sync()
            logger.info(f"🔄 {len(synced)} slash commands synchronisées")
        except Exception as e:
            logger.error(f"❌ Erreur sync slash commands: {e}")
        
        # Définir le statut
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="vos messages | /lumena"
            )
        )
    
    async def on_message(self, message: discord.Message):
        """Traite les messages (mention du bot)."""
        if message.author == self.user:
            return
        
        # Répondre si mentionné
        if self.user in message.mentions:
            content = message.content.replace(f"<@{self.user.id}>", "").strip()
            
            if content:
                async with message.channel.typing():
                    await self._lazy_load_lumena()
                    
                    try:
                        if self.lumena_core:
                            response = await self.lumena_core.chat(content)
                        else:
                            response = "❌ LUMENA n'est pas initialisée."
                        
                        if len(response) > 2000:
                            response = response[:1997] + "..."
                        
                        await message.reply(response)
                    except Exception as e:
                        await message.reply(f"❌ Erreur: {e}")
            else:
                await message.reply("✨ Comment puis-je t'aider ? Utilise `/lumena` ou mentionne-moi avec ta question !")
        
        await self.process_commands(message)
    
    async def start_bot(self):
        """Démarre le bot Discord."""
        if not self.token:
            raise ValueError("DISCORD_BOT_TOKEN requis")
        
        await self.start(self.token)

# Instance singleton avec lock thread-safe (Phase 2.1)
import threading
_discord_channel: Optional[DiscordChannel] = None
_discord_lock = threading.Lock()


def get_discord_channel() -> DiscordChannel:
    """Retourne l'instance singleton du channel Discord (thread-safe)."""
    global _discord_channel
    
    # Double-check locking pattern
    if _discord_channel is None:
        with _discord_lock:
            if _discord_channel is None:
                _discord_channel = DiscordChannel()
    return _discord_channel


async def run_discord_bot():
    """Fonction utilitaire pour lancer le bot."""
    channel = get_discord_channel()
    await channel.start_bot()


if __name__ == "__main__":
    asyncio.run(run_discord_bot())
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
