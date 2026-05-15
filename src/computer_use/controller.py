"""
🌟 LUMENA - Module Computer Use

Permet à LUMENA de contrôler l'ordinateur :
- Capturer l'écran
- Cliquer et taper
- Contrôler les fenêtres
- Automatiser des applications
"""

import asyncio
import math
import random
import re
import time
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass
from pathlib import Path
from loguru import logger

# Imports optionnels
try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
    pyautogui.FAILSAFE = True  # Coin supérieur gauche pour arrêter
    pyautogui.PAUSE = 0.1  # Pause entre actions
except ImportError:
    PYAUTOGUI_AVAILABLE = False

try:
    from PIL import Image
    import mss
    SCREENSHOT_AVAILABLE = True
except ImportError:
    SCREENSHOT_AVAILABLE = False

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

try:
    import pywinauto
    from pywinauto import Application as WinApplication
    from pywinauto import Desktop as WinDesktop
    PYWINAUTO_AVAILABLE = True
except ImportError:
    PYWINAUTO_AVAILABLE = False


class HumanBehavior:
    """
    🧠 Simulation de comportements humains naturels.

    Fournit des variations aléatoires réalistes pour rendre le contrôle PC
    moins robotique : imprécision de clic, trajectoire courbe, timing variable.
    """

    @staticmethod
    def random_delay(min_s: float = 0.05, max_s: float = 0.15) -> float:
        """Délai aléatoire entre deux bornes (secondes)."""
        return random.uniform(min_s, max_s)

    @staticmethod
    def jitter(x: int, y: int, pixels: int = 4) -> Tuple[int, int]:
        """
        Légère déviation aléatoire des coordonnées.
        Les humains ne cliquent jamais exactement au centre d'un élément.
        """
        return (
            x + random.randint(-pixels, pixels),
            y + random.randint(-pixels, pixels),
        )

    @staticmethod
    def move_duration(distance_px: float) -> float:
        """
        Durée réaliste d'un mouvement souris selon la distance (approximation loi de Fitts).
        Les humains bougent plus vite sur de longues distances mais pas linéairement.
        """
        if distance_px < 20:
            return random.uniform(0.06, 0.14)
        elif distance_px < 100:
            return random.uniform(0.13, 0.27)
        elif distance_px < 300:
            return random.uniform(0.22, 0.42)
        elif distance_px < 700:
            return random.uniform(0.32, 0.58)
        else:
            return random.uniform(0.48, 0.82)

    @staticmethod
    def typing_interval(base: float = 0.05) -> float:
        """
        Intervalle de frappe variable — les humains n'ont pas un rythme parfaitement régulier.

        Args:
            base: Intervalle de référence en secondes.
        """
        # Variation entre 40% et 180% de la base
        interval = base * random.uniform(0.4, 1.8)
        interval = max(0.025, interval)  # Minimum absolu : 25 ms
        # 4 % de chance d'une courte hésitation (cherche la touche suivante)
        if random.random() < 0.04:
            interval += random.uniform(0.12, 0.35)
        return interval


@dataclass
class ScreenRegion:
    """Région de l'écran."""
    x: int
    y: int
    width: int
    height: int
    
    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


@dataclass
class ClickAction:
    """Action de clic structurée — utilisée pour le logging, l'audit trail et l'Action Recording."""
    x: int
    y: int
    button: str = "left"  # left, right, middle
    clicks: int = 1
    timestamp: float = 0.0  # rempli automatiquement

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @classmethod
    def from_click(cls, x: Optional[int], y: Optional[int], button: str = "left", clicks: int = 1) -> "ClickAction":
        """Factory depuis les paramètres d'un clic handler."""
        return cls(x=x or 0, y=y or 0, button=button, clicks=clicks)

    def __str__(self) -> str:
        return f"Click({self.button} x{self.clicks} @ ({self.x},{self.y}))"


@dataclass
class TypeAction:
    """Action de frappe structurée — utilisée pour le logging, l'audit trail et l'Action Recording."""
    text: str
    interval: float = 0.05
    timestamp: float = 0.0  # rempli automatiquement

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @classmethod
    def from_type(cls, text: str, interval: float = 0.05) -> "TypeAction":
        """Factory depuis les paramètres d'une frappe handler."""
        return cls(text=text, interval=interval)

    def __str__(self) -> str:
        preview = self.text[:20] + "..." if len(self.text) > 20 else self.text
        return f"Type({preview!r})"


