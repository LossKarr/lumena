"""
🌟 LUMENA - Interface CLI

Interface en ligne de commande pour interagir avec LUMENA.
"""

import asyncio
import sys
import os
from typing import Optional
from pathlib import Path

from loguru import logger

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich.text import Text
    from rich.live import Live
    from rich.spinner import Spinner
    from rich.table import Table
    from rich.prompt import Prompt
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("⚠️ 'rich' non installé. Install avec: pip install rich")

from .core import LumenaCore, get_lumena
from .personality import Mood

# Import système multi-provider
try:
    from .llm.providers import (
        AVAILABLE_MODELS, get_model_config, check_api_key,
        ProviderType, get_free_models, get_local_models
    )
    MULTI_PROVIDER_AVAILABLE = True
except ImportError:
    MULTI_PROVIDER_AVAILABLE = False

# Helper défensif pour accès dictionary (Phase 4.12)
def _safe_stat(stats: dict, key: str, default: float = 0.0) -> float:
    """Accès défensif aux statistiques avec conversion float sûre."""
    try:
        value = stats.get(key, default) if stats else default
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def select_model_menu(console: Optional[Console] = None) -> str:
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
            "[bold cyan]🧠 SÉLECTION DU MODÈLE LLM[/bold cyan]\n"
            "[dim]Choisissez le cerveau de Lumena pour cette session[/dim]",
            border_style="cyan"
        ))
        
        # Créer le tableau
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=3)
        table.add_column("Modèle", style="cyan")
        table.add_column("Provider", style="green")
        table.add_column("Contexte", style="yellow")
        table.add_column("Coût", style="red")
        table.add_column("Status", style="blue")
        
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
            "[bold green]Votre choix[/bold green]",
            default="1",
            console=console
        )
        
        selected = model_map.get(choice, "qwen3-8b")
        config = get_model_config(selected)
        
        # Vérifier si le modèle cloud a sa clé
        if config and not config.is_local() and not check_api_key(config.provider):
            console.print(f"\n[yellow]⚠️ Clé API manquante pour {config.provider.value}![/yellow]")
            console.print(f"[dim]Configurez {config.provider.value.upper()}_API_KEY dans votre .env[/dim]")
            console.print("[dim]Fallback vers Qwen 3 8B (local)[/dim]\n")
            return "qwen3-8b"
        
        console.print(f"\n[green]✅ Modèle sélectionné: {config.display_name if config else selected}[/green]\n")
        return selected
    
    else:
        # Version sans Rich
        print("\n🧠 SÉLECTION DU MODÈLE:")
        print("1. Qwen 3 8B (Local)")
        print("2. Gemini 2.5 Pro (Google)")
        print("3. GPT-4o (OpenAI)")
        print("4. Claude Sonnet 4 (Anthropic)")
        print("5. Kimi K2.5 (Moonshot)")
        
        choice = input("\nVotre choix [1]: ").strip() or "1"
        
        choices = {
            "1": "qwen3-8b",
            "2": "gemini-2.5-pro",
            "3": "gpt-4o",
            "4": "claude-sonnet-4.6",
            "5": "kimi-k2.5"
        }
        
        return choices.get(choice, "qwen3-8b")


