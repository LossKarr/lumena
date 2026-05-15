"""
🌟 LUMENA - Vision et analyse d'écran

Analyse les captures d'écran pour comprendre ce qui se passe à l'écran.
Utilise l'OCR et potentiellement un modèle de vision.
"""

import asyncio
import os
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from pathlib import Path
from loguru import logger

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# OCR optionnel
try:
    import pytesseract
    # Configurer le chemin Tesseract sur Windows si pas dans PATH
    import os
    tesseract_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expanduser(r"~\AppData\Local\Tesseract-OCR\tesseract.exe")
    ]
    for path in tesseract_paths:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            break
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


@dataclass
class TextRegion:
    """Une région de texte détectée."""
    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float


@dataclass
class UIElement:
    """Un élément d'interface détecté — utilisé par le DOM Indexer (Phase 2.2), le Self-healing (Phase 1.4) et pywinauto."""
    type: str  # button, input, text, image, link, select
    text: Optional[str]
    x: int
    y: int
    width: int
    height: int
    confidence: float = 1.0  # 0.0-1.0, confiance de la détection
    source: str = "ocr"  # ocr, accessibility, vision, dom

    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    @classmethod
    def from_text_region(cls, region: "TextRegion") -> "UIElement":
        """Convertit un TextRegion OCR en UIElement."""
        return cls(
            type="text",
            text=region.text,
            x=region.x,
            y=region.y,
            width=region.width,
            height=region.height,
            confidence=region.confidence / 100.0,  # TextRegion utilise 0-100
            source="ocr",
        )

    def __str__(self) -> str:
        return f"UIElement({self.type} \"{self.text}\" @ ({self.x},{self.y}) [{self.source}])"


@dataclass
class _ProviderHealthEntry:
    """Santé d'un provider vision — instance-level, jamais module-level."""
    failures: int = 0
    last_error: str = ""
    cooldown_until: float = 0.0
    permanent: bool = False  # True si 401/403/clé manquante


class ScreenAnalyzer:
    """
    🔍 Analyseur d'écran
    
    Extrait le texte et les éléments d'interface des captures d'écran.
    """
    
    def __init__(self):
        self.ocr_available = OCR_AVAILABLE
        
        if OCR_AVAILABLE:
            logger.info("🔍 OCR disponible (pytesseract)")
        else:
            logger.warning("pytesseract non installé. pip install pytesseract")
    
    def extract_text(self, image: Image.Image) -> str:
        """
        Extrait tout le texte d'une image.
        
        Args:
            image: Image PIL
            
        Returns:
            Texte extrait
        """
        if not OCR_AVAILABLE:
            return ""
        
        try:
            text = pytesseract.image_to_string(image, lang='fra+eng')
            return text.strip()
        except Exception as e:
            logger.error(f"Erreur OCR: {e}")
            return ""
    
    def find_text_regions(self, image: Image.Image) -> List[TextRegion]:
        """
        Trouve les régions de texte avec leurs positions.
        
        Args:
            image: Image PIL
            
        Returns:
            Liste de TextRegion
        """
        if not OCR_AVAILABLE:
            return []
        
        try:
            # Obtenir les données détaillées
            data = pytesseract.image_to_data(
                image, 
                lang='fra+eng',
                output_type=pytesseract.Output.DICT
            )
            
            regions = []
            n_boxes = len(data['text'])
            
            for i in range(n_boxes):
                text = data['text'][i].strip()
                conf = float(data['conf'][i])
                
                if text and conf > 50:  # Filtrer les confiances faibles
                    regions.append(TextRegion(
                        text=text,
                        x=data['left'][i],
                        y=data['top'][i],
                        width=data['width'][i],
                        height=data['height'][i],
                        confidence=conf
                    ))
            
            return regions
            
        except Exception as e:
            logger.error(f"Erreur find_text_regions: {e}")
            return []
    
    def find_text(self, image: Image.Image, target: str) -> Optional[TextRegion]:
        """
        Trouve une occurrence de texte spécifique.
        
        Args:
            image: Image PIL
            target: Texte à chercher
            
        Returns:
            TextRegion ou None
        """
        regions = self.find_text_regions(image)
        target_lower = target.lower()
        
        for region in regions:
            if target_lower in region.text.lower():
                return region

    def find_text_as_element(self, image: Image.Image, target: str) -> Optional[UIElement]:
        """
        Trouve un texte et le retourne comme UIElement (utilisé par self-healing & DOM indexer).
        
        Args:
            image: Image PIL
            target: Texte à chercher
            
        Returns:
            UIElement ou None  
        """
        region = self.find_text(image, target)
        if region:
            return UIElement.from_text_region(region)
        return None

    def get_all_elements(self, image: Image.Image, min_confidence: float = 50.0) -> List[UIElement]:
        """
        Extrait tous les éléments textuels comme UIElements (base pour le DOM indexer).
        
        Args:
            image: Image PIL
            min_confidence: Confiance minimum OCR (0-100)
            
        Returns:
            Liste de UIElement triés par position (haut→bas, gauche→droite)
        """
        regions = self.find_text_regions(image)
        elements = [
            UIElement.from_text_region(r)
            for r in regions
            if r.confidence >= min_confidence
        ]
        # Tri spatial : haut→bas puis gauche→droite (ordre de lecture naturel)
        elements.sort(key=lambda e: (e.y, e.x))
        return elements
        
        return None
    
    def describe_screen(self, image: Image.Image) -> Dict[str, Any]:
        """
        Génère une description de l'écran.
        
        Args:
            image: Image PIL
            
        Returns:
            Dictionnaire avec la description
        """
        text = self.extract_text(image)
        regions = self.find_text_regions(image)
        
        # Statistiques basiques
        return {
            "size": image.size,
            "text_content": text[:500] + "..." if len(text) > 500 else text,
            "text_regions_count": len(regions),
            "main_texts": [r.text for r in regions[:10]],  # Top 10
        }