class ScreenCapture:
    """
    📸 Capture d'écran
    
    Permet de capturer l'écran ou des régions spécifiques.
    Supporte les configurations multi-écrans.
    """
    
    def __init__(self):
        if not SCREENSHOT_AVAILABLE:
            logger.warning("mss ou PIL non disponible pour les captures d'écran")
        self.sct = mss.mss() if SCREENSHOT_AVAILABLE else None
        
        # Détecter l'écran principal (par défaut Windows utilise l'écran avec la barre des tâches)
        self._primary_monitor_index = self._detect_primary_monitor()
        self._monitor_info = self._get_monitors_info()
        
        if self._monitor_info:
            primary = self._monitor_info.get(self._primary_monitor_index, {})
            logger.info(f"📺 {len(self._monitor_info) - 1} écrans détectés, principal: #{self._primary_monitor_index}")
            logger.debug(f"   Écran principal: {primary.get('width', 0)}x{primary.get('height', 0)} à ({primary.get('left', 0)}, {primary.get('top', 0)})")
    
    def _detect_primary_monitor(self) -> int:
        """
        Détecte l'index de l'écran principal.
        Utilise l'API Windows si disponible, sinon utilise l'écran à (0,0).
        """
        if not self.sct:
            return 1
        
        # Essayer d'utiliser l'API Windows pour détecter le vrai moniteur principal
        try:
            import ctypes
            from ctypes import wintypes
            
            # SM_CXSCREEN et SM_CYSCREEN donnent la taille de l'écran principal
            user32 = ctypes.windll.user32
            
            # SM_CXSCREEN et SM_CYSCREEN donnent la taille de l'écran principal
            primary_width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            primary_height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
            
            # Protection multi-screen edge case (Phase 4.14)
            if len(self.sct.monitors) < 2:
                logger.warning("Un seul moniteur détecté, utilisation de l'index 1")
                return 1 if len(self.sct.monitors) > 1 else 0
            
            # Trouver le moniteur qui correspond à cette taille
            for i, mon in enumerate(self.sct.monitors):
                if i == 0:  # Skip le virtual screen combiné
                    continue
                if mon.get("width") == primary_width and mon.get("height") == primary_height:
                    logger.debug(f"Écran principal Windows détecté: #{i} ({primary_width}x{primary_height})")
                    return i
                    
        except Exception as e:
            logger.debug(f"Détection Windows échouée: {e}")
        
        # Fallback: l'écran à (0, 0)
        for i, mon in enumerate(self.sct.monitors):
            if i == 0:
                continue
            if mon.get("left", 0) == 0 and mon.get("top", 0) == 0:
                return i
        
        # Fallback final: retourne 1 si existe, sinon 0 (Phase 4.14)
        return 1 if len(self.sct.monitors) > 1 else 0
    
    def set_target_monitor(self, index: int):
        """
        Définit manuellement l'écran cible pour les captures.
        Utile si la détection automatique ne choisit pas le bon écran.
        
        Args:
            index: 1-N pour un écran spécifique, 0 pour tous les écrans combinés
        """
        if index >= 0 and index < len(self.sct.monitors):
            self._primary_monitor_index = index
            self._monitor_info = self._get_monitors_info()
            logger.info(f"📺 Écran cible changé: #{index}")
    
    def _get_monitors_info(self) -> Dict[int, Dict]:
        """Retourne les infos de tous les moniteurs."""
        if not self.sct:
            return {}
        
        info = {}
        for i, mon in enumerate(self.sct.monitors):
            info[i] = {
                "left": mon.get("left", 0),
                "top": mon.get("top", 0),
                "width": mon.get("width", 0),
                "height": mon.get("height", 0),
                "is_combined": i == 0,
                "is_primary": i == self._primary_monitor_index
            }
        return info
    
    def get_primary_monitor_index(self) -> int:
        """Retourne l'index de l'écran principal."""
        return self._primary_monitor_index
    
    def get_monitor_offset(self, monitor: int = None) -> Tuple[int, int]:
        """
        Retourne l'offset (left, top) d'un moniteur.
        Crucial pour convertir les coordonnées écran → coordonnées absolues.
        """
        if monitor is None:
            monitor = self._primary_monitor_index
        
        info = self._monitor_info.get(monitor, {})
        return info.get("left", 0), info.get("top", 0)
    
    def capture_screen(self, monitor: int = None) -> Optional[Image.Image]:
        """
        Capture l'écran.
        
        Args:
            monitor: Index du moniteur (None = principal, 0 = tous combinés, 1+ = écran spécifique)
            
        Returns:
            Image PIL ou None
        """
        if not SCREENSHOT_AVAILABLE:
            return None
        
        # Par défaut, capturer l'écran principal (pas tous les écrans !)
        if monitor is None:
            monitor = self._primary_monitor_index
        
        # S'assurer que monitor est un int
        monitor = int(monitor)
        
        try:
            # Créer une instance mss fraîche par appel pour éviter
            # l'erreur thread-local "'_thread._local' object has no attribute 'srcdc'"
            # quand capture_screen est appelé depuis un thread différent de __init__.
            with mss.mss() as sct:
                screenshot = sct.grab(sct.monitors[monitor])
                img = Image.frombytes(
                    "RGB",
                    (screenshot.width, screenshot.height),
                    screenshot.rgb
                )
            
            logger.debug(f"Screenshot capturé: {img.size} (écran #{monitor})")
            return img
            
        except Exception as e:
            logger.error(f"Erreur capture écran: {e}")
            return None
    
    def capture_region(self, region: ScreenRegion) -> Optional[Image.Image]:
        """Capture une région spécifique."""
        if not SCREENSHOT_AVAILABLE:
            return None
        
        try:
            monitor_dict = {
                "left": region.x,
                "top": region.y,
                "width": region.width,
                "height": region.height
            }
            # Instance mss fraîche pour thread-safety (même raison que capture_screen)
            with mss.mss() as sct:
                screenshot = sct.grab(monitor_dict)
                img = Image.frombytes(
                    "RGB",
                    (screenshot.width, screenshot.height),
                    screenshot.rgb
                )
            
            return img
            
        except Exception as e:
            logger.error(f"Erreur capture région: {e}")
            return None
    
    def save_screenshot(self, path: Path, monitor: int = None) -> bool:
        """Capture et sauvegarde."""
        img = self.capture_screen(monitor)
        if img:
            img.save(str(path))
            logger.info(f"Screenshot sauvegardé: {path}")
            return True
        return False

    def zoom_region(self, x1: int, y1: int, x2: int, y2: int, save_path: Optional[str] = None) -> Optional[Image.Image]:
        """
        Capture une sous-région à PLEINE RÉSOLUTION (pas de downscale).
        
        Utilisé par le self-healing click (Phase 1.4) et l'Agent CU (Phase 1.2)
        pour inspecter une zone précise sans perte de qualité.
        
        Args:
            x1, y1: Coin supérieur gauche
            x2, y2: Coin inférieur droit
            save_path: Chemin optionnel pour sauvegarder l'image
            
        Returns:
            Image PIL à pleine résolution de la zone
        """
        # Normaliser les coordonnées
        left = min(x1, x2)
        top = min(y1, y2)
        width = abs(x2 - x1)
        height = abs(y2 - y1)
        
        if width < 1 or height < 1:
            logger.warning(f"Zoom region trop petite: {width}x{height}")
            return None
        
        region = ScreenRegion(x=left, y=top, width=width, height=height)
        img = self.capture_region(region)
        
        if img and save_path:
            img.save(str(save_path))
            logger.debug(f"🔍 Zoom {width}x{height} sauvegardé: {save_path}")
        
        if img:
            logger.debug(f"🔍 Zoom capturé: {width}x{height} @ ({left},{top}) → {img.size}")
        
        return img


