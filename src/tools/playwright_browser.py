"""
🌐 LUMENA - Contrôle Navigateur avec Playwright

Alternative moderne à Selenium avec Playwright.
Inspiré de Moltbot browser/index.ts

Avantages Playwright:
- Plus rapide que Selenium
- Meilleure gestion des timeouts
- Auto-wait intégré
- Screenshots/PDF faciles
- Network interception
"""

from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
import asyncio
import ipaddress
import os
import random
import socket
from loguru import logger


# ─── SSRF Guard (centralisé dans src/utils/url_safety.py) ──────────────────────
from src.utils.url_safety import assert_url_safe  # noqa: E402


try:
    from playwright.async_api import async_playwright, Browser, Page, BrowserContext
    from playwright.async_api import TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright non installé. Installez avec: pip install playwright && playwright install chromium")


MAX_TABS = int(os.getenv("LUMENA_BROWSER_MAX_TABS", "10"))  # Phase 2.3 — auto-fermeture du plus ancien si dépassé
MAX_NETWORK_LOG = 500  # Ring buffer pour les requêtes réseau interceptées
MAX_BATCH_ACTIONS = 50  # Limite batch actions

# Devices prédéfinis pour l'émulation
DEVICE_PRESETS: Dict[str, Dict[str, Any]] = {
    "iphone_14": {"width": 390, "height": 844, "scale": 3, "mobile": True, "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"},
    "iphone_14_pro_max": {"width": 430, "height": 932, "scale": 3, "mobile": True, "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"},
    "pixel_7": {"width": 412, "height": 915, "scale": 2.625, "mobile": True, "ua": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"},
    "ipad_pro": {"width": 1024, "height": 1366, "scale": 2, "mobile": True, "ua": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"},
    "galaxy_s23": {"width": 360, "height": 780, "scale": 3, "mobile": True, "ua": "Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"},
    "desktop_1080p": {"width": 1920, "height": 1080, "scale": 1, "mobile": False, "ua": None},
    "desktop_1440p": {"width": 2560, "height": 1440, "scale": 1, "mobile": False, "ua": None},
}


class PlaywrightBrowser:
    """
    Contrôleur de navigateur avec Playwright.
    
    Alternative moderne à LumenaBrowser (Selenium).
    Utilise l'API async pour de meilleures performances.
    """
    
    def __init__(self, headless: bool = False, profile_name: Optional[str] = "lumena"):
        """
        Initialise le contrôleur.
        
        Args:
            headless: Si True, le navigateur est invisible (défaut: False = visible)
            profile_name: Nom du profil persistant (défaut: 'lumena')
        """
        self.headless = headless
        self.profile_name = profile_name
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        from src.utils.paths import SCREENSHOTS_DIR, BROWSER_PROFILES_DIR, BROWSER_TRACES_DIR
        self._screenshots_dir = SCREENSHOTS_DIR
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        # Profils persistants
        self._profiles_dir = BROWSER_PROFILES_DIR
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        
        # Concurrency guard
        self._lock = asyncio.Lock()
        
        # Session tracking
        self._session_start: Optional[datetime] = None
        self._pages_visited: int = 0
        self._cookies_count: int = 0
        self._active_tab_index: int = 0

        # ── Phase 3 — Tracing & Network Inspection ──
        self._trace_active: bool = False
        self._traces_dir = BROWSER_TRACES_DIR
        self._traces_dir.mkdir(parents=True, exist_ok=True)
        self._network_log: List[Dict[str, Any]] = []
        self._network_listening: bool = False
        self._extra_headers: Dict[str, str] = {}

    def _tabs(self) -> List[Page]:
        if not self._context:
            return []
        return list(self._context.pages)

    def _ensure_active_tab_index(self) -> None:
        tabs = self._tabs()
        if not tabs:
            self._active_tab_index = 0
            self._page = None
            return
        if self._active_tab_index < 0 or self._active_tab_index >= len(tabs):
            self._active_tab_index = max(0, min(self._active_tab_index, len(tabs) - 1))
        self._page = tabs[self._active_tab_index]

    # ─── Phase 2.3 — Smart Tab Manager ─────────────────────────────────────────

    async def _enforce_max_tabs(self) -> int:
        """Ferme les onglets les plus anciens (index 1+) si MAX_TABS atteint.

        Ne ferme jamais l'onglet 0 (principal) ni l'onglet actif.
        Returns: nombre d'onglets fermés.
        """
        tabs = self._tabs()
        closed = 0
        while len(tabs) >= MAX_TABS:
            # Chercher le candidat le plus ancien (index 1 si non actif, sinon 2, etc.)
            victim = None
            for i in range(1, len(tabs)):
                if i != self._active_tab_index:
                    victim = i
                    break
            if victim is None:
                break  # Tous les onglets sont l'actif ou le principal
            try:
                await tabs[victim].close()
                closed += 1
            except Exception:
                break  # onglet déjà fermé
            tabs = self._tabs()
            # Réajuster l'index actif si nécessaire
            if self._active_tab_index > victim:
                self._active_tab_index -= 1
            self._ensure_active_tab_index()
        if closed:
            logger.info(f"Auto-fermé {closed} onglet(s) (MAX_TABS={MAX_TABS})")
        return closed

    async def tab_find(self, query: str) -> Dict[str, Any]:
        """Recherche un onglet par texte (titre ou URL, insensible à la casse).

        Args:
            query: Texte à chercher dans le titre ou l'URL de chaque onglet.

        Returns:
            Dict avec 'matches': liste de {index, title, url, active}.
        """
        self._ensure_active_tab_index()
        tabs = self._tabs()
        q = query.lower()
        matches: List[Dict[str, Any]] = []
        for i, tab in enumerate(tabs):
            try:
                title = await tab.title()
            except Exception:
                title = ""
            url = tab.url or ""
            if q in title.lower() or q in url.lower():
                matches.append({
                    "index": i,
                    "title": title,
                    "url": url,
                    "active": i == self._active_tab_index,
                })
        return {
            "success": True,
            "query": query,
            "matches": matches,
            "count": len(matches),
        }

    @property
    def is_available(self) -> bool:
        """Vérifie si Playwright est disponible."""
        return PLAYWRIGHT_AVAILABLE
    
    @property
    def is_running(self) -> bool:
        """Vérifie si le navigateur est démarré.

        En mode persistent context (profil), self._browser n'est jamais
        assigné — seul self._context l'est.  On doit donc vérifier les
        deux chemins.
        """
        # Mode persistent context (profil) : _browser est None
        if self._context is not None and self._page is not None:
            try:
                return not self._page.is_closed()
            except Exception:
                return False  # page déjà fermée
        # Mode standard
        return self._browser is not None and self._browser.is_connected()
    
    async def start(self) -> bool:
        """
        Démarre le navigateur.
        
        Si profile_name est défini, utilise un profil persistant (cookies gardés).
        
        Returns:
            True si succès
        """
        async with self._lock:
            if not PLAYWRIGHT_AVAILABLE:
                logger.error("Playwright non disponible")
                return False
            
            if self.is_running:
                return True

            # Nettoyer proprement tout état résiduel d'une session précédente crashée
            # pour éviter "NoneType has no attribute 'send'" au prochain navigate()
            if self._playwright is not None:
                try:
                    if self._context:
                        await self._context.close()
                    if self._browser:
                        await self._browser.close()
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
                self._browser = None
                self._context = None
                self._page = None
            
            try:
                self._playwright = await async_playwright().start()

                # Phase 2.4 — Configuration anti-détection
                from .browser_stealth import build_stealth_config
                stealth = build_stealth_config(headless=self.headless)
                ua = stealth["user_agent"]
                viewport = stealth["viewport"]
                args = stealth["args"]
                locale = stealth["locale"]
                tz = stealth["timezone_id"]
                proxy = stealth.get("proxy")
                stealth_js = stealth["stealth_js"]
                extra_headers = stealth.get("extra_http_headers", {})
                
                # Phase 18: Utiliser profil persistant si spécifié
                if self.profile_name:
                    profile_path = self._profiles_dir / self.profile_name
                    profile_path.mkdir(parents=True, exist_ok=True)
                    
                    context_kwargs = dict(
                        user_data_dir=str(profile_path),
                        headless=self.headless,
                        viewport=viewport,
                        user_agent=ua,
                        locale=locale,
                        timezone_id=tz,
                        args=args,
                    )
                    if proxy:
                        context_kwargs["proxy"] = proxy
                    
                    try:
                        self._context = await self._playwright.chromium.launch_persistent_context(
                            **context_kwargs
                        )
                    except Exception as e_profile:
                        # Profile verrouillé → tuer les chrome zombies et réessayer
                        error_msg = str(e_profile)
                        if "Target page, context or browser has been closed" in error_msg or "lock" in error_msg.lower():
                            logger.warning(f"🔒 Profil '{self.profile_name}' verrouillé, nettoyage en cours...")
                            import subprocess
                            try:
                                # Tuer les processus Chrome qui utilisent notre profil
                                subprocess.run(
                                    ["taskkill", "/F", "/IM", "chrome.exe"],
                                    capture_output=True, timeout=5,
                                )
                                await asyncio.sleep(1)
                            except Exception:
                                pass  # kill chrome zombie best-effort
                            
                            # Supprimer le lock file si présent
                            lock_file = profile_path / "SingletonLock"
                            if lock_file.exists():
                                try:
                                    lock_file.unlink()
                                except Exception:
                                    pass  # lock file cleanup best-effort
                            
                            # Réessayer une fois
                            logger.info("🔄 Réessai du lancement persistent_context...")
                            self._context = await self._playwright.chromium.launch_persistent_context(
                                **context_kwargs
                            )
                        else:
                            raise  # Autre erreur → propager
                    
                    # Injecter le JS anti-détection
                    await self._context.add_init_script(stealth_js)
                    self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
                    if extra_headers:
                        await self._page.set_extra_http_headers(extra_headers)
                    self._active_tab_index = 0
                    logger.info(f"🌐 Playwright démarré avec profil '{self.profile_name}' (stealth v2)")
                else:
                    # Mode standard sans persistance
                    launch_kwargs = dict(
                        headless=self.headless,
                        args=args,
                    )
                    if proxy:
                        launch_kwargs["proxy"] = proxy
                    
                    self._browser = await self._playwright.chromium.launch(**launch_kwargs)
                    self._context = await self._browser.new_context(
                        viewport=viewport,
                        user_agent=ua,
                        locale=locale,
                        timezone_id=tz,
                    )
                    # Injecter le JS anti-détection
                    await self._context.add_init_script(stealth_js)
                    self._page = await self._context.new_page()
                    if extra_headers:
                        await self._page.set_extra_http_headers(extra_headers)
                    self._active_tab_index = 0
                    logger.info(f"🌐 Playwright démarré (headless={self.headless}, stealth v2)")
                
                self._session_start = datetime.now()
                # Stocker les extra headers pour les nouvelles pages
                self._extra_headers = extra_headers
                return True
                
            except Exception as e:
                logger.error(f"Erreur démarrage Playwright: {e}")
                return False
    
    async def stop(self):
        """Arrête le navigateur."""
        async with self._lock:
            try:
                if self._context:
                    await self._context.close()
                if self._browser:
                    await self._browser.close()
                if self._playwright:
                    await self._playwright.stop()
                
                self._page = None
                self._context = None
                self._browser = None
                self._playwright = None
                
                logger.info("🌐 Playwright arrêté")
            except Exception as e:
                logger.error(f"Erreur arrêt Playwright: {e}")
    
    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Dict[str, Any]:
        """
        Navigue vers une URL.
        
        Args:
            url: URL à visiter
            wait_until: Quand considérer le chargement terminé
                        ("load", "domcontentloaded", "networkidle")
        
        Returns:
            Dict avec le statut et les infos de la page
        """
        if not self.is_running:
            if not await self.start():
                return {"success": False, "error": "Navigateur non démarré"}
        
        # SSRF Guard — bloquer navigation vers réseaux privés/localhost
        try:
            assert_url_safe(url)
        except ValueError as e:
            logger.warning(f"🛡️ SSRF bloqué: {e}")
            return {"success": False, "error": f"Navigation bloquée: {e}"}

        try:
            self._ensure_active_tab_index()
            # Récupération : context ok mais page None/fermée → créer une nouvelle page
            if not self._page or (not callable(getattr(self._page, "is_closed", None))) or self._page.is_closed():
                if self._context:
                    logger.debug("🔄 Playwright: page absente, création d'un nouvel onglet de récupération")
                    try:
                        self._page = await self._context.new_page()
                        self._active_tab_index = max(0, len(self._tabs()) - 1)
                    except Exception as e_new:
                        return {"success": False, "error": f"Impossible de créer un onglet: {e_new}"}
                else:
                    return {"success": False, "error": "Aucun onglet actif — relancez le navigateur"}
            response = await self._page.goto(url, wait_until=wait_until, timeout=30000)
            self._pages_visited += 1
            
            return {
                "success": True,
                "url": self._page.url,
                "title": await self._page.title(),
                "status": response.status if response else None
            }
        except PlaywrightTimeout:
            return {"success": False, "error": f"Timeout: {url}"}
        except Exception as e:
            err_str = str(e)
            # Page morte car fermée manuellement par l'utilisateur :
            # 'NoneType' object has no attribute 'send'
            # → réinitialiser l'état et redémarrer proprement, puis réessayer
            if "NoneType" in err_str and "send" in err_str:
                logger.warning("🔄 Page Playwright morte (fermée manuellement?) — redémarrage forcé")
                # Invalider tous les handles morts (process déjà terminé)
                self._page = None
                self._context = None
                self._browser = None
                self._playwright = None
                if await self.start():
                    try:
                        response = await self._page.goto(url, wait_until=wait_until, timeout=30000)
                        self._pages_visited += 1
                        return {
                            "success": True,
                            "url": self._page.url,
                            "title": await self._page.title(),
                            "status": response.status if response else None,
                        }
                    except Exception as e2:
                        return {"success": False, "error": str(e2)}
            return {"success": False, "error": err_str}

    async def open_tab(self, url: str = "", switch_to_new: bool = True) -> Dict[str, Any]:
        """Ouvre un nouvel onglet (optionnellement sur une URL)."""
        # SSRF Guard
        if url:
            try:
                assert_url_safe(url)
            except ValueError as e:
                logger.warning(f"🛡️ SSRF bloqué (open_tab): {e}")
                return {"success": False, "error": f"Navigation bloquée: {e}"}
        if not self.is_running:
            if not await self.start():
                return {"success": False, "error": "Navigateur non démarré"}

        if not self._context:
            return {"success": False, "error": "Contexte navigateur indisponible"}

        try:
            # Phase 2.3 — Auto-fermeture si MAX_TABS atteint
            await self._enforce_max_tabs()
            new_page = await self._context.new_page()
            tabs = self._tabs()
            new_index = max(0, len(tabs) - 1)

            if switch_to_new:
                self._active_tab_index = new_index
                self._page = new_page

            if url:
                nav_result = await self.navigate(url)
                if not nav_result.get("success"):
                    return nav_result

            return {
                "success": True,
                "tab_index": new_index,
                "tabs_count": len(self._tabs()),
                "active_tab": self._active_tab_index,
                "url": new_page.url,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_tabs(self) -> Dict[str, Any]:
        """Liste les onglets ouverts."""
        self._ensure_active_tab_index()
        tabs = self._tabs()
        payload: List[Dict[str, Any]] = []
        for i, tab in enumerate(tabs):
            try:
                title = await tab.title()
            except Exception:
                title = ""  # onglet fermé ou erreur
            payload.append(
                {
                    "index": i,
                    "active": i == self._active_tab_index,
                    "url": tab.url,
                    "title": title,
                }
            )
        return {
            "success": True,
            "tabs": payload,
            "tabs_count": len(payload),
            "active_tab": self._active_tab_index if payload else None,
        }

    async def switch_tab(self, index: int) -> Dict[str, Any]:
        """Bascule sur un onglet donné par index."""
        tabs = self._tabs()
        if not tabs:
            return {"success": False, "error": "Aucun onglet ouvert"}
        if index < 0 or index >= len(tabs):
            return {"success": False, "error": f"Index d'onglet invalide: {index}"}

        try:
            target = tabs[index]
            await target.bring_to_front()
            self._active_tab_index = index
            self._page = target
            return {
                "success": True,
                "active_tab": index,
                "url": target.url,
                "title": await target.title(),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def switch_tab_by_query(self, query: str) -> Dict[str, Any]:
        """Bascule sur le premier onglet dont le titre ou l'URL contient *query*.

        Recherche insensible à la casse. Utilise tab_find() en interne.
        """
        result = await self.tab_find(query)
        if not result["matches"]:
            return {"success": False, "error": f"Aucun onglet ne correspond à: {query}"}
        best = result["matches"][0]
        return await self.switch_tab(best["index"])

    async def close_tab(self, index: Optional[int] = None) -> Dict[str, Any]:
        """Ferme un onglet (actif par défaut)."""
        tabs = self._tabs()
        if not tabs:
            return {"success": False, "error": "Aucun onglet ouvert"}

        target_index = self._active_tab_index if index is None else index
        if target_index < 0 or target_index >= len(tabs):
            return {"success": False, "error": f"Index d'onglet invalide: {target_index}"}

        try:
            await tabs[target_index].close()

            remaining = self._tabs()
            if not remaining and self._context:
                self._page = await self._context.new_page()
                self._active_tab_index = 0
            else:
                if self._active_tab_index >= len(remaining):
                    self._active_tab_index = max(0, len(remaining) - 1)
                self._ensure_active_tab_index()

            return {
                "success": True,
                "closed_tab": target_index,
                "tabs_count": len(self._tabs()),
                "active_tab": self._active_tab_index,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def click(self, selector: str, timeout: int = 5000) -> Dict[str, Any]:
        """
        Clique sur un élément.
        
        Args:
            selector: Sélecteur CSS ou XPath
            timeout: Timeout en ms
        """
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        
        try:
            # Humanisation: délai pré-clic + mouvement souris
            from .browser_stealth import human_delay
            await asyncio.sleep(human_delay(60, 250) / 1000)
            await self._page.click(selector, timeout=timeout)
            await asyncio.sleep(human_delay(30, 120) / 1000)
            return {"success": True, "clicked": selector}
        except PlaywrightTimeout:
            return {"success": False, "error": f"Élément non trouvé: {selector}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def type_text(self, selector: str, text: str, delay: int = 50) -> Dict[str, Any]:
        """
        Tape du texte dans un champ.
        
        Args:
            selector: Sélecteur du champ
            text: Texte à taper
            delay: Délai entre les touches (ms)
        """
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        
        try:
            from .browser_stealth import human_delay
            await asyncio.sleep(human_delay(50, 200) / 1000)
            await self._page.fill(selector, "")  # Clear first
            # Délai variable entre touches (±30% du delay)
            jitter_delay = max(20, delay + random.randint(-delay // 3, delay // 3))
            await self._page.type(selector, text, delay=jitter_delay)
            return {"success": True, "typed": len(text)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def get_text(self, selector: str = "body") -> Dict[str, Any]:
        """
        Récupère le texte d'un élément.
        
        Args:
            selector: Sélecteur CSS (défaut: body = toute la page)
        """
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        
        try:
            text = await self._page.text_content(selector)
            return {
                "success": True,
                "text": text[:5000] if text else "",
                "length": len(text) if text else 0
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def screenshot(self, filename: Optional[str] = None, full_page: bool = False) -> Dict[str, Any]:
        """
        Prend une capture d'écran.
        
        Args:
            filename: Nom du fichier (auto-généré si None)
            full_page: Si True, capture toute la page
        """
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        
        try:
            if not filename:
                filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            
            filepath = self._screenshots_dir / filename
            await self._page.screenshot(path=str(filepath), full_page=full_page)
            
            return {
                "success": True,
                "path": str(filepath),
                "full_page": full_page
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def get_accessibility_tree(self) -> Dict[str, Any]:
        """
        Récupère l'arbre d'accessibilité de la page.
        
        Inspiré de Moltbot accessibility.ts - utile pour comprendre
        la structure de la page de façon sémantique.
        """
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        
        try:
            snapshot = await self._page.accessibility.snapshot()
            return {
                "success": True,
                "tree": snapshot
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def evaluate(self, script: str) -> Dict[str, Any]:
        """
        Exécute du JavaScript dans la page.
        
        Args:
            script: Code JavaScript à exécuter
        """
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        
        try:
            result = await self._page.evaluate(script)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def search_google(self, query: str) -> Dict[str, Any]:
        """
        Fait une recherche Google et retourne les résultats.
        
        Args:
            query: Termes de recherche
        """
        try:

            if not await self._ensure_started():
                return {"success": False, "error": "Navigateur non démarré"}
            if not self._page:
                return {"success": False, "error": "Page non chargée"}

            # Aller sur Google seulement si nécessaire pour éviter de reset la session.
            current_url = (self._page.url or "").lower()
            if "google." not in current_url:
                nav = await self.navigate("https://www.google.com")
                if not nav.get("success"):
                    return {"success": False, "error": nav.get("error", "navigation google échouée")}

            # Accepter les cookies (multi-langue / multi-bannière).
            await self.accept_cookies()

            # Taper la recherche sur un champ VISIBLE (évite input[type=hidden]).
            used_dom_fallback = False
            try:
                search_box = self._page.locator("textarea[name='q']:visible, input[name='q']:visible").first
                await search_box.wait_for(state="visible", timeout=8000)
                await search_box.fill(query, timeout=8000)
                await search_box.press("Enter", timeout=5000)
            except Exception:
                used_dom_fallback = True  # formulaire non trouvé, fallback DOM
                injected = await self._page.evaluate(
                    """
                    (q) => {
                        const boxes = Array.from(document.querySelectorAll("textarea[name='q'], input[name='q']"));
                        const visible = boxes.find((el) => {
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            return el.type !== 'hidden' && style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                        });
                        if (!visible) return false;
                        visible.focus();
                        visible.value = q;
                        visible.dispatchEvent(new Event('input', { bubbles: true }));
                        visible.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
                        return true;
                    }
                    """,
                    query,
                )
                if not injected:
                    return {"success": False, "error": "Champ de recherche Google introuvable (visible)"}

            # Attendre les résultats (sélecteurs tolérants aux variations Google).
            await self._page.wait_for_selector("#search, main, [role='main']", timeout=12000)

            # Extraire les résultats de manière robuste (A/B tests Google).
            results = await self._page.evaluate("""
                () => {
                    const out = [];
                    const seen = new Set();

                    const h3Links = Array.from(document.querySelectorAll('a h3'));
                    for (const h3 of h3Links) {
                        const a = h3.closest('a');
                        const href = (a?.href || '').trim();
                        const title = (h3.textContent || '').trim();
                        if (!href || !title || !/^https?:/i.test(href)) continue;
                        if (seen.has(href)) continue;
                        const card = h3.closest('div');
                        const snippetEl = card?.querySelector('.VwiC3b, .IsZvec, .MUxGbd, .yXK7lf, span');
                        out.push({ title, url: href, snippet: (snippetEl?.textContent || '').trim() });
                        seen.add(href);
                        if (out.length >= 10) break;
                    }

                    if (out.length === 0) {
                        const links = Array.from(document.querySelectorAll("a[href^='http']"));
                        for (const a of links) {
                            const href = (a.getAttribute('href') || '').trim();
                            const text = (a.textContent || '').trim();
                            if (!href || !text || text.length < 8) continue;
                            if (seen.has(href)) continue;
                            out.push({ title: text.slice(0, 140), url: href, snippet: '' });
                            seen.add(href);
                            if (out.length >= 10) break;
                        }
                    }

                    return out;
                }
            """)
            
            return {
                "success": True,
                "query": query,
                "source": "Google",
                "dom_fallback_used": used_dom_fallback,
                "results_count": len(results),
                "results": [
                    {
                        "position": i + 1,
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "description": r.get("snippet", ""),
                        "snippet": r.get("snippet", ""),
                    }
                    for i, r in enumerate(results)
                ],
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def wait_for_selector(self, selector: str, timeout: int = 5000) -> Dict[str, Any]:
        """Attend qu'un élément apparaisse."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        
        try:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return {"success": True, "found": selector}
        except PlaywrightTimeout:
            return {"success": False, "error": f"Timeout: {selector}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def scroll(self, direction: str = "down", amount: int = 500) -> Dict[str, Any]:
        """
        Scrolle la page.
        
        Args:
            direction: "up", "down", "top", "bottom"
            amount: Nombre de pixels
        """
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        
        try:
            if direction == "top":
                await self._page.evaluate("window.scrollTo(0, 0)")
                return {"success": True, "scrolled": "top"}
            elif direction == "bottom":
                await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                return {"success": True, "scrolled": "bottom"}
            else:
                delta = amount if direction == "down" else -amount
                await self._page.mouse.wheel(0, delta)
                return {"success": True, "scrolled": delta}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def get_page_info(self) -> Dict[str, Any]:
        """Retourne les informations sur la page actuelle."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        
        try:
            return {
                "success": True,
                "url": self._page.url,
                "title": await self._page.title()
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ========== Session & Profile Management (Phase 18) ==========
    
    def get_session_info(self) -> Dict[str, Any]:
        """Retourne les informations sur la session actuelle."""
        from datetime import datetime
        
        duration = None
        if self._session_start:
            duration = (datetime.now() - self._session_start).total_seconds()
        
        return {
            "running": self.is_running,
            "profile": self.profile_name or "anonymous",
            "session_duration_sec": duration,
            "pages_visited": self._pages_visited,
            "headless": self.headless
        }
    
    def list_profiles(self) -> List[str]:
        """Liste tous les profils browser disponibles."""
        return [p.name for p in self._profiles_dir.iterdir() if p.is_dir()]
    
    async def get_cookies(self) -> List[Dict[str, Any]]:
        """Récupère les cookies de la session actuelle."""
        if not self._context:
            return []
        try:
            return await self._context.cookies()
        except Exception:
            return []  # cookies non récupérables

    # ========== Selenium-compatible Methods (Phase 2.1 Migration) ==========

    @staticmethod
    def _build_selector(selector: str, by: str = "css") -> str:
        """Convert Selenium-style 'by' types to Playwright selectors."""
        by_lower = by.lower().strip()
        if by_lower == "xpath":
            return f"xpath={selector}"
        if by_lower == "id":
            return f"#{selector}"
        if by_lower == "class":
            return f".{selector}"
        if by_lower == "name":
            return f"[name='{selector}']"
        if by_lower == "text":
            return f"text='{selector}'"
        if by_lower == "partial_text":
            return f"text={selector}"
        return selector  # css is default

    async def _ensure_started(self) -> bool:
        """Auto-start the browser if not running."""
        if self.is_running:
            return True
        return await self.start()

    async def accept_cookies(self) -> Dict[str, Any]:
        """Tente d'accepter automatiquement les bandeaux cookies les plus courants."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}

        css_candidates = [
            "#onetrust-accept-btn-handler",
            "#L2AGLb",                          # Google consent "J'accepte"
            "button[id='L2AGLb']",              # Google consent variant
            "[aria-label='Tout accepter']",     # Google FR consent
            "[aria-label='Accept all']",        # Google EN consent
            "form[action*='consent'] button",   # Google consent form
            "button[aria-label*='Accept']",
            "button[aria-label*='Accepter']",
            "button[id*='accept']",
            "button[class*='accept']",
            "button[id*='consent']",
            "button[class*='consent']",
            "#didomi-notice-agree-button",      # Didomi (courant FR)
            ".sd-cmp-3cRQ2",                    # SourcePoint
        ]

        for selector in css_candidates:
            try:
                await self._page.click(selector, timeout=1000)
                return {"success": True, "method": "css", "selector": selector}
            except Exception:
                continue  # essayer le sélecteur suivant

        text_candidates = [
            "Tout accepter", "Accepter", "Accepter et fermer", "J'accepte",
            "Accept all", "Accept", "I agree", "Agree", "Consent",
        ]

        for text in text_candidates:
            try:
                await self._page.click(f"button:has-text('{text}')", timeout=1000)
                return {"success": True, "method": "text", "selector": text}
            except Exception:
                try:
                    await self._page.click(f"a:has-text('{text}')", timeout=500)
                    return {"success": True, "method": "text", "selector": text}
                except Exception:
                    continue  # essayer le texte suivant

        return {"success": False, "error": "Aucun bouton cookie reconnu"}

    async def click_at(self, x: int, y: int) -> Dict[str, Any]:
        """Clique à des coordonnées dans le viewport (humanisé)."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}

        try:
            from .browser_stealth import human_delay
            # Mouvement progressif vers la cible (2 étapes intermédiaires)
            vp = self._page.viewport_size or {"width": 1920, "height": 1080}
            cx, cy = vp.get("width", 1920) // 2, vp.get("height", 1080) // 2
            mid_x = cx + (x - cx) // 2 + random.randint(-15, 15)
            mid_y = cy + (y - cy) // 2 + random.randint(-10, 10)
            await self._page.mouse.move(mid_x, mid_y)
            await asyncio.sleep(human_delay(30, 80) / 1000)
            await self._page.mouse.move(x + random.randint(-2, 2), y + random.randint(-2, 2))
            await asyncio.sleep(human_delay(20, 60) / 1000)
            await self._page.mouse.click(x, y)
            vp = self._page.viewport_size or {}
            return {
                "success": True,
                "clicked_at": {"x": x, "y": y},
                "viewport": {"w": vp.get("width", 0), "h": vp.get("height", 0)},
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def click_element(self, selector: str, by: str = "css") -> Dict[str, Any]:
        """
        Clique sur un élément via sélecteur Selenium-compatible.

        Args:
            selector: Sélecteur de l'élément
            by: Type: css, xpath, id, class, name, text, partial_text
        """
        if not self._page:
            return {"success": False, "error": "Page non chargée"}

        pw_selector = self._build_selector(selector, by)
        try:
            from .browser_stealth import human_delay
            await asyncio.sleep(human_delay(60, 200) / 1000)
            await self._page.click(pw_selector, timeout=5000)
            await asyncio.sleep(human_delay(30, 100) / 1000)
            return {"success": True, "clicked": selector, "method": by}
        except Exception as e:
            return {"success": False, "error": f"Élément non trouvé: {selector} ({e})"}

    async def type_in_field(self, selector: str, text: str, by: str = "css",
                            clear: bool = True) -> Dict[str, Any]:
        """
        Tape du texte dans un champ avec support des sélecteurs Selenium-style.

        Args:
            selector: Sélecteur du champ
            text: Texte à taper
            by: Type: css, xpath, id, class, name
            clear: Effacer le champ avant de taper
        """
        if not self._page:
            return {"success": False, "error": "Page non chargée"}

        pw_selector = self._build_selector(selector, by)
        try:
            if clear:
                await self._page.fill(pw_selector, "")
            await self._page.type(pw_selector, text, delay=50)
            return {"success": True, "typed_in": selector, "text_length": len(text)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def hover(self, selector: str, by: str = "css") -> Dict[str, Any]:
        """Survole un élément (déclenche :hover, tooltips, menus déroulants)."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        pw_selector = self._build_selector(selector, by)
        try:
            await self._page.hover(pw_selector, timeout=5000)
            return {"success": True, "hovered": selector}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def select_option(self, selector: str, value: str = "", label: str = "", index: int = -1,
                            by: str = "css") -> Dict[str, Any]:
        """Sélectionne une option dans un <select> par valeur, label ou index."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        pw_selector = self._build_selector(selector, by)
        try:
            if value:
                selected = await self._page.select_option(pw_selector, value=value, timeout=5000)
            elif label:
                selected = await self._page.select_option(pw_selector, label=label, timeout=5000)
            elif index >= 0:
                selected = await self._page.select_option(pw_selector, index=index, timeout=5000)
            else:
                return {"success": False, "error": "Fournir value, label ou index"}
            return {"success": True, "selected": selected}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def keyboard_press(self, key: str) -> Dict[str, Any]:
        """Presse une touche clavier (ex: Enter, Tab, Escape, ArrowDown, Control+a)."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        try:
            await self._page.keyboard.press(key)
            return {"success": True, "key_pressed": key}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def save_pdf(self, filename: Optional[str] = None) -> Dict[str, Any]:
        """Exporte la page courante en PDF. Nécessite headless=True (Chromium)."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        if not self.headless:
            return {"success": False, "error": "L'export PDF nécessite headless=True. Relancer avec browser_start(headless=True)"}
        try:
            if not filename:
                filename = f"page_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            pdf_dir = self._screenshots_dir
            pdf_dir.mkdir(parents=True, exist_ok=True)
            filepath = pdf_dir / filename
            await self._page.pdf(path=str(filepath))
            return {"success": True, "path": str(filepath)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def upload_file(self, selector: str, file_paths: List[str], by: str = "css") -> Dict[str, Any]:
        """Upload un ou plusieurs fichiers via un <input type='file'>."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        pw_selector = self._build_selector(selector, by)
        try:
            existing = [p for p in file_paths if Path(p).exists()]
            if not existing:
                return {"success": False, "error": f"Aucun fichier trouvé: {file_paths}"}
            await self._page.set_input_files(pw_selector, existing, timeout=5000)
            return {"success": True, "uploaded": existing, "selector": selector}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def block_resources(self, resource_types: Optional[List[str]] = None,
                              url_patterns: Optional[List[str]] = None) -> Dict[str, Any]:
        """Bloque des types de ressources ou URLs (ads, trackers, images, fonts).

        resource_types: ["image", "font", "media", "stylesheet", "script", "xhr", "fetch"]
        url_patterns:   sous-chaînes d'URL à bloquer (ex: ["doubleclick", "google-analytics"])
        """
        if not self._context:
            return {"success": False, "error": "Contexte navigateur non initialisé"}
        default_blocked_urls = [
            "doubleclick.net", "google-analytics.com", "googletagmanager.com",
            "facebook.net", "connect.facebook", "ads.twitter", "scorecardresearch",
        ]
        blocked_urls = url_patterns if url_patterns is not None else default_blocked_urls
        blocked_types = set(resource_types or [])

        async def _handle_route(route, request):
            rtype = request.resource_type
            rurl = request.url
            if rtype in blocked_types:
                await route.abort()
                return
            if any(pat in rurl for pat in blocked_urls):
                await route.abort()
                return
            await route.continue_()

        try:
            await self._context.route("**/*", _handle_route)
            return {
                "success": True,
                "blocked_types": list(blocked_types),
                "blocked_url_patterns": blocked_urls,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def unblock_resources(self) -> Dict[str, Any]:
        """Retire tous les filtres réseau — retour à la navigation normale."""
        if not self._context:
            return {"success": False, "error": "Contexte navigateur non initialisé"}
        try:
            await self._context.unroute_all()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def go_back(self) -> Dict[str, Any]:
        """Retourne à la page précédente."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        try:
            await self._page.go_back(wait_until="domcontentloaded", timeout=10000)
            return {"success": True, "url": self._page.url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def go_forward(self) -> Dict[str, Any]:
        """Avance à la page suivante."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        try:
            await self._page.go_forward(wait_until="domcontentloaded", timeout=10000)
            return {"success": True, "url": self._page.url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def refresh(self) -> Dict[str, Any]:
        """Rafraîchit la page actuelle."""
        if not self._page:
            return {"success": False, "error": "Page non chargée"}
        try:
            await self._page.reload(wait_until="domcontentloaded", timeout=10000)
            return {"success": True, "url": self._page.url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def close_all_tabs_except_main(self) -> Dict[str, Any]:
        """Ferme tous les onglets sauf le premier."""
        tabs = self._tabs()
        if not tabs:
            return {"success": False, "error": "Aucun onglet ouvert"}

        closed = 0
        for tab in tabs[1:]:
            try:
                await tab.close()
                closed += 1
            except Exception:
                pass  # onglet déjà fermé

        self._active_tab_index = 0
        self._ensure_active_tab_index()
        return {"success": True, "closed_tabs": closed}

    async def get_page_content(self, url: Optional[str] = None) -> Dict[str, Any]:
        """
        Récupère le contenu textuel de la page actuelle.

        Args:
            url: URL optionnelle à visiter d'abord
        """
        if not await self._ensure_started():
            return {"success": False, "error": "Navigateur non démarré"}

        if url:
            nav = await self.navigate(url)
            if not nav.get("success"):
                return nav

        if not self._page:
            return {"success": False, "error": "Page non chargée"}

        try:
            # Attendre que le réseau se calme (SPA / rendu JS)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                await asyncio.sleep(2)  # networkidle timeout, fallback sleep

            try:
                await self.accept_cookies()
            except Exception:
                pass  # accept cookies best-effort

            content = await self._extract_readable_content()
            content = self._strip_cookie_noise(content)

            # Détection Cloudflare / WAF / challenge pages
            _waf_patterns = ("just a moment", "checking your browser", "un instant",
                             "please wait", "attention required", "access denied",
                             "cloudflare", "ddos protection")
            content_lower = (content or "").lower()
            if content and len(content.strip()) < 200 and any(p in content_lower for p in _waf_patterns):
                return {
                    "success": False,
                    "error": "Cloudflare/WAF challenge detected — page content not available",
                    "url": self._page.url,
                }

            title = await self._page.title()

            return {
                "success": True,
                "title": title,
                "url": self._page.url,
                "content": content[:10000] if content else "",
                "content_length": len(content) if content else 0,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_tabs(self) -> Dict[str, Any]:
        """Liste les onglets (alias Selenium-compatible de list_tabs)."""
        result = await self.list_tabs()
        if result.get("success"):
            result["count"] = result.get("tabs_count", 0)
        return result

    async def new_tab(self, url: Optional[str] = None) -> Dict[str, Any]:
        """Ouvre un nouvel onglet (alias Selenium-compatible de open_tab)."""
        return await self.open_tab(url or "")

    async def deep_research(self, query: str, max_pages: int = 5) -> Dict[str, Any]:
        """
        Recherche approfondie multi-pages avec synthèse.

        1. Fait une recherche Google
        2. Ouvre chaque résultat dans un onglet
        3. Extrait le contenu pertinent
        4. Synthétise les informations
        """
        if not await self._ensure_started():
            return {"success": False, "error": "Navigateur non démarré"}

        try:
            from datetime import datetime as _dt
            current_year = _dt.now().year

            query_lower = query.lower()
            # Toujours enrichir avec l'annee sauf si deja presente
            enriched = f"{query} {current_year}" if str(current_year) not in query else query

            # Step 1: Search
            search_result = await self.search_google(enriched)
            if not search_result.get("success") or not search_result.get("results"):
                return {"success": False, "error": "Aucun résultat de recherche"}

            results = search_result["results"][:max_pages]

            # Step 2: Open each result, extract content
            page_contents: List[Dict[str, Any]] = []
            opened = 0

            for i, result in enumerate(results):
                url = result.get("url", "")
                if not url or not url.startswith("http"):
                    continue

                try:
                    tab = await self.open_tab(url)
                    if not tab.get("success"):
                        continue
                    opened += 1

                    # Attendre le rendu complet (SPA, JS lourd)
                    try:
                        await self._page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        await asyncio.sleep(2)  # networkidle timeout, fallback sleep

                    try:
                        await self.accept_cookies()
                    except Exception:
                        pass  # accept cookies best-effort

                    content = await self._extract_readable_content()
                    if content:
                        page_contents.append({
                            "position": i + 1,
                            "title": result.get("title", ""),
                            "url": url,
                            "content": content[:3000],
                            "content_length": len(content),
                        })
                except Exception as e:
                    logger.warning(f"Erreur page {url}: {e}")
                    continue

            # Step 3: Close extra tabs
            await self.close_all_tabs_except_main()

            # Step 4: Synthesize
            synthesis = self._synthesize_content(query, page_contents)

            return {
                "success": True,
                "query": query,
                "pages_analyzed": len(page_contents),
                "tabs_opened": opened,
                "sources": [{"title": p["title"], "url": p["url"]} for p in page_contents],
                "synthesis": synthesis,
                "raw_contents": page_contents,
            }
        except Exception as e:
            logger.error(f"Erreur deep_research: {e}")
            return {"success": False, "error": str(e)}

    async def _extract_readable_content(self) -> str:
        """Extrait le contenu lisible (supprime scripts, styles, cookies, etc.)."""
        if not self._page:
            return ""

        try:
            script = """
            try {
                const remove = document.querySelectorAll(
                    'script, style, noscript, iframe, nav, footer, header, aside, '
                    + '.advertisement, .ads, [class*="cookie"], [class*="popup"], [class*="banner"]'
                );
                remove.forEach(el => { try { el.remove(); } catch(e) {} });

                const selectors = [
                    'main', 'article', '[role="main"]', '.content', '#content',
                    '.post-content', '.article-body', '.entry-content', '.page-content'
                ];
                for (const sel of selectors) {
                    const main = document.querySelector(sel);
                    if (main && main.innerText && main.innerText.length > 200) {
                        return main.innerText;
                    }
                }

                return document.body ? document.body.innerText || '' : '';
            } catch(e) {
                return document.body ? document.body.innerText || '' : '';
            }
            """

            content = await self._page.evaluate(script)

            if content:
                import re
                content = re.sub(r'\n{3,}', '\n\n', content)
                content = re.sub(r'[ \t]+', ' ', content)
                content = content.strip()

            return content or ""
        except Exception:
            try:
                text_result = await self.get_text("body")
                return text_result.get("text", "")
            except Exception:
                return ""  # extraction contenu impossible

    @staticmethod
    def _strip_cookie_noise(text: str) -> str:
        """Filtre le bruit des bandeaux cookies/GDPR dans le texte extrait."""
        if not text:
            return ""

        markers = [
            "continuer sans accepter", "avec votre accord", "utilisent des cookies",
            "technologies similaires", "politique de confidentialité",
            "politique de confidentialit", "personnaliser", "cookie", "consent",
            "gdpr", "cmp",
        ]

        lines = [line.strip() for line in text.splitlines()]
        filtered: List[str] = []
        for line in lines:
            if not line:
                if filtered and filtered[-1] != "":
                    filtered.append("")
                continue
            if any(marker in line.lower() for marker in markers):
                continue
            filtered.append(line)

        import re
        cleaned = "\n".join(filtered)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _synthesize_content(self, query: str, page_contents: List[Dict[str, Any]]) -> str:
        """Synthétise le contenu de plusieurs pages en un résumé structuré."""
        import re

        if not page_contents:
            return "Aucun contenu à synthétiser."

        price_patterns = [
            r'(\d+[\s,.]?\d*\s*[€$£])',
            r'([€$£]\s*\d+[\s,.]?\d*)',
            r'(\d+[\s,.]?\d*\s*euros?)',
            r'(tarif\s*:?\s*\d+[\s,.]?\d*)',
            r'(prix\s*:?\s*\d+[\s,.]?\d*)',
            r'(gratuit)',
            r'(entrée\s+libre)',
        ]

        # Détecter si la requête concerne des prix/annonces
        query_lower = query.lower()
        is_price_query = any(kw in query_lower for kw in [
            "prix", "tarif", "€", "euro", "annonce", "achat", "vente",
            "occasion", "budget", "coût", "leboncoin", "lacentrale",
        ])

        synthesis = f"# Résultats de recherche: {query}\n\n"
        synthesis += f"📊 {len(page_contents)} sources analysées\n\n"

        all_prices: List[str] = []

        for page in page_contents:
            synthesis += f"## 📄 {page['title']}\n"
            synthesis += f"🔗 {page['url']}\n\n"

            content = page.get("content", "")

            # Extraction de prix (si pertinent)
            if is_price_query:
                page_prices: List[str] = []
                for pattern in price_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    page_prices.extend(matches)

                if page_prices:
                    unique_prices = list(set(page_prices))[:5]
                    synthesis += f"💰 **Prix trouvés**: {', '.join(unique_prices)}\n\n"
                    all_prices.extend(unique_prices)

            # Extraction de paragraphes pertinents
            paragraphs = [p.strip() for p in content.split('\n\n') if len(p.strip()) > 50]

            if is_price_query:
                keywords = [
                    "prix", "tarif", "€", "euro", "billet", "gratuit", "horaire",
                    "ouvert", "fermé", "réservation", "achat", "visite", "km",
                    "annonce", "occasion",
                ]
            else:
                # Mode actualités / généraliste — extraire les paragraphes les plus longs
                keywords = [w for w in query_lower.split() if len(w) > 3]

            if keywords:
                relevant = [p for p in paragraphs[:15] if any(kw in p.lower() for kw in keywords)]
            else:
                relevant = []
            if not relevant:
                relevant = paragraphs[:3]

            key_info = "\n\n".join(relevant[:3])
            if key_info:
                synthesis += f"{key_info[:800]}\n\n"

            synthesis += "---\n\n"

        if all_prices and is_price_query:
            unique_all = list(set(all_prices))
            synthesis = f"💰 **RÉSUMÉ PRIX**: {', '.join(unique_all[:8])}\n\n" + synthesis

        return synthesis

    # ── Phase 3 — Tracing / Visual Debug ──

    async def trace_start(self) -> Dict[str, Any]:
        """Démarre l'enregistrement Playwright Trace (screenshots + snapshots).

        Le fichier .zip résultant s'ouvre avec: npx playwright show-trace trace.zip
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        if self._trace_active:
            return {"success": False, "error": "Trace déjà en cours. Arrête-la d'abord avec trace_stop."}
        try:
            await self._context.tracing.start(screenshots=True, snapshots=True, sources=False)
            self._trace_active = True
            logger.info("🎬 Playwright Trace démarré (screenshots + snapshots DOM)")
            return {"success": True, "message": "Trace en cours. Navigue, clique, etc. puis appelle trace_stop."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def trace_stop(self, name: Optional[str] = None) -> Dict[str, Any]:
        """Arrête la trace et sauvegarde le fichier .zip.

        Args:
            name: Nom du fichier (défaut: trace_YYYYMMDD_HHMMSS)

        Returns:
            Dict avec le chemin du fichier trace.
        """
        if not self._trace_active:
            return {"success": False, "error": "Aucune trace en cours."}
        try:
            if not name:
                name = f"trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            path = self._traces_dir / f"{name}.zip"
            await self._context.tracing.stop(path=str(path))
            self._trace_active = False
            logger.info(f"🎬 Trace sauvegardée: {path}")
            return {
                "success": True,
                "path": str(path),
                "message": f"Trace sauvegardée. Ouvre avec: npx playwright show-trace {path}",
            }
        except Exception as e:
            self._trace_active = False
            return {"success": False, "error": str(e)}

    # ── Phase 3 — Network Inspection ──

    def _setup_network_listeners(self) -> None:
        """Installe les listeners réseau sur la page active (ring buffer)."""
        if not self._page or self._network_listening:
            return

        def _on_request(request):
            entry = {
                "id": f"r{len(self._network_log)}",
                "timestamp": datetime.now().isoformat(),
                "method": request.method,
                "url": request.url,
                "resource_type": request.resource_type,
                "status": None,
                "ok": None,
                "failure": None,
            }
            self._network_log.append(entry)
            if len(self._network_log) > MAX_NETWORK_LOG:
                self._network_log.pop(0)

        def _on_response(response):
            url = response.request.url
            for entry in reversed(self._network_log):
                if entry["url"] == url and entry["status"] is None:
                    entry["status"] = response.status
                    entry["ok"] = response.ok
                    break

        def _on_request_failed(request):
            url = request.url
            for entry in reversed(self._network_log):
                if entry["url"] == url and entry["status"] is None:
                    try:
                        entry["failure"] = request.failure
                    except Exception:
                        entry["failure"] = "unknown"
                    entry["ok"] = False
                    break

        self._page.on("request", _on_request)
        self._page.on("response", _on_response)
        self._page.on("requestfailed", _on_request_failed)
        self._network_listening = True
        logger.debug("📡 Network listeners installés")

    async def network_get_requests(self, url_filter: Optional[str] = None,
                                    resource_type: Optional[str] = None,
                                    limit: int = 50) -> Dict[str, Any]:
        """Retourne les requêtes réseau interceptées.

        Args:
            url_filter: Sous-chaîne pour filtrer les URLs
            resource_type: Filtrer par type (xhr, fetch, document, script, etc.)
            limit: Nombre max de résultats

        Returns:
            Dict avec la liste des requêtes.
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        self._setup_network_listeners()
        entries = self._network_log
        if url_filter:
            entries = [e for e in entries if url_filter.lower() in e["url"].lower()]
        if resource_type:
            entries = [e for e in entries if e["resource_type"] == resource_type]
        return {
            "success": True,
            "total_captured": len(self._network_log),
            "filtered_count": len(entries),
            "requests": entries[-limit:],
        }

    def network_clear(self) -> Dict[str, Any]:
        """Vide le buffer de requêtes réseau."""
        count = len(self._network_log)
        self._network_log.clear()
        return {"success": True, "cleared": count}

    # ── Phase 3 — Device Emulation ──

    async def emulate_device(self, device: str) -> Dict[str, Any]:
        """Émule un device mobile/tablette/desktop.

        Args:
            device: Nom du preset (iphone_14, pixel_7, ipad_pro, galaxy_s23,
                    desktop_1080p, desktop_1440p) ou 'WxH' (ex: '375x812')

        Returns:
            Dict avec les dimensions appliquées.
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}

        preset = DEVICE_PRESETS.get(device.lower().replace(" ", "_"))
        if preset:
            width, height = preset["width"], preset["height"]
            ua = preset["ua"]
        elif "x" in device:
            parts = device.lower().split("x")
            try:
                width, height = int(parts[0]), int(parts[1])
                ua = None
                preset = {"mobile": False, "scale": 1}
            except ValueError:
                return {"success": False, "error": f"Format invalide: {device}. Utilise 'WxH' ou un preset."}
        else:
            available = ", ".join(DEVICE_PRESETS.keys())
            return {"success": False, "error": f"Device inconnu: {device}. Disponibles: {available}"}

        try:
            await self._page.set_viewport_size({"width": width, "height": height})

            # CDP pour User-Agent + device metrics si mobile
            if preset.get("mobile") and ua:
                try:
                    session = await self._context.new_cdp_session(self._page)
                    await session.send("Emulation.setUserAgentOverride", {"userAgent": ua})
                    await session.send("Emulation.setDeviceMetricsOverride", {
                        "mobile": True,
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": preset.get("scale", 2),
                        "screenWidth": width,
                        "screenHeight": height,
                    })
                    await session.send("Emulation.setTouchEmulationEnabled", {"enabled": True})
                    await session.detach()
                except Exception as e:
                    logger.warning(f"CDP device emulation partiel: {e}")

            logger.info(f"📱 Device emulé: {device} ({width}x{height})")
            return {
                "success": True,
                "device": device,
                "viewport": {"width": width, "height": height},
                "mobile": preset.get("mobile", False),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def set_geolocation(self, latitude: float, longitude: float,
                               accuracy: float = 100) -> Dict[str, Any]:
        """Définit une géolocalisation simulée.

        Args:
            latitude: Latitude (-90 à 90)
            longitude: Longitude (-180 à 180)
            accuracy: Précision en mètres
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        try:
            await self._context.grant_permissions(["geolocation"])
            await self._context.set_geolocation({"latitude": latitude, "longitude": longitude, "accuracy": accuracy})
            return {"success": True, "latitude": latitude, "longitude": longitude}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def emulate_media(self, color_scheme: Optional[str] = None,
                             media: Optional[str] = None) -> Dict[str, Any]:
        """Émule les media queries CSS (dark mode, print, etc.).

        Args:
            color_scheme: 'dark', 'light', ou 'no-preference'
            media: 'screen' ou 'print'
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        try:
            kwargs = {}
            if color_scheme:
                kwargs["color_scheme"] = color_scheme
            if media:
                kwargs["media"] = media
            await self._page.emulate_media(**kwargs)
            return {"success": True, **kwargs}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Phase 3 — Cookies & Storage ──

    async def cookies_get(self, urls: Optional[List[str]] = None) -> Dict[str, Any]:
        """Retourne les cookies du contexte.

        Args:
            urls: Optionnel — liste d'URLs pour filtrer les cookies.
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        try:
            if urls:
                cookies = await self._context.cookies(urls)
            else:
                cookies = await self._context.cookies()
            return {"success": True, "count": len(cookies), "cookies": cookies}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def cookies_set(self, cookies: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ajoute des cookies au contexte.

        Args:
            cookies: Liste de dicts {name, value, url ou domain+path, ...}
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        try:
            await self._context.add_cookies(cookies)
            return {"success": True, "added": len(cookies)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def cookies_clear(self) -> Dict[str, Any]:
        """Supprime tous les cookies du contexte."""
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        try:
            await self._context.clear_cookies()
            return {"success": True, "message": "Tous les cookies supprimés"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def storage_get(self, kind: str = "local",
                           key: Optional[str] = None) -> Dict[str, Any]:
        """Lit le localStorage ou sessionStorage.

        Args:
            kind: 'local' ou 'session'
            key: Clé spécifique (optionnel — si None, retourne tout)
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        try:
            result = await self._page.evaluate("""({kind, key}) => {
                const store = kind === 'session' ? window.sessionStorage : window.localStorage;
                if (key) {
                    const v = store.getItem(key);
                    return v === null ? {} : {[key]: v};
                }
                const out = {};
                for (let i = 0; i < store.length; i++) {
                    const k = store.key(i);
                    out[k] = store.getItem(k);
                }
                return out;
            }""", {"kind": kind, "key": key})
            return {"success": True, "kind": kind, "data": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def storage_set(self, key: str, value: str,
                           kind: str = "local") -> Dict[str, Any]:
        """Écrit une valeur dans localStorage/sessionStorage.

        Args:
            key: Clé
            value: Valeur (string)
            kind: 'local' ou 'session'
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        try:
            await self._page.evaluate("""({kind, key, value}) => {
                const store = kind === 'session' ? window.sessionStorage : window.localStorage;
                store.setItem(key, value);
            }""", {"kind": kind, "key": key, "value": value})
            return {"success": True, "kind": kind, "key": key}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def storage_clear(self, kind: str = "local") -> Dict[str, Any]:
        """Vide le localStorage ou sessionStorage.

        Args:
            kind: 'local' ou 'session'
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        try:
            await self._page.evaluate("""({kind}) => {
                const store = kind === 'session' ? window.sessionStorage : window.localStorage;
                store.clear();
            }""", {"kind": kind})
            return {"success": True, "kind": kind, "message": f"{kind}Storage vidé"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Phase 3 — Batch Actions ──

    async def batch_actions(self, actions: List[Dict[str, Any]],
                             stop_on_error: bool = True) -> Dict[str, Any]:
        """Exécute plusieurs actions navigateur en séquence.

        Args:
            actions: Liste de dicts {action: "click|type|navigate|scroll|wait|evaluate|screenshot", ...params}
            stop_on_error: Arrêter à la première erreur (défaut: True)

        Returns:
            Dict avec les résultats de chaque action.
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        if len(actions) > MAX_BATCH_ACTIONS:
            return {"success": False, "error": f"Max {MAX_BATCH_ACTIONS} actions (reçu {len(actions)})"}

        results = []
        for i, act in enumerate(actions):
            kind = act.get("action", "").lower()
            try:
                if kind == "navigate":
                    r = await self.navigate(act["url"], act.get("wait_until", "domcontentloaded"))
                elif kind == "click":
                    r = await self.click(act.get("selector", ""), by=act.get("by", "css"))
                elif kind == "click_at":
                    r = await self.click_at(act["x"], act["y"])
                elif kind == "type":
                    r = await self.type_text(act.get("selector", ""), act.get("text", ""),
                                              by=act.get("by", "css"))
                elif kind == "scroll":
                    r = await self.scroll(act.get("direction", "down"), act.get("amount", 500))
                elif kind == "wait":
                    await self._page.wait_for_timeout(act.get("ms", 1000))
                    r = {"success": True, "waited_ms": act.get("ms", 1000)}
                elif kind == "evaluate":
                    val = await self._page.evaluate(act.get("expression", "null"))
                    r = {"success": True, "result": val}
                elif kind == "screenshot":
                    r = await self.screenshot(full_page=act.get("full_page", False))
                elif kind == "keyboard":
                    r = await self.keyboard_press(act.get("key", "Enter"))
                elif kind == "hover":
                    r = await self.hover(act.get("selector", ""), by=act.get("by", "css"))
                elif kind == "select":
                    r = await self.select_option(act.get("selector", ""), act.get("value", ""),
                                                  by=act.get("by", "css"))
                else:
                    r = {"success": False, "error": f"Action inconnue: {kind}"}

                results.append({"index": i, "action": kind, **r})

                if not r.get("success", True) and stop_on_error:
                    break
            except Exception as e:
                results.append({"index": i, "action": kind, "success": False, "error": str(e)})
                if stop_on_error:
                    break

        succeeded = sum(1 for r in results if r.get("success"))
        return {
            "success": succeeded == len(results),
            "total": len(actions),
            "executed": len(results),
            "succeeded": succeeded,
            "results": results,
        }

    # ── Phase 3 — Screenshot avec labels ──

    async def screenshot_with_labels(self, max_labels: int = 80) -> Dict[str, Any]:
        """Prend un screenshot avec overlay des éléments interactifs labelisés.

        Injecte des labels visuels [1], [2]... sur les éléments cliquables/interactifs
        visibles dans le viewport, puis prend un screenshot.

        Args:
            max_labels: Nombre maximum de labels à afficher

        Returns:
            Dict avec le path du screenshot + la map des labels.
        """
        if not self.is_running:
            return {"success": False, "error": "Navigateur non démarré"}
        try:
            # 1. Extraire les éléments interactifs visibles
            elements = await self._page.evaluate("""(maxLabels) => {
                const selectors = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="tab"], [onclick], [tabindex]';
                const els = document.querySelectorAll(selectors);
                const results = [];
                const vw = window.innerWidth, vh = window.innerHeight;
                for (const el of els) {
                    if (results.length >= maxLabels) break;
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 5 || rect.height < 5) continue;
                    if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                    results.push({
                        tag: el.tagName.toLowerCase(),
                        text: (el.textContent || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim().slice(0, 40),
                        type: el.getAttribute('type') || '',
                        x: Math.round(rect.left), y: Math.round(rect.top),
                        w: Math.round(rect.width), h: Math.round(rect.height),
                    });
                }
                return results;
            }""", max_labels)

            # 2. Injecter les labels visuels sur la page
            await self._page.evaluate("""(elements) => {
                document.querySelectorAll('[data-lumena-label]').forEach(el => el.remove());
                const root = document.createElement('div');
                root.setAttribute('data-lumena-label', 'root');
                root.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:2147483647;';
                elements.forEach((el, i) => {
                    const label = document.createElement('div');
                    label.setAttribute('data-lumena-label', String(i + 1));
                    label.textContent = String(i + 1);
                    label.style.cssText = `position:absolute;left:${el.x}px;top:${el.y}px;background:#e11d48;color:#fff;font:bold 11px/1 monospace;padding:1px 3px;border-radius:3px;min-width:14px;text-align:center;`;
                    root.appendChild(label);
                });
                document.body.appendChild(root);
            }""", elements)

            # 3. Screenshot
            await self._page.wait_for_timeout(100)  # laisser le DOM se mettre à jour
            result = await self.screenshot(full_page=False)

            # 4. Nettoyer les labels
            await self._page.evaluate("document.querySelectorAll('[data-lumena-label]').forEach(el => el.remove())")

            if not result.get("success"):
                return result

            # 5. Construire la map des labels
            label_map = []
            for i, el in enumerate(elements):
                label_map.append({
                    "label": i + 1,
                    "tag": el["tag"],
                    "text": el["text"],
                    "type": el.get("type", ""),
                })

            return {
                "success": True,
                "path": result["path"],
                "labels_count": len(label_map),
                "labels": label_map,
            }
        except Exception as e:
            # Nettoyer en cas d'erreur
            try:
                await self._page.evaluate("document.querySelectorAll('[data-lumena-label]').forEach(el => el.remove())")
            except Exception:
                pass
            return {"success": False, "error": str(e)}


# Singleton global
_playwright_browser: Optional[PlaywrightBrowser] = None


def get_playwright_browser(headless: bool | None = None, profile_name: Optional[str] = "lumena") -> PlaywrightBrowser:
    """Retourne l'instance singleton du navigateur Playwright.
    
    headless: None = lire depuis env LUMENA_BROWSER_HEADLESS (défaut: False = visible).
    """
    global _playwright_browser
    if headless is None:
        import os
        headless = os.getenv("LUMENA_BROWSER_HEADLESS", "false").lower() in ("1", "true", "yes")
    if _playwright_browser is None:
        _playwright_browser = PlaywrightBrowser(headless=headless, profile_name=profile_name)
    elif _playwright_browser.headless != headless and not _playwright_browser.is_running:
        # Si pas encore démarré, on peut changer le mode
        _playwright_browser.headless = headless
    return _playwright_browser


async def close_playwright_browser():
    """Ferme le navigateur Playwright."""
    global _playwright_browser
    if _playwright_browser is not None:
        await _playwright_browser.stop()
        _playwright_browser = None
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
