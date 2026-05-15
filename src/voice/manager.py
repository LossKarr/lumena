
import asyncio
import threading
from typing import Optional
from loguru import logger
from .assistant_loop import VoiceAssistant
from ..core import LumenaCore

class VoiceManager:
    _instance: Optional['VoiceManager'] = None
    _lock = threading.Lock()
    
    def __init__(self):
        self.assistant: Optional[VoiceAssistant] = None
        self.task: Optional[asyncio.Task] = None
        self.running = False
        
    @classmethod
    def get_instance(cls) -> 'VoiceManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = VoiceManager()
        return cls._instance
        
    async def start(self, core: LumenaCore):
        if self.running:
            return True
        
        try:
            if not self.assistant:
                self.assistant = VoiceAssistant(core)
            
            self.running = True
            self.task = asyncio.create_task(self.assistant.start())
            logger.info("🎙️ Voice Manager started background listening task")
            return True
        except Exception as e:
            logger.error(f"Failed to start voice assistant: {e}")
            self.running = False
            return False
            
    async def stop(self):
        if not self.running:
            return
            
        if self.assistant:
            self.assistant.stop()
            
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
                
        self.running = False
        logger.info("🎙️ Voice Manager stopped background task")
        
    def get_status(self):
        return {
            "running": self.running,
            "wake_word": "Lumena"
        }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