class MouseController:
    """
    🖱️ Contrôle de la souris
    """
    
    def __init__(self):
        if not PYAUTOGUI_AVAILABLE:
            logger.warning("pyautogui non disponible pour le contrôle souris")
        self._last_action: Optional[ClickAction] = None  # Audit trail

    def get_position(self) -> Tuple[int, int]:
        """Retourne la position actuelle de la souris."""
        if PYAUTOGUI_AVAILABLE:
            return pyautogui.position()
        return (0, 0)
    
    def move_to(self, x: int, y: int, duration: float = None):
        """
        Déplace la souris vers une position avec un mouvement naturel.

        Le mouvement suit une légère courbe (point intermédiaire dévié) et
        utilise un easing adapté à la distance, comme un vrai humain.

        Args:
            x, y: Coordonnées cible
            duration: Durée totale (None = calculée automatiquement selon la distance)
        """
        if not PYAUTOGUI_AVAILABLE:
            return

        try:
            cx, cy = pyautogui.position()
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)

            # Durée calculée automatiquement si non fournie
            if duration is None:
                duration = HumanBehavior.move_duration(dist)

            if dist > 60:
                # Trajectoire courbe : point intermédiaire dévié perpendiculairement
                # (les humains décrivent une légère arche, jamais une ligne droite parfaite)
                mx = int((cx + x) / 2 + random.randint(-25, 25))
                my = int((cy + y) / 2 + random.randint(-18, 18))
                # Phase 1 : accélération (easeIn vers le milieu)
                pyautogui.moveTo(mx, my, duration=duration * 0.42,
                                 tween=pyautogui.easeInQuad)
                # Phase 2 : décélération (easeOut vers la cible)
                pyautogui.moveTo(x, y, duration=duration * 0.58,
                                 tween=pyautogui.easeOutQuad)
            else:
                # Court déplacement : simple avec ease symétrique
                pyautogui.moveTo(x, y, duration=duration,
                                 tween=pyautogui.easeInOutQuad)

            logger.debug(f"Mouse moved to ({x}, {y}) dist={dist:.0f}px dur={duration:.2f}s")
        except Exception as e:
            logger.error(f"Erreur mouvement souris: {e}")
    
    def click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        clicks: int = 1,
        _retries: int = 3
    ):
        """
        Effectue un clic avec mouvement souris naturel, jitter et retry automatique.

        Avant chaque clic avec coordonnées, la souris se déplace vers la cible
        avec une trajectoire légèrement courbe (comme un humain). Un léger jitter
        est appliqué pour éviter de toujours cliquer exactement au même pixel.

        Args:
            x, y: Position (None = clic à la position actuelle)
            button: "left", "right", "middle"
            clicks: Nombre de clics
            _retries: Nombre maximum de tentatives (interne)
        """
        if not PYAUTOGUI_AVAILABLE:
            return

        click_x, click_y = x, y

        # Mouvement humanisé vers la cible si coordonnées fournies
        if x is not None and y is not None:
            # Légère imprécision naturelle (les humains ne visent pas le pixel exact)
            click_x, click_y = HumanBehavior.jitter(int(x), int(y), pixels=3)
            self.move_to(click_x, click_y)
            # Micro-pause entre fin du mouvement et le clic (temps de réaction humain)
            time.sleep(HumanBehavior.random_delay(0.04, 0.12))

        # Créer l'action structurée pour audit/recording
        action = ClickAction.from_click(click_x, click_y, button, clicks)

        for attempt in range(1, _retries + 1):
            try:
                pyautogui.click(click_x, click_y, clicks=clicks, button=button)
                logger.debug(f"✅ {action}")
                # Stocker la dernière action pour l'audit trail (utilisé par cu_agent_loop & cu_recorder)
                self._last_action = action
                return
            except Exception as e:
                if attempt < _retries:
                    logger.debug(f"Clic tentative {attempt} échouée ({e}), nouvel essai…")
                    time.sleep(0.3 * attempt)
                else:
                    logger.error(f"Erreur clic après {_retries} tentatives: {e}")
    
    def double_click(self, x: Optional[int] = None, y: Optional[int] = None):
        """Double-clic."""
        self.click(x, y, clicks=2)
    
    def right_click(self, x: Optional[int] = None, y: Optional[int] = None):
        """Clic droit."""
        self.click(x, y, button="right")
    
    def drag_to(
        self,
        x: int,
        y: int,
        duration: float = 0.5,
        button: str = "left"
    ):
        """Glisser-déposer vers des coordonnées absolues."""
        if not PYAUTOGUI_AVAILABLE:
            return

        try:
            # dragTo() = coordonnées absolues ; drag() = delta relatif (ne pas confondre)
            pyautogui.dragTo(x, y, duration=duration, button=button)
            logger.debug(f"Drag to ({x}, {y})")
        except Exception as e:
            logger.error(f"Erreur drag: {e}")
    
    def scroll(self, amount: int, x: Optional[int] = None, y: Optional[int] = None):
        """
        Scroll.
        
        Args:
            amount: Positif = haut, Négatif = bas
            x, y: Position optionnelle
        """
        if not PYAUTOGUI_AVAILABLE:
            return
        
        try:
            pyautogui.scroll(amount, x, y)
        except Exception as e:
            logger.error(f"Erreur scroll: {e}")