class LumenaCLI:
    """
    Interface CLI pour interagir avec LUMENA.
    
    Utilise la bibliothèque 'rich' pour une interface moderne.
    """
    
    def __init__(self):
        self.lumena = get_lumena()
        self.console = Console() if RICH_AVAILABLE else None
        self.running = False
        self._voice_mode = False  # Mode voix activé
        self._agent_mode = False  # Mode agent activé
        
    async def _save_agent_result_to_memory(self, query: str, response: str) -> None:
        """
        Sauvegarde les résultats importants du mode agent en mémoire ChromaDB.
        
        Permet à Lumena de se rappeler des analyses même dans les conversations futures.
        """
        if not hasattr(self.lumena, 'memory') or not self.lumena.memory:
            return
        
        # Détecter si c'est une analyse importante à sauvegarder
        important_keywords = ["portfolio", "site", "page", "analyse", "http", "recherche"]
        is_important = any(kw in query.lower() for kw in important_keywords)
        
        if is_important and len(response) > 100:
            # Extraire l'URL si présente
            import re
            url_match = re.search(r'https?://[^\s]+', query)
            url = url_match.group(0) if url_match else ""
            
            # Créer un résumé pour la mémoire
            memory_content = f"Analyse demandée: {query[:200]}\n"
            if url:
                memory_content += f"URL: {url}\n"
            memory_content += f"Résumé: {response[:1500]}"
            
            # Sauvegarder avec haute importance
            self.lumena.memory.remember(
                content=memory_content,
                memory_type="episodic",
                importance=0.9  # Haute importance
            )
        
    def print(self, message: str, style: str = "") -> None:
        """Affiche un message avec style optionnel."""
        if self.console:
            self.console.print(message, style=style)
        else:
            print(message)
    
    def print_panel(self, content: str, title: str = "", border_style: str = "blue") -> None:
        """Affiche un panneau stylisé."""
        if self.console:
            self.console.print(Panel(content, title=title, border_style=border_style))
        else:
            print(f"\n{'='*50}")
            if title:
                print(f"  {title}")
            print(f"{'='*50}")
            print(content)
            print(f"{'='*50}\n")
    
    def print_structured_response(self, response: str) -> None:
        """
        Affiche une réponse de Lumena avec séparation visuelle.
        - Message conversationnel en haut
        - Contenu additionnel (recherche, résumé, code) dans un cadre distinct
        """
        import re
        
        # Patterns pour détecter le contenu spécial (plus flexibles)
        special_patterns = [
            # Recherche web (commence par 🔍)
            (r'🔍 \*\*Recherche.*', '🔍 Recherche Web', 'bright_blue'),
            # Résumé (commence par 📝)
            (r'📝 \*\*Résumé.*', '📝 Résumé', 'bright_green'),
            # Action (commence par ✅)
            (r'✅ J\'ai ouvert.*', '✅ Action', 'bright_cyan'),
            # Code block
            (r'```[\s\S]+?```', '💻 Code', 'bright_yellow'),
        ]
        
        # Chercher du contenu spécial
        has_special = False
        conversational_part = ""
        special_content = response
        special_title = ""
        special_color = "blue"
        
        for pattern, title, color in special_patterns:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                has_special = True
                special_content = match.group(0)
                special_title = title
                special_color = color
                # Ce qui est AVANT le contenu spécial est conversationnel
                conversational_part = response[:match.start()].strip()
                break
        
        if self.console:
            # Afficher la partie conversationnelle s'il y en a
            if conversational_part:
                self.console.print(f"[bold cyan]🌟 Lumena:[/bold cyan] {conversational_part}")
                self.console.print()
            
            # Afficher le contenu spécial dans un panneau
            if has_special:
                self.console.print(Panel(
                    special_content,
                    title=f"[bold white]{special_title}[/bold white]",
                    border_style=special_color,
                    padding=(1, 2),
                    expand=False
                ))
            else:
                # Pas de contenu spécial, affichage normal
                self.console.print(f"[bold cyan]🌟 Lumena:[/bold cyan] {response}")
            
            self.console.print()  # Ligne vide
        else:
            # Fallback sans rich
            if has_special:
                if conversational_part:
                    print(f"\n🌟 Lumena: {conversational_part}")
                print(f"\n{'═'*70}")
                print(f"  {special_title}")
                print(f"{'═'*70}")
                print(special_content)
                print(f"{'═'*70}\n")
            else:
                print(f"\n🌟 Lumena: {response}\n")
    
    def print_welcome(self) -> None:
        """Affiche le message de bienvenue."""
        welcome_art = """
    ╦  ╦ ╦╔╦╗╔═╗╔╗╔╔═╗
    ║  ║ ║║║║║╣ ║║║╠═╣
    ╩═╝╚═╝╩ ╩╚═╝╝╚╝╩ ╩
    
    🌟 Une IA qui vit avec toi 🌟
        """
        
        if self.console:
            self.console.print(Panel(
                welcome_art,
                title="[bold magenta]Bienvenue ![/bold magenta]",
                border_style="bright_magenta",
                padding=(1, 2)
            ))
        else:
            print(welcome_art)
        
        # Message de salutation de LUMENA
        greeting = self.lumena.greet()
        self.print(f"\n[bold cyan]Lumena:[/bold cyan] {greeting}\n" if self.console else f"\nLumena: {greeting}\n")
    
    def print_help(self) -> None:
        """Affiche l'aide des commandes."""
        help_text = """
[bold]Commandes de base:[/bold]
  [cyan]/help[/cyan]     - Affiche cette aide
  [cyan]/clear[/cyan]    - Efface l'historique de conversation
  [cyan]/mood[/cyan]     - Change l'humeur de Lumena
  [cyan]/status[/cyan]   - Affiche l'état de Lumena
  [cyan]/quit[/cyan]     - Quitte le programme

[bold]Commandes mémoire:[/bold]
  [cyan]/memory[/cyan]          - Statistiques mémoire
  [cyan]/memory search <mot>[/cyan] - Cherche dans les souvenirs
  [cyan]/recall <sujet>[/cyan] - Raccourci pour rechercher

[bold]Commandes avancées:[/bold]
  [cyan]/skills[/cyan]   - Voir les compétences de Lumena
  [cyan]/voice[/cyan]    - Fait parler Lumena (TTS)
  [cyan]/agent[/cyan]    - Mode agent avec outils (ReAct)
  [cyan]/say <texte>[/cyan] - Fait dire un texte à Lumena

[bold]Astuces:[/bold]
  - Tapez simplement votre message pour parler avec Lumena
  - Elle se souvient de TOUT, même après redémarrage ! 💾
  - Préfixez avec "!" pour activer le mode agent
  - Dites "n'oublie pas que..." pour qu'elle retienne
        """
        
        if self.console:
            self.console.print(Panel(help_text, title="Aide", border_style="green"))
        else:
            print(help_text)
    
    async def handle_command(self, command: str) -> bool:
        """
        Gère les commandes spéciales.
        
        Returns:
            True si on doit continuer, False si on doit quitter
        """
        command_lower = command.lower().strip()
        
        if command_lower in ["/quit", "/exit", "/q"]:
            self.print("\n[yellow]Lumena:[/yellow] À bientôt ! 👋✨\n")
            return False
        
        elif command_lower == "/help":
            self.print_help()
        
        elif command_lower == "/clear":
            self.lumena.clear_context()
            self.print("\n[green]✓[/green] Conversation effacée !\n")
        
        elif command_lower == "/status":
            stats = self.lumena.get_emotion_stats()
            model = self.lumena.llm.model
            
            # Construire les barres de progression pour les émotions
            def make_bar(value, max_val=100, width=10):
                filled = int((value / max_val) * width)
                return "█" * filled + "░" * (width - filled)
            
            # Utiliser _safe_stat pour accès défensif (Phase 4.12)
            status = f"""
[bold]🎭 État émotionnel de Lumena:[/bold]
  • Humeur: [cyan]{stats.get('mood', 'neutral') if stats else 'neutral'}[/cyan]
  • Énergie: {stats.get('energy', 'medium') if stats else 'medium'}

[bold]📊 Scores émotionnels:[/bold]
  Bonheur:   {make_bar(_safe_stat(stats, 'happiness', 50))} {_safe_stat(stats, 'happiness', 50):.0f}%
  Curiosité: {make_bar(_safe_stat(stats, 'curiosity', 50))} {_safe_stat(stats, 'curiosity', 50):.0f}%
  Excitation:{make_bar(_safe_stat(stats, 'excitement', 40))} {_safe_stat(stats, 'excitement', 40):.0f}%
  Fierté:    {make_bar(_safe_stat(stats, 'pride', 30))} {_safe_stat(stats, 'pride', 30):.0f}%
  Ennui:     {make_bar(_safe_stat(stats, 'boredom', 0))} {_safe_stat(stats, 'boredom', 0):.0f}%
  Fatigue:   {make_bar(_safe_stat(stats, 'tiredness', 0))} {_safe_stat(stats, 'tiredness', 0):.0f}%

[bold]📈 Statistiques:[/bold]
  • Compliments reçus: {int(_safe_stat(stats, 'compliments_received', 0))}
  • Tâches accomplies: {int(_safe_stat(stats, 'tasks_completed', 0))}
  • Modèle LLM: {model}
  • Messages: {len(self.lumena.context.messages)}
            """
            self.print_panel(status, title="Status de Lumena", border_style="magenta")
        
        elif command_lower.startswith("/mood"):
            parts = command.split()
            if len(parts) > 1:
                mood_name = parts[1].upper()
                try:
                    new_mood = Mood[mood_name]
                    comment = self.lumena.set_mood(new_mood)
                    self.print(f"\n[green]✓[/green] Humeur changée en: {new_mood.value}")
                    if comment:
                        self.print(f"[cyan]Lumena:[/cyan] {comment}\n")
                except KeyError:
                    moods = ", ".join([m.name.lower() for m in Mood])
                    self.print(f"\n[red]✗[/red] Humeur invalide. Choix: {moods}\n")
            else:
                moods = ", ".join([m.name.lower() for m in Mood])
                self.print(f"\nUsage: /mood <{moods}>\n")
        
        elif command_lower == "/voice":
            # Active le mode voix pour la prochaine réponse
            self.print("\n[cyan]Mode voix activé pour la prochaine réponse ![/cyan]")
            self.print("[dim]Tapez votre message et Lumena parlera à voix haute.[/dim]\n")
            self._voice_mode = True
        
        elif command_lower == "/agent":
            # Active le mode agent (ReAct)
            self.print("\n[cyan]Mode agent activé ![/cyan]")
            self.print("[dim]Lumena peut maintenant utiliser des outils pour vous aider.[/dim]")
            self.print("[dim]Préfixez votre message avec '!' pour utiliser le mode agent.[/dim]\n")
        
        elif command_lower.startswith("/say "):
            # Fait dire un texte à Lumena
            text = command[5:].strip()
            if text:
                self.print(f"\n[cyan]Lumena (voix):[/cyan] {text}")
                try:
                    await self.lumena.speak(text)
                except Exception as e:
                    self.print(f"[red]Erreur TTS: {e}[/red]")
                self.print()
            else:
                self.print("\n[yellow]Usage: /say <texte à dire>[/yellow]\n")
        
        elif command_lower == "/memory":
            # Affiche les statistiques mémoire
            if hasattr(self.lumena, 'memory') and self.lumena.memory:
                stats = self.lumena.memory.get_stats()
                memory_info = f"""
[bold]💾 Mémoire de Lumena:[/bold]

📊 **Statistiques:**
  • Total souvenirs: {stats.get('count', 0)}
  • Souvenirs sémantiques: {stats.get('semantic', 0)} (faits, préférences)
  • Souvenirs épisodiques: {stats.get('episodic', 0)} (conversations)
  • Souvenirs procéduraux: {stats.get('procedural', 0)} (apprentissages)

📂 **Fichiers journal:**
  • Emplacement: data/memory/journal/
  
💡 Utilisez `/memory search <mot-clé>` pour chercher dans les souvenirs.
"""
                self.print_panel(memory_info, title="💾 Mémoire", border_style="magenta")
            else:
                self.print("\n[yellow]Mémoire non disponible.[/yellow]\n")
        
        elif command_lower.startswith("/memory search "):
            # Recherche dans les souvenirs
            query = command[15:].strip()
            if query and hasattr(self.lumena, 'memory') and self.lumena.memory:
                memories = self.lumena.memory.recall(query, limit=20)
                if memories:
                    result = f"[bold]🔍 Résultats pour '{query}':[/bold]\n\n"
                    for i, mem in enumerate(memories, 1):
                        content = mem.content[:200] + "..." if len(mem.content) > 200 else mem.content
                        result += f"{i}. [{mem.memory_type}] {content}\n\n"
                    self.print_panel(result, title="Recherche mémoire", border_style="cyan")
                else:
                    self.print(f"\n[yellow]Aucun souvenir trouvé pour '{query}'.[/yellow]\n")
            else:
                self.print("\n[yellow]Usage: /memory search <mot-clé>[/yellow]\n")
        
        elif command_lower == "/skills":
            # Affiche les skills disponibles
            if hasattr(self.lumena, '_skills') and self.lumena._skills:
                skills_info = "[bold]🎯 Compétences de Lumena:[/bold]\n\n"
                for name, content in self.lumena._skills.items():
                    # Extraire la description du frontmatter
                    desc = "Pas de description"
                    if "description:" in content:
                        lines = content.split("\n")
                        for line in lines:
                            if line.startswith("description:"):
                                desc = line.replace("description:", "").strip()
                                break
                    skills_info += f"• [cyan]{name}[/cyan] - {desc}\n"
                
                skills_info += "\n[dim]Ces skills guident mes réponses automatiquement ![/dim]"
                self.print_panel(skills_info, title="🎯 Skills", border_style="magenta")
            else:
                self.print("\n[yellow]Aucun skill chargé.[/yellow]\n")
        
        elif command_lower.startswith("/recall "):
            # Raccourci pour /memory search
            query = command[8:].strip()
            if query and hasattr(self.lumena, 'memory') and self.lumena.memory:
                memories = self.lumena.memory.recall(query, limit=20)
                if memories:
                    result = f"[bold]🧠 Je me souviens de '{query}':[/bold]\n\n"
                    for i, mem in enumerate(memories, 1):
                        content = mem.content[:200] + "..." if len(mem.content) > 200 else mem.content
                        result += f"{i}. {content}\n\n"
                    self.print_panel(result, title="💭 Souvenirs", border_style="bright_blue")
                else:
                    self.print(f"\n[yellow]Je n'ai pas de souvenir de '{query}'...[/yellow]\n")
            else:
                self.print("\n[yellow]Usage: /recall <ce que tu cherches>[/yellow]\n")
        
        else:
            self.print(f"\n[red]✗[/red] Commande inconnue: {command}. Tapez /help pour l'aide.\n")
        
        return True
    
    async def chat_with_stream(self, message: str) -> None:
        """Envoie un message et affiche la réponse en streaming."""
        
        full_response = ""  # Collecter la réponse complète
        
        if self.console:
            self.console.print()  # Nouvelle ligne
            
            # Afficher le spinner pendant le thinking
            with self.console.status("[bold cyan]Lumena réfléchit...[/bold cyan]", spinner="dots"):
                # D'abord, obtenir la réponse complète pour voir si c'est du contenu spécial
                full_response = await self.lumena.chat(message)
            
            # Vérifier si c'est du contenu spécial (recherche, résumé, etc.)
            special_markers = ['🔍 **Recherche', '📝 **Résumé', '✅ J\'ai ouvert', '```']
            is_special = any(marker in full_response for marker in special_markers)
            
            if is_special:
                # Utiliser l'affichage structuré
                self.print_structured_response(full_response)
            else:
                # Affichage normal conversationnel
                self.console.print(f"[bold cyan]🌟 Lumena:[/bold cyan] {full_response}")
                self.console.print()  # Ligne vide pour la lisibilité
        else:
            print("\nLumena réfléchit...")
            full_response = await self.lumena.chat(message)
            
            # Vérifier contenu spécial
            special_markers = ['🔍 **Recherche', '📝 **Résumé', '✅ J\'ai ouvert']
            is_special = any(marker in full_response for marker in special_markers)
            
            if is_special:
                self.print_structured_response(full_response)
            else:
                print(f"\n🌟 Lumena: {full_response}\n")
        
    async def run(self) -> None:
        """Boucle principale du CLI."""
        
        self.running = True
        
        # 🧠 MENU DE SÉLECTION DU MODÈLE
        selected_model = select_model_menu(self.console)
        
        # Appliquer le modèle sélectionné
        if MULTI_PROVIDER_AVAILABLE and selected_model != "qwen3-8b":
            try:
                from .llm.multi_provider import MultiProviderLLM
                config = get_model_config(selected_model)
                if config:
                    # Créer le nouveau client LLM
                    self.lumena.llm = MultiProviderLLM(model_name=selected_model)
                    self.print(f"[cyan]🧠 Cerveau: {config.display_name}[/cyan]")
            except Exception as e:
                self.print(f"[yellow]⚠️ Erreur chargement modèle: {e}. Fallback Qwen.[/yellow]")
        
        # Initialiser LUMENA
        if self.console:
            with self.console.status("[bold]Initialisation de Lumena...[/bold]", spinner="dots"):
                initialized = await self.lumena.initialize()
        else:
            print("Initialisation de Lumena...")
            initialized = await self.lumena.initialize()
        
        if not initialized:
            self.print("\n[red]❌ Impossible d'initialiser Lumena.[/red]")
            self.print("[yellow]Vérifiez qu'Ollama est lancé avec 'ollama serve'[/yellow]\n")
            return
        
        # Afficher la bienvenue
        self.print_welcome()
        
        # Boucle principale
        while self.running:
            try:
                # Prompt utilisateur
                if self.console:
                    user_input = self.console.input("[bold green]Vous:[/bold green] ")
                else:
                    user_input = input("Vous: ")
                
                user_input = user_input.strip()
                
                if not user_input:
                    continue
                
                # Commande spéciale ?
                if user_input.startswith("/"):
                    self.running = await self.handle_command(user_input)
                    continue
                
                # Mode agent (préfixe !)
                if user_input.startswith("!"):
                    query = user_input[1:].strip()
                    if query:
                        self.print("\n[dim]Mode agent activé...[/dim]")
                        response = await self.lumena.think_and_act(query)
                        self.print(f"\n[bold cyan]Lumena:[/bold cyan] {response}\n")
                        # Sauvegarder la réponse dans le contexte pour les questions de suivi
                        self.lumena.context.add_message("user", query)
                        self.lumena.context.add_message("assistant", response)
                        # Sauvegarder en mémoire ChromaDB si c'est une analyse importante
                        await self._save_agent_result_to_memory(query, response)
                    continue
                
                # Détection automatique d'URLs → mode agent auto
                import re
                url_pattern = r'https?://[^\s]+'
                contains_url = re.search(url_pattern, user_input)
                url_keywords = ["ouvre", "analyse", "va sur", "visite", "regarde", "consulte"]
                wants_url_action = any(kw in user_input.lower() for kw in url_keywords)
                
                if contains_url and wants_url_action:
                    self.print("\n[dim]URL détectée → Mode agent activé automatiquement...[/dim]")
                    response = await self.lumena.think_and_act(user_input)
                    self.print(f"\n[bold cyan]Lumena:[/bold cyan] {response}\n")
                    # Sauvegarder dans le contexte
                    self.lumena.context.add_message("user", user_input)
                    self.lumena.context.add_message("assistant", response)
                    # Sauvegarder en mémoire ChromaDB
                    await self._save_agent_result_to_memory(user_input, response)
                    continue
                
                # Message normal -> chat avec Lumena
                await self.chat_with_stream(user_input)
                
                # Mode voix: faire parler la réponse
                if self._voice_mode:
                    self._voice_mode = False
                    if self.lumena.context.messages:
                        last_response = self.lumena.context.messages[-1].content
                        await self.lumena.speak(last_response, wait=False)
                
            except KeyboardInterrupt:
                self.print("\n\n[yellow]Lumena:[/yellow] Oh, tu pars ? À bientôt ! 👋\n")
                break
            except EOFError:
                break
        
        # Arrêt propre
        await self.lumena.shutdown()


async def main() -> None:
    """Point d'entrée principal."""
    cli = LumenaCLI()
    await cli.run()


def run() -> None:
    """Exécute le CLI (point d'entrée synchrone)."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
