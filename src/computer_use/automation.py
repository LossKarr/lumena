"""
🌟 LUMENA - Automation d'applications

Permet d'automatiser des applications spécifiques.
"""

import asyncio
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from pathlib import Path
from loguru import logger

from .controller import ComputerUse, get_computer_use


@dataclass
class AppAction:
    """Une action dans une application."""
    action: str
    params: Dict[str, Any]
    wait_after: float = 0.5


class AppAutomation:
    """
    🤖 Base pour l'automation d'applications
    
    Chaque opération crée un AppAction structuré pour l'audit trail
    et l'Action Recording (Phase 3.3).
    """
    
    def __init__(self, app_name: str):
        self.app_name = app_name
        self.computer = get_computer_use()
        self.is_open = False
        self.action_history: list = []  # Historique des AppAction pour recording/replay
    
    def _record(self, action_name: str, params: Dict[str, Any] = None, wait: float = 0.5) -> AppAction:
        """Enregistre une action structurée dans l'historique."""
        action = AppAction(action=action_name, params=params or {}, wait_after=wait)
        self.action_history.append(action)
        logger.debug(f"📋 {self.app_name}: {action.action}({action.params})")
        return action

    async def open(self) -> bool:
        """Ouvre l'application."""
        self._record("open", {"app": self.app_name}, wait=2.0)
        result = await self.computer.open_application(self.app_name)
        if result:
            self.is_open = True
            await asyncio.sleep(2)  # Attendre le chargement
        return result
    
    async def focus(self) -> bool:
        """Met l'application au premier plan."""
        self._record("focus", {"app": self.app_name}, wait=0.3)
        return self.computer.window.focus_window(self.app_name)
    
    async def close(self) -> bool:
        """Ferme l'application."""
        self._record("close", {"app": self.app_name}, wait=0.5)
        if await self.focus():
            await self.computer.close_window()
            self.is_open = False
            return True
        return False


class NotepadAutomation(AppAutomation):
    """
    📝 Automation du Bloc-notes
    """
    
    def __init__(self):
        super().__init__("notepad")
    
    async def write_text(self, text: str):
        """Écrit du texte."""
        await self.focus()
        self.computer.keyboard.type_text(text)
    
    async def save_as(self, filename: str):
        """Sauvegarde sous un nom."""
        self.computer.keyboard.hotkey("ctrl", "shift", "s")
        await asyncio.sleep(0.5)
        self.computer.keyboard.type_text(filename)
        await asyncio.sleep(0.3)
        self.computer.keyboard.press_key("enter")
        await asyncio.sleep(0.5)
    
    async def new_file(self):
        """Nouveau fichier."""
        self.computer.keyboard.hotkey("ctrl", "n")
        await asyncio.sleep(0.3)


class BrowserAutomation(AppAutomation):
    """
    🌐 Automation du navigateur
    """
    
    def __init__(self, browser: str = "chrome"):
        super().__init__(browser)
    
    async def open_url(self, url: str):
        """Ouvre une URL."""
        # Nouvelle tab ou focus barre d'adresse
        self.computer.keyboard.hotkey("ctrl", "l")
        await asyncio.sleep(0.2)
        
        # Taper l'URL
        self.computer.keyboard.type_text(url)
        self.computer.keyboard.press_key("enter")
        await asyncio.sleep(2)
    
    async def new_tab(self):
        """Nouvelle tab."""
        self.computer.keyboard.hotkey("ctrl", "t")
        await asyncio.sleep(0.3)
    
    async def close_tab(self):
        """Ferme la tab courante."""
        self.computer.keyboard.hotkey("ctrl", "w")
        await asyncio.sleep(0.3)
    
    async def search(self, query: str):
        """Effectue une recherche."""
        await self.new_tab()
        self.computer.keyboard.type_text(query)
        self.computer.keyboard.press_key("enter")
        await asyncio.sleep(2)
    
    async def scroll_down(self, amount: int = 3):
        """Scroll vers le bas."""
        self.computer.mouse.scroll(-amount)
    
    async def scroll_up(self, amount: int = 3):
        """Scroll vers le haut."""
        self.computer.mouse.scroll(amount)
    
    async def go_back(self):
        """Page précédente."""
        self.computer.keyboard.hotkey("alt", "left")
        await asyncio.sleep(1)
    
    async def go_forward(self):
        """Page suivante."""
        self.computer.keyboard.hotkey("alt", "right")
        await asyncio.sleep(1)
    
    async def refresh(self):
        """Rafraîchit la page."""
        self.computer.keyboard.press_key("f5")
        await asyncio.sleep(2)


class FileExplorerAutomation(AppAutomation):
    """
    📁 Automation de l'explorateur de fichiers
    """
    
    def __init__(self):
        super().__init__("explorer")
    
    async def go_to_path(self, path: str):
        """Navigue vers un chemin."""
        # Focus sur la barre d'adresse
        self.computer.keyboard.hotkey("ctrl", "l")
        await asyncio.sleep(0.2)
        
        # Taper le chemin
        self.computer.keyboard.type_text(path)
        self.computer.keyboard.press_key("enter")
        await asyncio.sleep(1)
    
    async def create_folder(self, name: str):
        """Crée un nouveau dossier."""
        self.computer.keyboard.hotkey("ctrl", "shift", "n")
        await asyncio.sleep(0.3)
        self.computer.keyboard.type_text(name)
        self.computer.keyboard.press_key("enter")
        await asyncio.sleep(0.5)
    
    async def search_files(self, query: str):
        """Recherche de fichiers."""
        self.computer.keyboard.hotkey("ctrl", "e")
        await asyncio.sleep(0.2)
        self.computer.keyboard.type_text(query)
        self.computer.keyboard.press_key("enter")
        await asyncio.sleep(1)


class AppAutomationRegistry:
    """
    📚 Registre des automations d'applications
    """
    
    def __init__(self):
        self.apps: Dict[str, AppAutomation] = {}
        
        # Enregistrer les apps par défaut
        self._register_defaults()
    
    def _register_defaults(self):
        """Enregistre les automations par défaut."""
        self.apps["notepad"] = NotepadAutomation()
        self.apps["chrome"] = BrowserAutomation("chrome")
        self.apps["edge"] = BrowserAutomation("msedge")
        self.apps["firefox"] = BrowserAutomation("firefox")
        self.apps["explorer"] = FileExplorerAutomation()
    
    def get(self, app_name: str) -> Optional[AppAutomation]:
        """Obtient une automation par nom."""
        return self.apps.get(app_name.lower())
    
    def register(self, name: str, automation: AppAutomation):
        """Enregistre une nouvelle automation."""
        self.apps[name.lower()] = automation
    
    def list_apps(self) -> List[str]:
        """Liste les apps disponibles."""
        return list(self.apps.keys())


# Instance singleton
_app_registry: Optional[AppAutomationRegistry] = None


def get_app_registry() -> AppAutomationRegistry:
    """Obtient le registre d'automations."""
    global _app_registry
    if _app_registry is None:
        _app_registry = AppAutomationRegistry()
    return _app_registry
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