class KeyboardController:
    """
    ⌨️ Contrôle du clavier
    """
    
    def __init__(self):
        if not PYAUTOGUI_AVAILABLE:
            logger.warning("pyautogui non disponible pour le contrôle clavier")
        self._last_action: Optional[TypeAction] = None  # Audit trail

    def type_text(self, text: str, interval: float = 0.05):
        """
        Tape du texte avec un rythme naturellement variable.

        Chaque caractère est tapé avec un délai légèrement différent (±60% autour
        de ``interval``), et 4 % des frappes incluent une courte hésitation.
        Les caractères non-ASCII utilisent ``keyboard.write()`` si disponible,
        sinon ``pyautogui.write()``, pour éviter les erreurs d'encodage.

        Args:
            text: Texte à taper
            interval: Délai de base entre les caractères (secondes)
        """
        if not PYAUTOGUI_AVAILABLE:
            return

        try:
            for char in text:
                char_delay = HumanBehavior.typing_interval(base=interval)

                if ord(char) < 128 and char.isprintable():
                    # Caractère ASCII standard : typewrite() gère proprement les touches
                    try:
                        pyautogui.typewrite(char, interval=char_delay)
                        continue
                    except Exception:
                        pass  # typewrite fallback, essayer méthode suivante

                # Caractère unicode ou non géré par typewrite
                if KEYBOARD_AVAILABLE:
                    try:
                        keyboard.write(char)
                        time.sleep(char_delay)
                        continue
                    except Exception:
                        pass  # keyboard fallback, essayer méthode suivante

                # Dernier recours : pyautogui.write (peut échouer sur certains accents)
                try:
                    pyautogui.write(char)
                    time.sleep(char_delay)
                except Exception:
                    pass  # frappe dernier recours ignorée

            # Stocker l'action structurée pour audit/recording
            action = TypeAction.from_type(text, interval)
            self._last_action = action
            logger.debug(f"✅ {action}")
        except Exception as e:
            logger.error(f"Erreur frappe: {e}")
    
    def press_key(self, key: str):
        """
        Appuie sur une touche.
        
        Args:
            key: Nom de la touche (enter, tab, escape, etc.)
        """
        if not PYAUTOGUI_AVAILABLE:
            return
        
        try:
            pyautogui.press(key)
            logger.debug(f"Pressed: {key}")
        except Exception as e:
            logger.error(f"Erreur touche: {e}")
    
    def hotkey(self, *keys):
        """
        Combinaison de touches.
        
        Example:
            hotkey('ctrl', 'c')  # Copier
            hotkey('alt', 'tab')  # Changer fenêtre
        """
        if not PYAUTOGUI_AVAILABLE:
            return
        
        try:
            pyautogui.hotkey(*keys)
            logger.debug(f"Hotkey: {'+'.join(keys)}")
        except Exception as e:
            logger.error(f"Erreur hotkey: {e}")
    
    def hold_key(self, key: str, duration: float = 0.5):
        """Maintient une touche enfoncée."""
        if not PYAUTOGUI_AVAILABLE:
            return
        
        try:
            pyautogui.keyDown(key)
            time.sleep(duration)
            pyautogui.keyUp(key)
        except Exception as e:
            logger.error(f"Erreur hold: {e}")


