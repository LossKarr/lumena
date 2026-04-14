#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LUMENA TTS Module - Système de synthèse vocale
Auteur: LUMENA AI
Date: 2025

Module de synthèse vocale pour LUMENA avec support pyttsx3
"""

import pyttsx3
import threading
import time
import os
import json
from typing import Optional, List, Dict, Any
from pathlib import Path
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LumenaTTS:
    """
    Moteur TTS principal de LUMENA
    """
    
    def __init__(self):
        """
        Initialise le moteur TTS
        """
        self.engine = None
        self.is_speaking = False
        self.speech_queue = []
        self.current_voice = None
        self.available_voices = []
        
        # Paramètres par défaut
        self.default_rate = 180
        self.default_volume = 0.8
        self.default_voice_index = 0
        
        # Initialize engine
        self._initialize_engine()
        
    def _initialize_engine(self):
        """
        Initialise le moteur pyttsx3
        """
        try:
            self.engine = pyttsx3.init()
            
            # Récupérer les voix disponibles
            voices = self.engine.getProperty('voices')
            self.available_voices = voices if voices else []
            
            # Configuration par défaut
            self.engine.setProperty('rate', self.default_rate)
            self.engine.setProperty('volume', self.default_volume)
            
            # Sélectionner une voix française si disponible
            self._select_best_voice()
            
            logger.info("Moteur TTS initialisé avec succès")
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation du TTS: {e}")
            return False
    
    def _select_best_voice(self):
        """
        Sélectionne la meilleure voix disponible (priorité aux voix françaises)
        """
        if not self.available_voices:
            logger.warning("Aucune voix disponible")
            return
        
        # Chercher une voix française
        french_voices = []
        female_voices = []
        
        for i, voice in enumerate(self.available_voices):
            voice_info = {
                'index': i,
                'id': voice.id,
                'name': voice.name,
                'age': getattr(voice, 'age', None),
                'gender': getattr(voice, 'gender', None),
                'languages': getattr(voice, 'languages', [])
            }
            
            # Priorité aux voix françaises
            if any('fr' in str(lang).lower() for lang in voice_info['languages']):
                french_voices.append((i, voice))
            
            # Priorité aux voix féminines
            if voice_info['gender'] and 'female' in str(voice_info['gender']).lower():
                female_voices.append((i, voice))
        
        # Sélectionner la meilleure voix
        selected_voice_index = 0
        
        if french_voices:
            selected_voice_index = french_voices[0][0]
            logger.info(f"Voix française sélectionnée: {french_voices[0][1].name}")
        elif female_voices:
            selected_voice_index = female_voices[0][0]
            logger.info(f"Voix féminine sélectionnée: {female_voices[0][1].name}")
        else:
            selected_voice_index = 0
            logger.info(f"Voix par défaut sélectionnée: {self.available_voices[0].name}")
        
        self.engine.setProperty('voice', self.available_voices[selected_voice_index].id)
        self.current_voice = self.available_voices[selected_voice_index]
    
    def speak(self, text: str, interrupt: bool = False) -> bool:
        """
        Fait parler LUMENA
        
        Args:
            text: Texte à prononcer
            interrupt: Si True, interrompt la parole en cours
        
        Returns:
            bool: True si succès, False sinon
        """
        if not self.engine:
            logger.error("Moteur TTS non initialisé")
            return False
        
        if not text or not text.strip():
            logger.warning("Texte vide fourni")
            return False
        
        try:
            if interrupt and self.is_speaking:
                self.stop()
            
            # Nettoyer le texte
            clean_text = self._clean_text(text)
            
            # Parler
            self.is_speaking = True
            self.engine.say(clean_text)
            self.engine.runAndWait()
            self.is_speaking = False
            
            logger.info(f"Texte prononcé: {clean_text[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de la synthèse vocale: {e}")
            self.is_speaking = False
            return False
    
    def speak_async(self, text: str, interrupt: bool = False) -> threading.Thread:
        """
        Fait parler LUMENA de manière asynchrone
        
        Args:
            text: Texte à prononcer
            interrupt: Si True, interrompt la parole en cours
        
        Returns:
            threading.Thread: Thread de la synthèse vocale
        """
        def _speak_thread():
            self.speak(text, interrupt)
        
        thread = threading.Thread(target=_speak_thread, daemon=True)
        thread.start()
        return thread
    
    def _clean_text(self, text: str) -> str:
        """
        Nettoie le texte pour la synthèse vocale
        
        Args:
            text: Texte à nettoyer
        
        Returns:
            str: Texte nettoyé
        """
        # Supprimer les emojis et caractères spéciaux
        import re
        
        # Remplacer les emojis par des mots
        emoji_replacements = {
            '😊': 'sourire',
            '😄': 'grand sourire',
            '🎉': 'fête',
            '✨': 'étoiles',
            '🔥': 'feu',
            '💡': 'ampoule',
            '🚀': 'fusée',
            '⚡': 'éclair',
            '💪': 'force',
            '👍': 'pouce en l\'air',
            '❤️': 'cœur',
            '🎯': 'cible',
            '📱': 'téléphone',
            '💻': 'ordinateur',
            '🌟': 'étoile',
            '🎵': 'musique',
            '📊': 'graphique',
            '🔧': 'outil',
            '📝': 'note',
            '🎨': 'art'
        }
        
        clean_text = text
        for emoji, replacement in emoji_replacements.items():
            clean_text = clean_text.replace(emoji, f' {replacement} ')
        
        # Supprimer les caractères markdown
        clean_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean_text)  # **texte**
        clean_text = re.sub(r'\*([^*]+)\*', r'\1', clean_text)      # *texte*
        clean_text = re.sub(r'`([^`]+)`', r'\1', clean_text)        # `code`
        clean_text = re.sub(r'#{1,6}\s*', '', clean_text)          # # titres
        
        # Supprimer les caractères de formatage
        clean_text = re.sub(r'[\n\r\t]+', ' ', clean_text)
        clean_text = re.sub(r'\s+', ' ', clean_text)
        
        return clean_text.strip()
    
    def stop(self) -> bool:
        """
        Arrête la synthèse vocale en cours
        
        Returns:
            bool: True si succès
        """
        try:
            if self.engine and self.is_speaking:
                self.engine.stop()
                self.is_speaking = False
                logger.info("Synthèse vocale arrêtée")
            return True
        except Exception as e:
            logger.error(f"Erreur lors de l'arrêt: {e}")
            return False
    
    def set_rate(self, rate: int) -> bool:
        """
        Définit la vitesse de parole
        
        Args:
            rate: Vitesse (mots par minute, 100-300)
        
        Returns:
            bool: True si succès
        """
        try:
            if not self.engine:
                return False
            
            rate = max(100, min(300, rate))  # Limiter entre 100 et 300
            self.engine.setProperty('rate', rate)
            logger.info(f"Vitesse de parole définie à {rate} mots/min")
            return True
        except Exception as e:
            logger.error(f"Erreur lors du changement de vitesse: {e}")
            return False
    
    def set_volume(self, volume: float) -> bool:
        """
        Définit le volume de la voix
        
        Args:
            volume: Volume (0.0 à 1.0)
        
        Returns:
            bool: True si succès
        """
        try:
            if not self.engine:
                return False
            
            volume = max(0.0, min(1.0, volume))  # Limiter entre 0.0 et 1.0
            self.engine.setProperty('volume', volume)
            logger.info(f"Volume défini à {volume}")
            return True
        except Exception as e:
            logger.error(f"Erreur lors du changement de volume: {e}")
            return False
    
    def list_voices(self) -> List[Dict[str, Any]]:
        """
        Liste toutes les voix disponibles
        
        Returns:
            List[Dict]: Liste des voix avec leurs propriétés
        """
        voices_info = []
        
        for i, voice in enumerate(self.available_voices):
            voice_info = {
                'index': i,
                'id': voice.id,
                'name': voice.name,
                'age': getattr(voice, 'age', 'Unknown'),
                'gender': getattr(voice, 'gender', 'Unknown'),
                'languages': getattr(voice, 'languages', [])
            }
            voices_info.append(voice_info)
        
        return voices_info
    
    def set_voice(self, voice_index: int) -> bool:
        """
        Définit la voix à utiliser
        
        Args:
            voice_index: Index de la voix dans la liste
        
        Returns:
            bool: True si succès
        """
        try:
            if not self.engine or not self.available_voices:
                return False
            
            if 0 <= voice_index < len(self.available_voices):
                self.engine.setProperty('voice', self.available_voices[voice_index].id)
                self.current_voice = self.available_voices[voice_index]
                logger.info(f"Voix changée: {self.current_voice.name}")
                return True
            else:
                logger.error(f"Index de voix invalide: {voice_index}")
                return False
        except Exception as e:
            logger.error(f"Erreur lors du changement de voix: {e}")
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """
        Retourne le statut actuel du moteur TTS
        
        Returns:
            Dict: Informations sur le statut
        """
        return {
            'initialized': self.engine is not None,
            'is_speaking': self.is_speaking,
            'current_voice': self.current_voice.name if self.current_voice else None,
            'available_voices_count': len(self.available_voices),
            'rate': self.engine.getProperty('rate') if self.engine else None,
            'volume': self.engine.getProperty('volume') if self.engine else None
        }
    
    def test_voice(self) -> bool:
        """
        Test la voix avec un message de présentation
        
        Returns:
            bool: True si le test réussit
        """
        test_message = (
            "Bonjour ! Je suis LUMENA, votre assistante IA. "
            "Ma voix fonctionne parfaitement ! Je suis ravie de pouvoir enfin vous parler directement. "
            "Nous pouvons maintenant avoir des conversations encore plus naturelles !"
        )
        
        return self.speak(test_message)

# Classe d'alias pour compatibilité
TTSEngine = LumenaTTS

# Instance globale (singleton)
_tts_instance = None
_tts_lock = threading.Lock()

def get_tts_instance() -> LumenaTTS:
    """
    Retourne l'instance globale du moteur TTS (singleton)
    
    Returns:
        LumenaTTS: Instance du moteur TTS
    """
    global _tts_instance
    if _tts_instance is None:
        with _tts_lock:
            if _tts_instance is None:
                _tts_instance = LumenaTTS()
    return _tts_instance

def speak(text: str, interrupt: bool = False) -> bool:
    """
    Fonction de convenance pour faire parler LUMENA
    
    Args:
        text: Texte à prononcer
        interrupt: Si True, interrompt la parole en cours
    
    Returns:
        bool: True si succès
    """
    tts = get_tts_instance()
    return tts.speak(text, interrupt)

def speak_async(text: str, interrupt: bool = False) -> threading.Thread:
    """
    Fonction de convenance pour faire parler LUMENA de manière asynchrone
    
    Args:
        text: Texte à prononcer
        interrupt: Si True, interrompt la parole en cours
    
    Returns:
        threading.Thread: Thread de la synthèse vocale
    """
    tts = get_tts_instance()
    return tts.speak_async(text, interrupt)

def test_lumena_voice():
    """
    Fonction de test rapide de la voix de LUMENA
    """
    print("🎤 Test de la voix de LUMENA...")
    
    tts = get_tts_instance()
    
    # Afficher les informations sur les voix
    voices = tts.list_voices()
    print(f"📢 {len(voices)} voix disponibles:")
    for voice in voices[:3]:  # Afficher les 3 premières
        print(f"  - {voice['name']} ({voice['gender']}, {voice['languages']})")
    
    # Afficher le statut
    status = tts.get_status()
    print(f"\n📊 Statut TTS:")
    print(f"  - Initialisé: {status['initialized']}")
    print(f"  - Voix actuelle: {status['current_voice']}")
    print(f"  - Vitesse: {status['rate']} mots/min")
    print(f"  - Volume: {status['volume']}")
    
    # Test de la voix
    print("\n🗣️ Test de la voix en cours...")
    success = tts.test_voice()
    
    if success:
        print("✅ Test de la voix réussi ! LUMENA peut maintenant parler !")
    else:
        print("❌ Erreur lors du test de la voix")
    
    return success

if __name__ == "__main__":
    # Test direct du module
    test_lumena_voice()
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