class VisionModule:
    """
    👁️ Module de Vision
    
    Combine l'analyse d'écran avec la compréhension visuelle.
    Utilise Gemini ou Claude pour la vision LLM.
    """
    
    def __init__(self):
        self.analyzer = ScreenAnalyzer()
        self.default_provider = "google"  # ou "anthropic"
        
        # Constants pour le scaling (best practice Claude Computer Use 2026)
        self.MAX_IMAGE_SIZE = int(os.environ.get("LUMENA_VISION_MAX_IMAGE_SIZE", "1568"))  # pixels max pour LLM
        self.DEFAULT_JPEG_QUALITY = 85
        
        # Tracking des fichiers temporaires pour nettoyage
        self._temp_files: List[str] = []
        # Santé des providers vision — instance-level (pas de fuite entre tests)
        self._provider_health: Dict[str, _ProviderHealthEntry] = {}
        # Dernière transformation image appliquée (scale + padding).
        self._last_transform: Dict[str, Any] = {
            "scale_factor": 1.0,
            "pad_offset_x": 0,
            "pad_offset_y": 0,
            "prepared_path": None,
        }
        import atexit
        atexit.register(self._cleanup_temp_files)
        
        logger.info("👁️ Module Vision initialisé (LLM Vision supporté)")

    def _cleanup_temp_files(self):
        """Nettoie les fichiers temporaires créés par prepare_screenshot_for_llm."""
        import os
        cleaned = 0
        for f in self._temp_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
                    cleaned += 1
            except Exception:
                pass  # cleanup best-effort
        if cleaned:
            logger.debug(f"🧹 {cleaned} fichier(s) temporaire(s) vision nettoyé(s)")
        self._temp_files.clear()

    async def prepare_screenshot_for_llm(self, image_path: str) -> tuple:
        """
        Prépare un screenshot pour l'analyse LLM.
        
        Améliorations Phase 1.1 :
        - Formule Anthropic recommandée : min(1.0, 1568/long_edge, sqrt(1_150_000/pixels))
        - Curseur crosshair rouge dessiné sur le screenshot
        - Support padding noir pour résolutions < XGA
        
        Returns:
            (prepared_path, scale_factor, original_width, original_height, pad_offset_x, pad_offset_y)
        """
        if not PIL_AVAILABLE:
            return image_path, 1.0, 0, 0
        
        from PIL import Image, ImageDraw
        import tempfile
        import os
        import math
        
        img = Image.open(image_path).convert("RGB")
        orig_width, orig_height = img.size
        
        # --- Dessiner le curseur (crosshair rouge) sur le screenshot ---
        try:
            cursor_x, cursor_y = self._get_cursor_position()
            if 0 <= cursor_x < orig_width and 0 <= cursor_y < orig_height:
                draw = ImageDraw.Draw(img)
                r = 10  # rayon du crosshair (10px comme spécifié dans le plan)
                # Croix rouge avec contour noir pour visibilité sur fond clair ET foncé
                for offset, color in [(2, "black"), (0, "red")]:
                    draw.line([(cursor_x - r - offset, cursor_y), (cursor_x + r + offset, cursor_y)], fill=color, width=2)
                    draw.line([(cursor_x, cursor_y - r - offset), (cursor_x, cursor_y + r + offset)], fill=color, width=2)
                logger.debug(f"🔴 Curseur dessiné à ({cursor_x}, {cursor_y})")
        except Exception as e:
            logger.debug(f"Curseur non dessiné: {e}")
        
        # --- Formule de scaling Anthropic recommandée ---
        max_dim = max(orig_width, orig_height)
        total_pixels = orig_width * orig_height
        
        # scale_factor < 1.0 = on réduit, 1.0 = taille originale
        scale_factor = min(
            1.0,
            self.MAX_IMAGE_SIZE / max_dim,
            math.sqrt(1_150_000 / total_pixels) if total_pixels > 0 else 1.0
        )
        
        # --- Dimensions cibles ---
        XGA_WIDTH, XGA_HEIGHT = 1024, 768
        pad_offset_x, pad_offset_y = 0, 0
        
        if scale_factor >= 1.0:
            new_width, new_height = orig_width, orig_height
            img_final = img
        else:
            new_width = int(orig_width * scale_factor)
            new_height = int(orig_height * scale_factor)
            img_final = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # --- Padding noir pour résolutions < XGA ---
        if new_width < XGA_WIDTH or new_height < XGA_HEIGHT:
            pad_w = max(new_width, XGA_WIDTH)
            pad_h = max(new_height, XGA_HEIGHT)
            padded = Image.new("RGB", (pad_w, pad_h), (0, 0, 0))
            pad_offset_x = (pad_w - new_width) // 2
            pad_offset_y = (pad_h - new_height) // 2
            padded.paste(img_final, (pad_offset_x, pad_offset_y))
            img_final = padded
            logger.debug(f"⬛ Padding noir appliqué: {new_width}x{new_height} → {pad_w}x{pad_h}")
        
        # Sauvegarder dans un fichier temporaire (JPEG)
        temp_dir = tempfile.gettempdir()
        base_name = os.path.splitext(os.path.basename(image_path))[0]
        prepared_path = os.path.join(temp_dir, f"lumena_prepared_{base_name}.jpg")
        img_final.save(prepared_path, "JPEG", quality=self.DEFAULT_JPEG_QUALITY)
        
        # Tracker pour nettoyage automatique à la fermeture
        self._temp_files.append(prepared_path)
        self._last_transform = {
            "scale_factor": float(scale_factor),
            "pad_offset_x": int(pad_offset_x),
            "pad_offset_y": int(pad_offset_y),
            "prepared_path": prepared_path,
        }
        
        if scale_factor < 1.0:
            logger.debug(f"📐 Image redimensionnée: {orig_width}x{orig_height} → {new_width}x{new_height} (scale: {scale_factor:.3f})")
        
        return prepared_path, scale_factor, orig_width, orig_height, pad_offset_x, pad_offset_y

    @staticmethod
    def _get_cursor_position() -> Tuple[int, int]:
        """Récupère la position du curseur souris (0,0 si indisponible)."""
        try:
            import pyautogui
            return pyautogui.position()
        except Exception:
            return (0, 0)  # position curseur inconnue

    def get_screen_metadata(self) -> str:
        """
        Retourne les métadonnées d'écran pour le contexte LLM.
        
        Format: [Screen: 1920x1080 → scaled 1568x882 | Active: "Google Chrome" | Cursor: (543, 312)]
        """
        parts = []
        
        # Résolution d'écran + dimensions scaled
        try:
            import mss
            import math
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # Écran principal
                w, h = monitor['width'], monitor['height']
                max_dim = max(w, h)
                total_px = w * h
                sf = min(
                    1.0,
                    self.MAX_IMAGE_SIZE / max_dim,
                    math.sqrt(1_150_000 / total_px) if total_px > 0 else 1.0
                )
                sw, sh = int(w * sf), int(h * sf)
                if sf < 1.0:
                    parts.append(f"Screen: {w}x{h} → scaled {sw}x{sh}")
                else:
                    parts.append(f"Screen: {w}x{h}")
        except Exception:
            parts.append("Screen: unknown")  # résolution inaccessible
        
        # Fenêtre active
        try:
            from .controller import get_computer_use
            cu = get_computer_use()
            title = cu.window.get_active_window()
            if title:
                # Tronquer les titres longs
                if len(title) > 50:
                    title = title[:47] + "..."
                parts.append(f'Active: "{title}"')
        except Exception:
            pass  # fenêtre active non disponible
        
        # Position curseur
        try:
            cx, cy = self._get_cursor_position()
            parts.append(f"Cursor: ({cx}, {cy})")
        except Exception:
            pass  # curseur non disponible
        
        return "[" + " | ".join(parts) + "]"
    
    def scale_coordinates_to_screen(
        self,
        x: int,
        y: int,
        scale_factor: float,
        pad_offset_x: int = 0,
        pad_offset_y: int = 0,
    ) -> tuple:
        """
        Convertit les coordonnées LLM vers coordonnées écran réelles.
        CRITIQUE pour que les clics tombent au bon endroit !
        
        Le scale_factor est le ratio de réduction appliqué :
        - scale_factor < 1.0 : l'image a été réduite → diviser pour agrandir
        - scale_factor = 1.0 : pas de changement
        
        Formule :
        1) retirer d'abord l'offset de padding (si présent),
        2) reprojecter avec screen_coord = round(coord / scale_factor)
        """
        # Les coordonnées reçues du LLM sont dans l'espace de l'image préparée.
        # Si du padding noir a été ajouté, on retire cet offset avant l'upscale.
        llm_x = max(0, int(x) - int(pad_offset_x))
        llm_y = max(0, int(y) - int(pad_offset_y))

        if scale_factor <= 0 or scale_factor >= 1.0:
            return llm_x, llm_y

        screen_x = round(llm_x / scale_factor)
        screen_y = round(llm_y / scale_factor)
        return screen_x, screen_y
    
    async def _encode_image_base64(self, image_path: str) -> str:
        """Encode une image en base64."""
        import base64
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image non trouvée: {image_path}")
        
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    
    async def analyze_with_gemini(
        self,
        image_path: str,
        prompt: str,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Analyse un screenshot avec Gemini Vision."""
        import httpx
        import os
        from dotenv import load_dotenv
        load_dotenv()
        
        key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not key:
            return {"success": False, "error": "GOOGLE_API_KEY non configurée"}
        
        image_base64 = await self._encode_image_base64(image_path)
        
        suffix = Path(image_path).suffix.lower()
        mime_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        mime_type = mime_types.get(suffix, "image/png")
        
        try:
            from src.llm.providers import models_with_capability, AVAILABLE_MODELS, ProviderType
            google_models = [
                n for n in models_with_capability("vision_describe")
                if AVAILABLE_MODELS[n].provider == ProviderType.GOOGLE
            ]
            gemini_model_id = AVAILABLE_MODELS[google_models[0]].model_id if google_models else "gemini-2.5-flash"
        except Exception:
            gemini_model_id = "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model_id}:generateContent?key={key}"
        
        payload = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": image_base64}},
                    {"text": prompt}
                ]
            }],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048}
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
            
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return {"success": True, "answer": text}
        except Exception as e:
            logger.error(f"Erreur Gemini Vision: {e}")
            return {"success": False, "error": str(e)}
    
    async def analyze_with_claude(
        self,
        image_path: str,
        prompt: str,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Analyse un screenshot avec Claude Vision."""
        import httpx
        import os
        from dotenv import load_dotenv
        load_dotenv()
        
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            return {"success": False, "error": "ANTHROPIC_API_KEY non configurée"}
        
        image_base64 = await self._encode_image_base64(image_path)
        
        suffix = Path(image_path).suffix.lower()
        mime_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        media_type = mime_types.get(suffix, "image/png")
        
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        try:
            from src.llm.providers import models_with_capability, AVAILABLE_MODELS, ProviderType
            anthropic_models = [
                n for n in models_with_capability("vision_grounding")
                if AVAILABLE_MODELS[n].provider == ProviderType.ANTHROPIC
            ]
            claude_model = anthropic_models[0] if anthropic_models else "claude-sonnet-4.6"
        except Exception:
            claude_model = "claude-sonnet-4.6"

        payload = {
            "model": claude_model,
            "max_tokens": 2048,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            }]
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
                if response.status_code != 200:
                    error_body = response.text
                    logger.error(f"Claude Vision {response.status_code}: {error_body[:500]}")
                response.raise_for_status()
                data = response.json()
            
            text = data["content"][0]["text"]
            return {"success": True, "answer": text}
        except httpx.HTTPStatusError as e:
            logger.error(f"Erreur Claude Vision HTTP: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Erreur Claude Vision: {e}")
            return {"success": False, "error": str(e)}

    async def analyze_with_ollama(
        self,
        image_path: str,
        prompt: str,
        ollama_host: Optional[str] = None
    ) -> Dict[str, Any]:
        """Analyse une image avec un modèle vision Ollama local.
        
        Détection model-agnostic : essaie les modèles connus en priorité,
        puis se rabat sur TOUT modèle présent qui supporte la vision
        (détection via modelfile 'projector' / family 'clip').
        """
        import httpx
        import os

        host = ollama_host or os.getenv("OLLAMA_HOST", "http://localhost:11434")

        # Modèles vision connus (ordre de priorité)
        preferred_vision = [
            "minicpm-v", "llava-llama3", "llava", "bakllava",
            "moondream", "llava-phi3", "nanollava", "obsidian",
            "granite3.2-vision", "llama3.2-vision", "gemma3",
        ]
        available_model = None

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{host}/api/tags")
                if resp.status_code != 200:
                    return {"success": False, "error": f"Ollama /api/tags HTTP {resp.status_code}"}
                all_models = resp.json().get("models", [])
                local_names = [m["name"].split(":")[0] for m in all_models]

                # 1. Chercher dans la liste de priorité
                for vm in preferred_vision:
                    if vm in local_names:
                        available_model = vm
                        break

                # 2. Si rien trouvé, détecter tout modèle vision par son modelfile
                if not available_model:
                    for m in all_models:
                        model_name = m["name"].split(":")[0]
                        if model_name in local_names:
                            # Détection heuristique : mots-clés vision dans le nom
                            name_lower = model_name.lower()
                            vision_keywords = ["vision", "llava", "visual", "eye", "image", "see", "look"]
                            if any(kw in name_lower for kw in vision_keywords):
                                available_model = model_name
                                break
                    # 3. Dernier recours : tester si le modèle accepte les images
                    #    en interrogeant /api/show
                    if not available_model:
                        for m in all_models:
                            model_name = m["name"].split(":")[0]
                            try:
                                show_resp = await client.post(
                                    f"{host}/api/show",
                                    json={"name": model_name},
                                    timeout=3.0,
                                )
                                if show_resp.status_code == 200:
                                    show_data = show_resp.json()
                                    modelfile = show_data.get("modelfile", "")
                                    details = show_data.get("details", {})
                                    families = details.get("families", [])
                                    # projector dans le modelfile = multimodal
                                    if ("projector" in modelfile.lower()
                                            or "clip" in str(families).lower()
                                            or "vision" in str(families).lower()):
                                        available_model = model_name
                                        break
                            except Exception:
                                continue  # modèle inaccessible
        except Exception:
            return {"success": False, "error": "Ollama non disponible"}

        if not available_model:
            return {"success": False, "error": "Aucun modèle vision Ollama installé (ollama pull llava)"}

        image_base64 = await self._encode_image_base64(image_path)

        payload = {
            "model": available_model,
            "messages": [{
                "role": "user",
                "content": prompt,
                "images": [image_base64]
            }],
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 1024,
            }
        }

        try:
            # P5.2 — timeout adapté : 60s en local (Ollama seul), 120s sinon
            try:
                from .cu_router import get_execution_mode
                _ollama_timeout = 60.0 if get_execution_mode() == "local" else 120.0
            except Exception:
                _ollama_timeout = 120.0
            async with httpx.AsyncClient(timeout=_ollama_timeout) as client:
                response = await client.post(f"{host}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()

            text = data.get("message", {}).get("content", "")
            if text:
                logger.info(f"👁️ Ollama Vision ({available_model}) analyse réussie")
                return {"success": True, "answer": text}
            return {"success": False, "error": "Réponse vide d'Ollama Vision"}
        except Exception as e:
            logger.error(f"Erreur Ollama Vision: {e}")
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # P3.1 — nouvelles méthodes vision
    # ------------------------------------------------------------------

    async def analyze_with_openai(
        self,
        image_path: str,
        prompt: str,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyse un screenshot avec GPT Vision (OpenAI).

        Utilise _build_openai_payload de multi_provider pour construire le payload
        afin d'éviter toute duplication de logique OpenAI.
        """
        import httpx
        from dotenv import load_dotenv
        load_dotenv()

        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            return {"success": False, "error": "OPENAI_API_KEY non configurée"}

        image_base64 = await self._encode_image_base64(image_path)
        suffix = Path(image_path).suffix.lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        mime_type = mime_map.get(suffix, "image/png")

        try:
            from src.llm.providers import models_with_capability, AVAILABLE_MODELS, ProviderType
            openai_models = [
                n for n in models_with_capability("vision_describe")
                if AVAILABLE_MODELS[n].provider == ProviderType.OPENAI
            ]
            model_name = openai_models[0] if openai_models else "gpt-5.4-nano"
        except Exception:
            model_name = "gpt-5.4-nano"

        # Messages vision : user role avec content array (image + texte)
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}},
                {"type": "text", "text": prompt},
            ],
        }]

        # Payload centralisé : pas de max_tokens cap, temperature basse pour legacy
        try:
            from src.llm.multi_provider import MultiProviderLLM
            payload = MultiProviderLLM._build_openai_payload(
                model_name, messages,
                temperature=0.1,
                max_tokens=None,  # pas de cap — l'API décide
            )
        except Exception:
            # Fallback minimal si import échoue
            payload = {"model": model_name, "messages": messages}

        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
            text = data["choices"][0]["message"]["content"]
            return {"success": True, "answer": text}
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            body = e.response.text[:1000] if e.response else "(no body)"
            logger.error(f"OpenAI Vision HTTP {status}: {body}")
            raise
        except Exception as e:
            logger.error(f"Erreur OpenAI Vision: {e}")
            raise

    async def analyze_with_xai(
        self,
        image_path: str,
        prompt: str,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyse un screenshot avec Grok Vision (xAI)."""
        import httpx
        from dotenv import load_dotenv
        load_dotenv()

        key = api_key or os.getenv("XAI_API_KEY")
        if not key:
            return {"success": False, "error": "XAI_API_KEY non configurée"}

        image_base64 = await self._encode_image_base64(image_path)
        suffix = Path(image_path).suffix.lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        mime_type = mime_map.get(suffix, "image/png")

        # Modèle de grounding rapide xAI
        model_name = "grok-4.20-0309-non-reasoning"

        payload = {
            "model": model_name,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post("https://api.x.ai/v1/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
            text = data["choices"][0]["message"]["content"]
            return {"success": True, "answer": text}
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            logger.error(f"xAI Vision HTTP {status}: {e}")
            raise
        except Exception as e:
            logger.error(f"Erreur xAI Vision: {e}")
            raise

    # ------------------------------------------------------------------
    # P3.3 — provider health (instance-level)
    # ------------------------------------------------------------------

    def _is_provider_available(self, name: str) -> bool:
        """Vérifie si un provider est disponible (pas en cooldown, pas permanent)."""
        import time
        entry = self._provider_health.get(name)
        if entry is None:
            return True
        if entry.permanent:
            return False
        if time.time() < entry.cooldown_until:
            return False
        return True

    def _record_provider_failure(self, name: str, error: Exception) -> None:
        """Enregistre un échec provider et applique cooldown ou permanent selon l'erreur."""
        import time
        entry = self._provider_health.get(name)
        if entry is None:
            entry = _ProviderHealthEntry()
            self._provider_health[name] = entry

        error_str = str(error).lower()
        status_code = None
        try:
            # httpx.HTTPStatusError a un attribut response
            status_code = error.response.status_code  # type: ignore[union-attr]
        except Exception:
            pass

        # Permanent : auth error ou clé manquante
        if status_code in (401, 403) or "api_key" in error_str or "non configurée" in error_str:
            entry.permanent = True
        else:
            # Transitoire : rate-limit, timeout, 5xx, connexion
            entry.failures += 1
            entry.cooldown_until = time.time() + 60.0

        entry.last_error = str(error)[:200]

    # ------------------------------------------------------------------
    # P3.4 helper — dispatch par provider name
    # ------------------------------------------------------------------

    async def _call_analyze(self, provider: str, image_path: str, prompt: str) -> str:
        """Dispatch vers la méthode analyze_with_<provider> et retourne le texte brut.
        
        Lève une exception si le provider échoue (pour que le caller puisse catchier).
        """
        if provider == "google":
            result = await self.analyze_with_gemini(image_path, prompt)
        elif provider == "anthropic":
            result = await self.analyze_with_claude(image_path, prompt)
        elif provider == "openai":
            result = await self.analyze_with_openai(image_path, prompt)
        elif provider == "xai":
            result = await self.analyze_with_xai(image_path, prompt)
        elif provider == "ollama":
            result = await self.analyze_with_ollama(image_path, prompt)
        else:
            raise ValueError(f"Provider vision inconnu: {provider}")

        if not result.get("success"):
            raise RuntimeError(result.get("error", f"{provider} vision failed"))

        return result.get("answer", result.get("text", ""))

    async def find_element_coordinates(
        self,
        image_path: str,
        element_description: str,
        provider: str = "google"
    ) -> Dict[str, Any]:
        """
        Trouve les coordonnées d'un élément dans un screenshot via LLM Vision.
        
        AMÉLIORATION 2026: Utilise le scaling automatique des coordonnées
        pour garantir la précision des clics sur écrans haute résolution.
        
        Args:
            image_path: Chemin vers le screenshot
            element_description: Description de l'élément à trouver
            provider: "google" ou "anthropic"
            
        Returns:
            {"success": bool, "x": int, "y": int, "found": bool, "scale_factor": float}
        """
        import json
        
        # 1. PRÉPARER L'IMAGE (resize + calcul scale factor)
        prepared_path, scale_factor, orig_w, orig_h, pad_offset_x, pad_offset_y = await self.prepare_screenshot_for_llm(image_path)
        
        # 2. PROMPT OPTIMISÉ (best practices 2026)
        prompt = f"""Analyse ce screenshot et trouve l'élément UI suivant : "{element_description}"

INSTRUCTIONS CRITIQUES :
1. Regarde attentivement TOUT l'écran, en particulier les barres de navigation, menus, et zones de contenu
2. Identifie l'élément par son texte, icône, forme, couleur, ou position contextuelle
3. Si c'est un bouton ou lien, trouve son CENTRE exact
4. Les coordonnées doivent être relatives à l'image que tu vois (coin supérieur gauche = 0,0)

FORMAT DE RÉPONSE STRICT (JSON uniquement, sans markdown) :
{{"found": true, "x": <X du centre>, "y": <Y du centre>, "confidence": "high", "element_type": "button", "description": "<ce que tu as trouvé>"}}

Si l'élément N'EST PAS VISIBLE sur l'écran :
{{"found": false, "x": 0, "y": 0, "confidence": "none", "element_type": "unknown", "description": "Non trouvé"}}

RÉPONDS UNIQUEMENT AVEC LE JSON, sans explication ni markdown."""

        # 3. CASCADE VIA ROUTER (P3.4) — LLM providers uniquement
        from .cu_router import build_vision_policy, get_execution_mode

        answer: Optional[str] = None
        for prov in build_vision_policy("vision_grounding"):
            if not self._is_provider_available(prov):
                logger.debug(f"find_element_coordinates: skip {prov} (cooldown/permanent)")
                continue
            try:
                answer = await self._call_analyze(prov, prepared_path, prompt)
                logger.debug(f"find_element_coordinates: succès via {prov}")
                break
            except Exception as exc:
                logger.info(f"🔄 {prov} échec ({exc}) → provider suivant")
                self._record_provider_failure(prov, exc)

        # Fallback OCR si tous les LLM ont échoué (toujours, pas seulement local)
        if answer is None:
            logger.info("🔄 Tous les providers LLM échoués → OCR local (fallback final)")
            ocr_result = await self._find_element_with_ocr(image_path, element_description)
            return ocr_result
        
        # 4. PARSER LE JSON - Extraction multi-stratégie (Phase 4.7)
        coords = self._extract_json_robust(answer)
        
        if coords is None:
            logger.warning(f"Impossible d'extraire JSON de la réponse LLM")
            # Fallback OCR si le JSON est invalide
            return await self._find_element_with_ocr(image_path, element_description)
        
        if not coords.get("found", False):
            return {
                "success": True,
                "found": False,
                "x": 0,
                "y": 0,
                "confidence": "none",
                "description": coords.get("description", "Non trouvé"),
                "scale_factor": scale_factor
            }
        
        # 5. APPLIQUER LE SCALING (CRITIQUE !) - Phase 4.15: round() au lieu de int()
        llm_x = round(float(coords.get("x", 0)))
        llm_y = round(float(coords.get("y", 0)))
        screen_x, screen_y = self.scale_coordinates_to_screen(
            llm_x,
            llm_y,
            scale_factor,
            pad_offset_x=pad_offset_x,
            pad_offset_y=pad_offset_y,
        )
        
        logger.debug(f"🎯 Coordonnées: LLM({llm_x}, {llm_y}) → Écran({screen_x}, {screen_y}) [scale: {scale_factor:.2f}]")
        
        return {
            "success": True,
            "found": True,
            "x": screen_x,
            "y": screen_y,
            "llm_x": llm_x,  # Coordonnées originales du LLM (pour debug)
            "llm_y": llm_y,
            "scale_factor": scale_factor,
            "confidence": coords.get("confidence", "low"),
            "element_type": coords.get("element_type", "unknown"),
            "description": coords.get("description", "")
        }
    
    def _extract_json_robust(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Extraction JSON multi-stratégie (Phase 4.7).
        Essaie plusieurs méthodes pour extraire un JSON valide.
        """
        import re
        import json
        
        if not text:
            return None
        
        # Stratégie 1: JSON dans bloc markdown ```json ... ```
        if "```json" in text:
            try:
                json_str = text.split("```json")[1].split("```")[0].strip()
                return json.loads(json_str)
            except (json.JSONDecodeError, IndexError):
                pass  # essayer méthode suivante
        
        # Stratégie 2: JSON dans bloc markdown ``` ... ```
        if "```" in text:
            try:
                json_str = text.split("```")[1].split("```")[0].strip()
                return json.loads(json_str)
            except (json.JSONDecodeError, IndexError):
                pass  # essayer méthode suivante
        
        # Stratégie 3: Chercher un objet JSON avec regex
        json_pattern = r'\{[^{}]*"found"[^{}]*\}'
        matches = re.findall(json_pattern, text, re.DOTALL)
        for match in matches:
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                pass  # essayer méthode suivante
        
        # Stratégie 4: Texte brut est du JSON
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass  # essayer méthode suivante
        
        # Stratégie 5: Chercher entre { et } le plus large possible
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass  # essayer méthode suivante
        
        return None
    
    async def describe_screen_llm(
        self,
        image_path: str,
        provider: str = "google"
    ) -> Dict[str, Any]:
        """Décrit l'écran avec LLM Vision."""
        prompt = """Décris ce screenshot de manière concise:
- Quelle application est visible?
- Quels éléments cliquables vois-tu?
- Où se trouve la barre de recherche si présente?",

Réponds en JSON: {"app": "...", "elements": [...], "search_bar": {"x": N, "y": N} ou null}"""

        if provider == "google":
            return await self.analyze_with_gemini(image_path, prompt)
        else:
            return await self.analyze_with_claude(image_path, prompt)

    async def analyze_screen(self, image: Image.Image) -> Dict[str, Any]:
        """Analyse complète d'une capture d'écran (OCR)."""
        return self.analyzer.describe_screen(image)
    
    async def find_element(
        self, 
        image: Image.Image, 
        description: str
    ) -> Optional[Tuple[int, int]]:
        """Trouve un élément décrit en langage naturel (OCR)."""
        region = self.analyzer.find_text(image, description)
        
        if region and region.width > 0:
            return region.x + region.width // 2, region.y + region.height // 2
        
        return None
    
    async def get_window_content(self, image: Image.Image) -> str:
        """Retourne le contenu textuel d'une fenêtre."""
        return self.analyzer.extract_text(image)
    
    async def _ocr_fuzzy_find(self, screenshot_path: str, target_text: str) -> Optional[Tuple[int, int]]:
        """P5.3 — Fuzzy OCR grounding : retourne (x, y) du centre du meilleur match.

        Utilise difflib.SequenceMatcher ratio > 0.8 sur les régions OCR.
        Retourne None si aucun match suffisant ou si OCR indisponible.
        """
        import difflib
        try:
            if not PIL_AVAILABLE or not OCR_AVAILABLE:
                return None
            from PIL import Image
            image = Image.open(screenshot_path)
            regions = self.analyzer.find_text_regions(image)
            if not regions:
                return None

            target_lower = target_text.lower()
            best_ratio = 0.0
            best_region = None

            for region in regions:
                ratio = difflib.SequenceMatcher(None, target_lower, region.text.lower()).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_region = region

            if best_region and best_ratio >= 0.8:
                x = best_region.x + best_region.width // 2
                y = best_region.y + best_region.height // 2
                return (x, y)

            return None
        except Exception as e:
            logger.debug(f"_ocr_fuzzy_find error: {e}")
            return None

    async def _find_element_with_ocr(self, image_path: str, element_description: str) -> Dict[str, Any]:
        """
        Fallback OCR: cherche un élément par texte avec pytesseract.
        Moins précis que la vision LLM mais fonctionne hors-ligne.
        """
        try:
            if not PIL_AVAILABLE:
                return {"success": False, "error": "PIL non disponible pour OCR"}
            
            if not OCR_AVAILABLE:
                return {"success": False, "error": "pytesseract non disponible. pip install pytesseract"}
            
            from PIL import Image
            image = Image.open(image_path)
            
            # Chercher le texte dans l'image
            region = self.analyzer.find_text(image, element_description)
            
            if region and region.width > 0:
                x = region.x + region.width // 2
                y = region.y + region.height // 2
                return {
                    "success": True,
                    "found": True,
                    "x": x,
                    "y": y,
                    "confidence": "medium" if region.confidence > 70 else "low",
                    "description": f"Texte '{region.text}' trouvé via OCR"
                }
            
            # Si le texte exact n'est pas trouvé, chercher des mots-clés
            all_regions = self.analyzer.find_text_regions(image)
            element_lower = element_description.lower()
            
            for region in all_regions:
                if any(word in region.text.lower() for word in element_lower.split()):
                    x = region.x + region.width // 2
                    y = region.y + region.height // 2
                    return {
                        "success": True,
                        "found": True,
                        "x": x,
                        "y": y,
                        "confidence": "low",
                        "description": f"Mot-clé '{region.text}' trouvé via OCR"
                    }

            # P5.3 — fuzzy match en dernier recours avant abandon
            fuzzy_coords = await self._ocr_fuzzy_find(image_path, element_description)
            if fuzzy_coords:
                return {
                    "success": True,
                    "found": True,
                    "x": fuzzy_coords[0],
                    "y": fuzzy_coords[1],
                    "confidence": "low",
                    "description": f"'{element_description}' trouvé via OCR fuzzy"
                }

            return {
                "success": True,
                "found": False,
                "x": 0,
                "y": 0,
                "confidence": "none",
                "description": f"'{element_description}' non trouvé via OCR"
            }
            
        except Exception as e:
            logger.error(f"Erreur OCR fallback: {e}")
            return {"success": False, "error": str(e)}
    
    def get_status(self) -> Dict[str, Any]:
        """Retourne le statut du module."""
        return {
            "ocr_available": self.analyzer.ocr_available,
            "llm_vision": True,
            "providers": ["google", "anthropic", "ocr_fallback"]
        }

# Instance singleton avec lock thread-safe (Phase 2.1)
import threading
_vision: Optional[VisionModule] = None
_vision_lock = threading.Lock()


def get_vision() -> VisionModule:
    """Obtient l'instance singleton du module vision (thread-safe)."""
    global _vision
    
    # Double-check locking pattern
    if _vision is None:
        with _vision_lock:
            if _vision is None:
                _vision = VisionModule()
    return _vision
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