class WindowController:
    """
    🪟 Contrôle des fenêtres
    """
    
    def __init__(self):
        if not PYAUTOGUI_AVAILABLE:
            logger.warning("pyautogui non disponible pour le contrôle fenêtres")
    
    def get_active_window(self) -> Optional[str]:
        """Retourne le titre de la fenêtre active."""
        if not PYAUTOGUI_AVAILABLE:
            return None
        
        try:
            win = pyautogui.getActiveWindow()
            return win.title if win else None
        except Exception:
            return None  # fenêtre active inconnue
    
    def get_all_windows(self) -> List[str]:
        """Liste toutes les fenêtres."""
        if not PYAUTOGUI_AVAILABLE:
            return []
        
        try:
            return [w.title for w in pyautogui.getAllWindows() if w.title]
        except Exception:
            return []  # listing fenêtres impossible
    
    def list_windows(self) -> List[str]:
        """Alias pour get_all_windows() - compatibilité avec react.py."""
        return self.get_all_windows()
    
    def focus_window(self, title: str) -> bool:
        """
        Met une fenêtre au premier plan.
        
        Args:
            title: Titre (partiel) de la fenêtre
            
        Returns:
            True si trouvée et focusée
        """
        if not PYAUTOGUI_AVAILABLE:
            return False
        
        try:
            windows = pyautogui.getWindowsWithTitle(title)
            if windows:
                windows[0].activate()
                logger.info(f"Fenêtre activée: {windows[0].title}")
                return True
        except Exception as e:
            logger.error(f"Erreur focus fenêtre: {e}")
        
        return False
    
    def minimize_window(self, title: Optional[str] = None):
        """Minimise une fenêtre (ou la fenêtre active)."""
        if not PYAUTOGUI_AVAILABLE:
            return
        
        try:
            if title:
                windows = pyautogui.getWindowsWithTitle(title)
                if windows:
                    windows[0].minimize()
            else:
                win = pyautogui.getActiveWindow()
                if win:
                    win.minimize()
        except Exception as e:
            logger.error(f"Erreur minimize: {e}")
    
    def maximize_window(self, title: Optional[str] = None):
        """Maximise une fenêtre."""
        if not PYAUTOGUI_AVAILABLE:
            return
        
        try:
            if title:
                windows = pyautogui.getWindowsWithTitle(title)
                if windows:
                    windows[0].maximize()
            else:
                win = pyautogui.getActiveWindow()
                if win:
                    win.maximize()
        except Exception as e:
            logger.error(f"Erreur maximize: {e}")


class UIAutomationController:
    """
    🪟 Contrôle par Windows UI Automation (pywinauto)

    Trouve et interagit avec les éléments Windows via l'arbre d'accessibilité,
    sans dépendre de coordonnées pixel. Beaucoup plus robuste que pyautogui
    quand les fenêtres peuvent être déplacées ou redimensionnées.

    Utilisation recommandée :
        ui = UIAutomationController()
        if ui.click_element_by_name("Enregistrer"):
            ...  # OK via UI Automation
        else:
            ...  # Fallback vers vision LLM
    """

    def __init__(self):
        if not PYWINAUTO_AVAILABLE:
            logger.warning(
                "pywinauto non disponible. Installez-le : pip install pywinauto\n"
                "Le fallback vision LLM sera utilisé à la place."
            )

    def is_available(self) -> bool:
        return PYWINAUTO_AVAILABLE

    def connect_window(self, title: str = None, exe: str = None):
        """
        Connecte à une fenêtre existante par titre ou nom d'exe.

        Args:
            title: Titre (partiel) de la fenêtre
            exe: Nom de l'exécutable (ex: "notepad.exe")

        Returns:
            WindowSpecification ou None si non trouvée
        """
        if not PYWINAUTO_AVAILABLE:
            return None
        try:
            kwargs = {"backend": "uia"}
            if title:
                kwargs["title_re"] = f".*{title}.*"
            elif exe:
                kwargs["path"] = exe
            app = WinApplication(**kwargs).connect(**{
                k: v for k, v in kwargs.items() if k != "backend"
            })
            return app
        except Exception as e:
            logger.debug(f"UIAutomation connect_window({title!r}, {exe!r}): {e}")
            return None

    def find_window(self, title: str):
        """
        Trouve une fenêtre par titre (correspondance partielle).

        Returns:
            pywinauto WindowSpecification ou None
        """
        if not PYWINAUTO_AVAILABLE:
            return None
        try:
            desktop = WinDesktop(backend="uia")
            wins = desktop.windows(title_re=f".*{re.escape(title)}.*", visible_only=True)
            return wins[0] if wins else None
        except Exception as e:
            logger.debug(f"UIAutomation find_window({title!r}): {e}")
            return None

    def click_element_by_name(self, element_name: str, window_title: str = None) -> bool:
        """
        Clique sur un élément UI par son nom d'accessibilité.
        Ne nécessite pas de coordonnées — robuste si la fenêtre est déplacée.

        Args:
            element_name: Texte ou nom accessible du contrôle (ex: "Enregistrer", "OK")
            window_title: Titre (partiel) de la fenêtre cible. Si None, cherche sur le bureau.

        Returns:
            True si le clic a réussi, False sinon.
        """
        if not PYWINAUTO_AVAILABLE:
            return False
        try:
            desktop = WinDesktop(backend="uia")

            if window_title:
                wins = desktop.windows(title_re=f".*{re.escape(window_title)}.*", visible_only=True)
                if not wins:
                    logger.debug(f"UIAutomation: fenêtre '{window_title}' non trouvée")
                    return False
                target = wins[0]
            else:
                target = desktop

            # Stratégie 1 : child_window rapide (apps Win32 natives)
            for ct in ("Button", "MenuItem", "Hyperlink", None):
                try:
                    kw = {"title": element_name}
                    if ct:
                        kw["control_type"] = ct
                    ctrl = target.child_window(**kw)
                    ctrl.click_input()
                    logger.debug(f"UIAutomation clic exact [{ct}] sur '{element_name}'")
                    return True
                except Exception:
                    pass  # essayer le contrôle suivant

            # Stratégie 2 : recherche regex dans child_window
            try:
                ctrl = target.child_window(title_re=f".*{element_name}.*")
                ctrl.click_input()
                logger.debug(f"UIAutomation clic regex sur '{element_name}'")
                return True
            except Exception:
                pass  # essayer le contrôle suivant

            # Stratégie 3 : parcours des descendants (Electron / apps web-embedded)
            if window_title:
                try:
                    win = wins[0]
                    name_lower = element_name.lower()
                    for ctrl in win.descendants():
                        try:
                            text = ctrl.window_text().strip()
                            ct = ctrl.element_info.control_type
                            if text.lower() == name_lower and ct in ("Button", "MenuItem", "Hyperlink", "ListItem"):
                                ctrl.click_input()
                                logger.debug(f"UIAutomation clic descendant exact [{ct}] sur '{element_name}'")
                                return True
                        except Exception:
                            pass  # essayer le contrôle suivant
                    # Sous-chaîne si aucun exact
                    for ctrl in win.descendants():
                        try:
                            text = ctrl.window_text().strip()
                            ct = ctrl.element_info.control_type
                            if name_lower in text.lower() and ct in ("Button", "MenuItem", "Hyperlink"):
                                ctrl.click_input()
                                logger.debug(f"UIAutomation clic descendant partiel [{ct}] '{text}' pour '{element_name}'")
                                return True
                        except Exception:
                            pass  # essayer le contrôle suivant
                except Exception:
                    pass  # descendants non accessibles

            logger.debug(f"UIAutomation: élément '{element_name}' non trouvé")
            return False

        except Exception as e:
            logger.debug(f"UIAutomation click_element_by_name: {e}")
            return False

    def type_in_field(self, field_name: str, text: str, window_title: str = None) -> bool:
        """
        Tape du texte dans un champ de saisie identifié par son nom.

        Args:
            field_name: Nom accessible du champ (ex: "Rechercher", "Nom du fichier")
            text: Texte à taper
            window_title: Titre de la fenêtre cible

        Returns:
            True si succès
        """
        if not PYWINAUTO_AVAILABLE:
            return False
        try:
            desktop = WinDesktop(backend="uia")
            target = desktop
            if window_title:
                wins = desktop.windows(title_re=f".*{re.escape(window_title)}.*", visible_only=True)
                if wins:
                    target = wins[0]

            ctrl = target.child_window(title_re=f".*{field_name}.*", control_type="Edit")
            ctrl.set_focus()
            ctrl.type_keys(text, with_spaces=True)
            logger.debug(f"UIAutomation type_in_field '{field_name}': {text[:30]!r}")
            return True
        except Exception as e:
            logger.debug(f"UIAutomation type_in_field: {e}")
            return False

    def get_window_text(self, window_title: str) -> Optional[str]:
        """
        Extrait tout le texte d'une fenêtre via l'arbre d'accessibilité.

        Returns:
            Texte concaténé de tous les contrôles, ou None
        """
        if not PYWINAUTO_AVAILABLE:
            return None
        try:
            desktop = WinDesktop(backend="uia")
            wins = desktop.windows(title_re=f".*{re.escape(window_title)}.*", visible_only=True)
            if not wins:
                return None
            win = wins[0]
            texts = [c.window_text() for c in win.descendants() if c.window_text().strip()]
            return "\n".join(texts)
        except Exception as e:
            logger.debug(f"UIAutomation get_window_text: {e}")
            return None

    def list_controls(self, window_title: str) -> List[Dict[str, Any]]:
        """
        Liste tous les contrôles cliquables d'une fenêtre.

        Returns:
            Liste de {'name': str, 'type': str, 'rect': tuple}
        """
        if not PYWINAUTO_AVAILABLE:
            return []
        try:
            import re as _re_ctrl
            desktop = WinDesktop(backend="uia")
            wins = desktop.windows(title_re=f".*{_re_ctrl.escape(window_title)}.*", visible_only=True)
            if not wins:
                return []
            win = wins[0]
            controls = []
            for ctrl in win.descendants():
                try:
                    name = ctrl.window_text().strip()
                    ctrl_type = ctrl.element_info.control_type
                    rect = ctrl.rectangle()
                    if name or ctrl_type in ("Button", "Edit", "MenuItem"):
                        controls.append({
                            "name": name,
                            "type": ctrl_type,
                            "rect": (rect.left, rect.top, rect.right, rect.bottom)
                        })
                except Exception:
                    pass  # contrôle inaccessible, ignorer
            return controls
        except Exception as e:
            logger.debug(f"UIAutomation list_controls: {e}")
            return []


class ComputerUse:
    """
    🖥️ Interface principale Computer Use

    Combine tous les contrôles pour permettre à LUMENA
    de contrôler l'ordinateur de manière autonome.
    """
    
    def __init__(self):
        self.screen = ScreenCapture()
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.window = WindowController()
        self.ui = UIAutomationController()  # Windows UI Automation (sans coords)

        logger.info("🖥️ Module Computer Use initialisé")
        logger.info(f"  - Screenshots: {'✅' if SCREENSHOT_AVAILABLE else '❌'}")
        logger.info(f"  - Mouse/Keyboard: {'✅' if PYAUTOGUI_AVAILABLE else '❌'}")
        logger.info(f"  - UI Automation: {'✅' if PYWINAUTO_AVAILABLE else '❌ (pip install pywinauto)'}")
    
    # =====================
    # Actions de haut niveau
    # =====================
    
    async def open_application(self, name: str) -> bool:
        """
        Ouvre une application via le menu Démarrer.

        Les délais sont légèrement variables (comme un humain qui attend que
        l'interface réponde) tout en restant courts pour ne pas ralentir Lumena.

        Args:
            name: Nom de l'application

        Returns:
            True si succès
        """
        # Ouvrir le menu Démarrer
        self.keyboard.press_key("win")
        await asyncio.sleep(random.uniform(0.35, 0.60))  # Attente d'ouverture du menu

        # Taper le nom (frappe naturelle)
        self.keyboard.type_text(name)
        await asyncio.sleep(random.uniform(0.45, 0.80))  # Attente des résultats de recherche

        # Confirmer
        self.keyboard.press_key("enter")
        await asyncio.sleep(random.uniform(0.80, 1.40))  # Attente du lancement

        # Tenter de mettre la fenêtre au premier plan via pywinauto
        if PYWINAUTO_AVAILABLE:
            try:
                import re as _re
                _name_pattern = _re.escape(name.split()[0]) if name else ""
                _desktop = WinDesktop(backend="uia")
                _wins = [
                    w for w in _desktop.windows()
                    if _name_pattern and _re.search(_name_pattern, w.window_text() or "", _re.IGNORECASE)
                ]
                if _wins:
                    _wins[0].set_focus()
            except Exception:
                # Fallback : Alt+Tab pour amener la dernière fenêtre ouverte au premier plan
                self.keyboard.hotkey("alt", "tab")
                await asyncio.sleep(0.3)
        else:
            self.keyboard.hotkey("alt", "tab")
            await asyncio.sleep(0.3)

        logger.info(f"Application ouverte: {name}")
        return True
    
    async def open_url(self, url: str) -> bool:
        """
        Ouvre une URL dans le navigateur par défaut.
        """
        import webbrowser
        webbrowser.open(url)
        await asyncio.sleep(1)
        logger.info(f"URL ouverte: {url}")
        return True
    
    async def copy_text(self) -> bool:
        """Copie le texte sélectionné."""
        self.keyboard.hotkey("ctrl", "c")
        await asyncio.sleep(0.2)
        return True
    
    async def paste_text(self) -> bool:
        """Colle le texte du presse-papiers."""
        self.keyboard.hotkey("ctrl", "v")
        await asyncio.sleep(0.2)
        return True
    
    async def select_all(self) -> bool:
        """Sélectionne tout."""
        self.keyboard.hotkey("ctrl", "a")
        await asyncio.sleep(0.2)
        return True
    
    async def save(self) -> bool:
        """Sauvegarde (Ctrl+S)."""
        self.keyboard.hotkey("ctrl", "s")
        await asyncio.sleep(0.5)
        return True
    
    async def undo(self) -> bool:
        """Annuler (Ctrl+Z)."""
        self.keyboard.hotkey("ctrl", "z")
        await asyncio.sleep(0.2)
        return True
    
    async def switch_window(self) -> bool:
        """Change de fenêtre (Alt+Tab)."""
        self.keyboard.hotkey("alt", "tab")
        await asyncio.sleep(0.5)
        return True
    
    async def close_window(self) -> bool:
        """Ferme la fenêtre active (Alt+F4)."""
        self.keyboard.hotkey("alt", "F4")
        await asyncio.sleep(0.5)
        return True
    
    async def take_screenshot(self, path: Optional[Path] = None) -> Optional[Path]:
        """
        Prend une capture d'écran.
        
        Args:
            path: Chemin de sauvegarde (auto-généré si None)
            
        Returns:
            Chemin du fichier ou None
        """
        if path is None:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = Path(f"screenshot_{timestamp}.png")
        
        if self.screen.save_screenshot(path):
            return path
        return None
    
    def get_status(self) -> Dict[str, Any]:
        """Retourne le statut du module."""
        return {
            "screenshot_available": SCREENSHOT_AVAILABLE,
            "pyautogui_available": PYAUTOGUI_AVAILABLE,
            "keyboard_available": KEYBOARD_AVAILABLE,
            "pywinauto_available": PYWINAUTO_AVAILABLE,
            "active_window": self.window.get_active_window(),
            "mouse_position": self.mouse.get_position(),
        }

# Instance singleton avec lock thread-safe (Phase 2.1)
import threading
_computer_use: Optional[ComputerUse] = None
_computer_use_lock = threading.Lock()


def get_computer_use() -> ComputerUse:
    """Obtient l'instance singleton de Computer Use (thread-safe)."""
    global _computer_use
    
    # Double-check locking pattern
    if _computer_use is None:
        with _computer_use_lock:
            if _computer_use is None:
                _computer_use = ComputerUse()
    return _computer_use


# Compat legacy tests
ComputerController = ComputerUse


def get_controller() -> ComputerController:
    """Alias legacy pour get_computer_use()."""
    return get_computer_use()
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
