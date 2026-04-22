"""
🌐 LUMENA - Contrôle du Navigateur
Permet de contrôler le navigateur comme un humain

Actions disponibles:
- open_browser: Ouvre le navigateur
- navigate: Va à une URL
- search_google: Fait une recherche Google
- get_page_content: Récupère le contenu d'une page
- click: Clique sur un élément
- type_text: Tape du texte
- screenshot: Prend une capture
- close_browser: Ferme le navigateur
"""

import asyncio
from typing import Optional, Dict, Any, List
from pathlib import Path
from loguru import logger
import re
import threading
import time

# Essayer d'importer Selenium
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    logger.warning("Selenium non installé. pip install selenium webdriver-manager")

# Essayer webdriver-manager pour l'installation automatique
try:
    from webdriver_manager.chrome import ChromeDriverManager
    WEBDRIVER_MANAGER_AVAILABLE = True
except ImportError:
    WEBDRIVER_MANAGER_AVAILABLE = False


class LumenaBrowser:
    """
    🌐 Contrôle du navigateur pour Lumena
    
    Permet de:
    - Naviguer sur le web
    - Faire des recherches Google
    - Scraper des pages
    - Interagir avec les éléments (clic, texte)
    - Prendre des captures d'écran
    """
    
    def __init__(self, headless: bool = False):
        self.driver: Optional[webdriver.Chrome] = None
        self.headless = headless
        from src.utils.paths import SCREENSHOTS_DIR
        self.screenshots_dir = SCREENSHOTS_DIR
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        
    def is_available(self) -> bool:
        """Vérifie si Selenium est disponible."""
        return SELENIUM_AVAILABLE
    
    def start(self) -> bool:
        """Démarre le navigateur Chrome."""
        if not SELENIUM_AVAILABLE:
            logger.error("Selenium non disponible")
            return False
        
        if self.driver:
            logger.info("Navigateur déjà démarré")
            return True
        
        try:
            options = Options()
            
            if self.headless:
                options.add_argument("--headless=new")
            
            # Options communes
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # User agent réaliste
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            
            # Utiliser webdriver-manager si disponible
            if WEBDRIVER_MANAGER_AVAILABLE:
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=options)
            else:
                self.driver = webdriver.Chrome(options=options)
            
            # Masquer le flag webdriver
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            logger.info("🌐 Navigateur Chrome démarré")
            return True
            
        except Exception as e:
            logger.error(f"Erreur démarrage navigateur: {e}")
            return False
    
    def stop(self):
        """Ferme le navigateur."""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("🌐 Navigateur fermé")
            except Exception as e:
                logger.warning(f"Erreur fermeture: {e}")
            finally:
                self.driver = None
    
    def navigate(self, url: str) -> Dict[str, Any]:
        """Navigue vers une URL."""
        if not self.driver:
            if not self.start():
                return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            self.driver.get(url)
            time.sleep(1)  # Attendre le chargement
            
            return {
                "success": True,
                "url": self.driver.current_url,
                "title": self.driver.title
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def search_google(self, query: str) -> Dict[str, Any]:
        """
        Fait une recherche Google et retourne les résultats.
        Utilise DuckDuckGo en fallback car plus tolérant aux bots.
        """
        if not self.driver:
            if not self.start():
                return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            results = []
            
            # Essayer d'abord DuckDuckGo (plus tolérant aux bots)
            try:
                ddg_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
                self.driver.get(ddg_url)
                time.sleep(2)
                
                # Extraire les résultats DuckDuckGo
                result_divs = self.driver.find_elements(By.CSS_SELECTOR, ".result")
                
                for i, div in enumerate(result_divs[:10]):
                    try:
                        title_elem = div.find_element(By.CSS_SELECTOR, ".result__title a")
                        snippet_elem = div.find_element(By.CSS_SELECTOR, ".result__snippet")
                        
                        url = title_elem.get_attribute("href")
                        # DuckDuckGo utilise des redirects, extraire l'URL réelle
                        if "uddg=" in url:
                            import urllib.parse
                            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                            url = parsed.get("uddg", [url])[0]
                        
                        results.append({
                            "position": i + 1,
                            "title": title_elem.text,
                            "url": url,
                            "description": snippet_elem.text
                        })
                    except (NoSuchElementException, Exception):
                        continue
                
                if results:
                    return {
                        "success": True,
                        "query": query,
                        "source": "DuckDuckGo",
                        "results_count": len(results),
                        "results": results
                    }
            except Exception as ddg_err:
                logger.warning(f"DuckDuckGo failed: {ddg_err}")
            
            # Fallback: Google avec attente plus longue
            try:
                self.driver.get("https://www.google.com")
                time.sleep(2)
                
                # Accepter les cookies si nécessaire
                try:
                    # Essayer plusieurs sélecteurs pour le bouton d'acceptation
                    for selector in ["#L2AGLb", "[aria-label*='Accept']", "button[id*='accept']"]:
                        try:
                            accept_btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                            accept_btn.click()
                            time.sleep(1)
                            break
                        except NoSuchElementException:
                            continue
                except Exception:
                    pass  # Cookie banner handling - non-critical
                
                # Trouver la barre de recherche
                search_box = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.NAME, "q"))
                )
                search_box.clear()
                search_box.send_keys(query)
                search_box.send_keys(Keys.RETURN)
                
                time.sleep(3)  # Attendre les résultats
                
                # Essayer plusieurs sélecteurs pour les résultats Google
                selectors = [
                    "div.g",
                    "div[data-sokoban-container]",
                    "div.tF2Cxc",
                    "[data-header-feature] h3"
                ]
                
                for selector in selectors:
                    try:
                        result_divs = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        if result_divs:
                            break
                    except NoSuchElementException:
                        continue
                
                for i, div in enumerate(result_divs[:10]):
                    try:
                        # Essayer différents sélecteurs pour le titre
                        title_elem = None
                        for title_sel in ["h3", "a h3", "[role='heading']"]:
                            try:
                                title_elem = div.find_element(By.CSS_SELECTOR, title_sel)
                                break
                            except NoSuchElementException:
                                continue
                        
                        if not title_elem:
                            continue
                        
                        # Trouver le lien
                        try:
                            link_elem = div.find_element(By.CSS_SELECTOR, "a")
                            url = link_elem.get_attribute("href")
                        except NoSuchElementException:
                            url = ""
                        
                        # Trouver la description
                        description = ""
                        for desc_sel in [".VwiC3b", "[data-content-feature]", ".IsZvec"]:
                            try:
                                desc_elem = div.find_element(By.CSS_SELECTOR, desc_sel)
                                description = desc_elem.text
                                break
                            except NoSuchElementException:
                                continue
                        
                        if title_elem.text and url:
                            results.append({
                                "position": i + 1,
                                "title": title_elem.text,
                                "url": url,
                                "description": description
                            })
                    except (NoSuchElementException, Exception):
                        continue
                
                if results:
                    return {
                        "success": True,
                        "query": query,
                        "source": "Google",
                        "results_count": len(results),
                        "results": results
                    }
                        
            except TimeoutException:
                pass  # élément non trouvé dans le timeout, on continue
            
            # Dernier fallback: utiliser l'API HTTP directe
            if not results:
                try:
                    import urllib.request
                    import urllib.parse
                    
                    # Utiliser DuckDuckGo Instant Answer API
                    api_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1"
                    req = urllib.request.Request(api_url, headers={
                        'User-Agent': 'LumenaBot/1.0'
                    })
                    with urllib.request.urlopen(req, timeout=10) as response:
                        import json
                        data = json.loads(response.read().decode('utf-8'))
                    
                    # Extraire les résultats
                    if data.get("AbstractText"):
                        results.append({
                            "position": 1,
                            "title": data.get("Heading", query),
                            "url": data.get("AbstractURL", ""),
                            "description": data.get("AbstractText", "")
                        })
                    
                    for i, related in enumerate(data.get("RelatedTopics", [])[:5]):
                        if isinstance(related, dict) and related.get("Text"):
                            results.append({
                                "position": len(results) + 1,
                                "title": related.get("Text", "")[:100],
                                "url": related.get("FirstURL", ""),
                                "description": related.get("Text", "")
                            })
                except Exception as api_err:
                    logger.warning(f"API fallback failed: {api_err}")
            
            return {
                "success": True,
                "query": query,
                "source": "Mixed",
                "results_count": len(results),
                "results": results
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_page_content(self, url: Optional[str] = None) -> Dict[str, Any]:
        """
        Récupère le contenu textuel d'une page.
        """
        if not self.driver:
            if not self.start():
                return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            if url:
                self.navigate(url)

            try:
                self.accept_cookies()
            except Exception as e:
                logger.debug(f"Accept cookies: {e}")
            time.sleep(0.5)
            
            # Extraire le texte principal
            text_content = self._extract_readable_content()
            if not text_content:
                body = self.driver.find_element(By.TAG_NAME, "body")
                text_content = body.text if body else ""

            text_content = self._strip_cookie_noise(text_content)
            
            # Limiter la taille
            if len(text_content) > 10000:
                text_content = text_content[:10000] + "..."
            
            return {
                "success": True,
                "url": self.driver.current_url,
                "title": self.driver.title,
                "content": text_content,
                "content_length": len(text_content)
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def click_element(self, selector: str, by: str = "css") -> Dict[str, Any]:
        """
        Clique sur un élément.
        
        by: "css", "xpath", "id", "class", "text"
        """
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            selector = (selector or "").strip()
            if not selector:
                return {"success": False, "error": "Sélecteur vide"}

            by_key = (by or "css").strip().lower()

            if by_key in {"text", "partial_text"}:
                text_literal = self._xpath_text_literal(selector)
                if by_key == "text":
                    xpath = (
                        "//*[self::button or self::a or self::span or self::div or @role='button']"
                        f"[normalize-space(.)={text_literal}]"
                    )
                else:
                    xpath = (
                        "//*[self::button or self::a or self::span or self::div or @role='button']"
                        f"[contains(normalize-space(.), {text_literal})]"
                    )
                by_method = By.XPATH
                effective_selector = xpath
            else:
                by_map = {
                    "css": By.CSS_SELECTOR,
                    "xpath": By.XPATH,
                    "id": By.ID,
                    "class": By.CLASS_NAME,
                    "name": By.NAME,
                }
                by_method = by_map.get(by_key, By.CSS_SELECTOR)
                effective_selector = selector

            element = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((by_method, effective_selector))
            )

            try:
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                    element,
                )
            except Exception as e:
                logger.debug(f"Scroll to element: {e}")

            try:
                element.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", element)
            
            time.sleep(0.5)
            
            return {
                "success": True,
                "clicked": selector,
                "new_url": self.driver.current_url
            }
            
        except TimeoutException:
            return {"success": False, "error": f"Élément non trouvé: {selector}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _xpath_text_literal(value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ", \"'\", ".join([f"'{p}'" for p in parts]) + ")"

    def click_at(self, x: int, y: int) -> Dict[str, Any]:
        """Clique à des coordonnées (simulation souris)."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}

        try:
            viewport = self.driver.execute_script(
                "return {w: window.innerWidth || 1920, h: window.innerHeight || 1080};"
            )
            width = int(viewport.get("w", 1920))
            height = int(viewport.get("h", 1080))

            x = max(0, min(int(x), max(0, width - 1)))
            y = max(0, min(int(y), max(0, height - 1)))

            body = self.driver.find_element(By.TAG_NAME, "body")
            actions = ActionChains(self.driver)
            actions.move_to_element_with_offset(body, x, y).click().perform()
            time.sleep(0.3)

            return {
                "success": True,
                "clicked_at": {"x": x, "y": y},
                "viewport": {"w": width, "h": height},
                "new_url": self.driver.current_url,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def accept_cookies(self) -> Dict[str, Any]:
        """Tente d'accepter automatiquement les bandeaux cookies les plus courants."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}

        try:
            css_candidates = [
                "#onetrust-accept-btn-handler",
                "button[aria-label*='Accept']",
                "button[aria-label*='Accepter']",
                "button[id*='accept']",
                "button[class*='accept']",
                "button[id*='consent']",
                "button[class*='consent']",
            ]

            for selector in css_candidates:
                result = self.click_element(selector, by="css")
                if result.get("success"):
                    return {
                        "success": True,
                        "method": "css",
                        "selector": selector,
                        "new_url": self.driver.current_url,
                    }

            text_candidates = [
                "Tout accepter",
                "Accepter",
                "Accepter et fermer",
                "J'accepte",
                "Accept all",
                "Accept",
                "I agree",
                "Agree",
                "Consent",
                "Continuer sans accepter",
            ]

            for text in text_candidates:
                for mode in ("text", "partial_text"):
                    result = self.click_element(text, by=mode)
                    if result.get("success"):
                        return {
                            "success": True,
                            "method": mode,
                            "selector": text,
                            "new_url": self.driver.current_url,
                        }

            return {
                "success": False,
                "error": "Aucun bouton cookie reconnu",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def type_text(self, selector: str, text: str, by: str = "css", clear: bool = True) -> Dict[str, Any]:
        """
        Tape du texte dans un champ.
        """
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            by_map = {
                "css": By.CSS_SELECTOR,
                "xpath": By.XPATH,
                "id": By.ID,
                "class": By.CLASS_NAME,
                "name": By.NAME
            }
            
            by_method = by_map.get(by, By.CSS_SELECTOR)
            
            element = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((by_method, selector))
            )
            
            if clear:
                element.clear()
            
            element.send_keys(text)
            
            return {
                "success": True,
                "typed_in": selector,
                "text_length": len(text)
            }
            
        except TimeoutException:
            return {"success": False, "error": f"Élément non trouvé: {selector}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def press_enter(self, selector: Optional[str] = None) -> Dict[str, Any]:
        """Appuie sur Entrée."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            if selector:
                element = self.driver.find_element(By.CSS_SELECTOR, selector)
                element.send_keys(Keys.RETURN)
            else:
                # Envoyer à l'élément actif
                active = self.driver.switch_to.active_element
                active.send_keys(Keys.RETURN)
            
            time.sleep(1)
            return {"success": True}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def screenshot(self, filename: Optional[str] = None) -> Dict[str, Any]:
        """Prend une capture d'écran."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            if not filename:
                filename = f"screenshot_{int(time.time())}.png"
            
            filepath = self.screenshots_dir / filename
            self.driver.save_screenshot(str(filepath))
            
            return {
                "success": True,
                "path": str(filepath),
                "url": self.driver.current_url
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def scroll(self, direction: str = "down", amount: int = 500) -> Dict[str, Any]:
        """Scrolle la page."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            if direction == "down":
                self.driver.execute_script(f"window.scrollBy(0, {amount})")
            elif direction == "up":
                self.driver.execute_script(f"window.scrollBy(0, -{amount})")
            elif direction == "top":
                self.driver.execute_script("window.scrollTo(0, 0)")
            elif direction == "bottom":
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
            
            return {"success": True, "scrolled": direction}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_tabs(self) -> Dict[str, Any]:
        """Liste les onglets ouverts."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            handles = self.driver.window_handles
            current = self.driver.current_window_handle
            
            tabs = []
            for i, handle in enumerate(handles):
                self.driver.switch_to.window(handle)
                tabs.append({
                    "index": i,
                    "handle": handle,
                    "title": self.driver.title,
                    "url": self.driver.current_url,
                    "active": handle == current
                })
            
            # Revenir à l'onglet actif
            self.driver.switch_to.window(current)
            
            return {
                "success": True,
                "count": len(tabs),
                "tabs": tabs
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def new_tab(self, url: Optional[str] = None) -> Dict[str, Any]:
        """Ouvre un nouvel onglet."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            self.driver.switch_to.new_window('tab')
            
            if url:
                self.driver.get(url)
            
            return {
                "success": True,
                "url": self.driver.current_url,
                "handle": self.driver.current_window_handle
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def close_tab(self) -> Dict[str, Any]:
        """Ferme l'onglet actuel."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            handles = self.driver.window_handles
            
            if len(handles) <= 1:
                return {"success": False, "error": "Dernier onglet, utilisez close_browser"}
            
            self.driver.close()
            self.driver.switch_to.window(handles[0])
            
            return {"success": True}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def switch_tab(self, index: int) -> Dict[str, Any]:
        """Change d'onglet par index."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            handles = self.driver.window_handles
            
            if index < 0 or index >= len(handles):
                return {"success": False, "error": f"Index invalide (max: {len(handles)-1})"}
            
            self.driver.switch_to.window(handles[index])
            
            return {
                "success": True,
                "url": self.driver.current_url,
                "title": self.driver.title
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def go_back(self) -> Dict[str, Any]:
        """Retourne à la page précédente."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            self.driver.back()
            time.sleep(1)
            return {
                "success": True,
                "url": self.driver.current_url
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def go_forward(self) -> Dict[str, Any]:
        """Avance à la page suivante."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            self.driver.forward()
            time.sleep(1)
            return {
                "success": True,
                "url": self.driver.current_url
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def refresh(self) -> Dict[str, Any]:
        """Rafraîchit la page."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            self.driver.refresh()
            time.sleep(1)
            return {
                "success": True,
                "url": self.driver.current_url
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def deep_research(self, query: str, max_pages: int = 10) -> Dict[str, Any]:
        """
        🔬 Recherche approfondie
        
        1. Fait une recherche
        2. Ouvre les résultats pertinents dans des onglets séparés
        3. Analyse le contenu de chaque page
        4. Synthétise les meilleures informations
        
        Args:
            query: La requête de recherche
            max_pages: Nombre maximum de pages à analyser (défaut: 5)
        """
        if not self.driver:
            if not self.start():
                return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            # Enrichir la requête avec la date si nécessaire (prix, tarifs, horaires, événements)
            from datetime import datetime
            current_year = datetime.now().year
            current_date = datetime.now().strftime("%Y")
            
            # Mots-clés qui nécessitent une date récente
            date_keywords = ["prix", "tarif", "coût", "billet", "place", "horaire", "ouverture", 
                             "événement", "concert", "spectacle", "réservation", "achat"]
            
            query_lower = query.lower()
            needs_date = any(kw in query_lower for kw in date_keywords)
            
            # Ajouter l'année si pas déjà présente et si pertinent
            if needs_date and str(current_year) not in query:
                enriched_query = f"{query} {current_year}"
                logger.info(f"🗓️ Recherche enrichie avec date: {enriched_query}")
            else:
                enriched_query = query
            
            # Étape 1: Faire la recherche
            search_result = self.search_google(enriched_query)
            if not search_result["success"] or not search_result.get("results"):
                return {"success": False, "error": "Aucun résultat de recherche"}
            
            results = search_result["results"][:max_pages]
            original_handle = self.driver.current_window_handle
            
            # Étape 2: Ouvrir chaque résultat dans un nouvel onglet
            page_contents = []
            opened_tabs = []
            
            for i, result in enumerate(results):
                url = result.get("url", "")
                if not url or not url.startswith("http"):
                    continue
                
                try:
                    # Ouvrir nouvel onglet
                    self.driver.switch_to.new_window('tab')
                    opened_tabs.append(self.driver.current_window_handle)
                    
                    # Naviguer vers l'URL
                    self.driver.get(url)
                    time.sleep(2)  # Attendre le chargement

                    # Tenter d'accepter les cookies avant extraction
                    try:
                        self.accept_cookies()
                    except Exception as e:
                        logger.debug(f"Accept cookies: {e}")
                    
                    # Extraire le contenu
                    content = self._extract_readable_content()
                    
                    if content:
                        page_contents.append({
                            "position": i + 1,
                            "title": result.get("title", self.driver.title),
                            "url": url,
                            "content": content[:3000],  # Limiter la taille
                            "content_length": len(content)
                        })
                        
                except Exception as page_err:
                    logger.warning(f"Erreur page {url}: {page_err}")
                    continue
            
            # Étape 3: Revenir à l'onglet principal
            self.driver.switch_to.window(original_handle)
            
            # Étape 4: Préparer le résumé synthétisé
            synthesis = self._synthesize_content(query, page_contents)
            
            return {
                "success": True,
                "query": query,
                "pages_analyzed": len(page_contents),
                "tabs_opened": len(opened_tabs),
                "sources": [
                    {"title": p["title"], "url": p["url"]} 
                    for p in page_contents
                ],
                "synthesis": synthesis,
                "raw_contents": page_contents
            }
            
        except Exception as e:
            logger.error(f"Erreur deep_research: {e}")
            return {"success": False, "error": str(e)}
    
    def _extract_readable_content(self) -> str:
        """
        Extrait le contenu lisible d'une page.
        Supprime les scripts, styles, et garde le texte principal.
        """
        try:
            # Supprimer les éléments non pertinents et extraire le texte
            script = """
            try {
                // Supprimer les éléments non désirés
                const remove = document.querySelectorAll('script, style, noscript, iframe, nav, footer, header, aside, .advertisement, .ads, [class*="cookie"], [class*="popup"], [class*="banner"]');
                remove.forEach(el => { try { el.remove(); } catch(e) {} });
                
                // Essayer de trouver le contenu principal
                const selectors = ['main', 'article', '[role="main"]', '.content', '#content', '.post-content', '.article-body', '.entry-content', '.page-content'];
                for (const sel of selectors) {
                    const main = document.querySelector(sel);
                    if (main && main.innerText && main.innerText.length > 200) {
                        return main.innerText;
                    }
                }
                
                // Fallback: contenu du body
                if (document.body && document.body.innerText) {
                    return document.body.innerText;
                }
                
                return '';
            } catch(e) {
                return document.body ? document.body.innerText || '' : '';
            }
            """
            
            content = self.driver.execute_script(script)
            
            # Nettoyer le contenu
            if content:
                content = re.sub(r'\n{3,}', '\n\n', content)
                content = re.sub(r'[ \t]+', ' ', content)
                content = content.strip()
                content = self._strip_cookie_noise(content)
            
            return content or ""
            
        except Exception as e:
            # Fallback simple sans JavaScript
            try:
                body = self.driver.find_element(By.TAG_NAME, "body")
                text = body.text if body else ""
                text = self._strip_cookie_noise(text)
                return text[:5000] if text else ""
            except Exception:
                return ""

    @staticmethod
    def _strip_cookie_noise(text: str) -> str:
        if not text:
            return ""

        markers = [
            "continuer sans accepter",
            "avec votre accord",
            "utilisent des cookies",
            "technologies similaires",
            "politique de confidentialité",
            "politique de confidentialit",
            "personnaliser",
            "cookie",
            "consent",
            "gdpr",
            "cmp",
        ]

        lines = [line.strip() for line in text.splitlines()]
        filtered: List[str] = []
        for line in lines:
            if not line:
                if filtered and filtered[-1] != "":
                    filtered.append("")
                continue

            lowered = line.lower()
            if any(marker in lowered for marker in markers):
                continue
            filtered.append(line)

        cleaned = "\n".join(filtered)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()
    
    def _synthesize_content(self, query: str, page_contents: List[Dict[str, Any]]) -> str:
        """
        Synthétise le contenu de plusieurs pages en un résumé cohérent.
        Extrait automatiquement les informations clés (prix, dates, etc.)
        """
        import re
        
        if not page_contents:
            return "Aucun contenu à synthétiser."
        
        # Regex pour extraire les informations importantes
        price_patterns = [
            r'(\d+[\s,.]?\d*\s*[€$£])',  # 13 €, 13.50€
            r'([€$£]\s*\d+[\s,.]?\d*)',  # €13, € 13.50
            r'(\d+[\s,.]?\d*\s*euros?)',  # 13 euros
            r'(tarif\s*:?\s*\d+[\s,.]?\d*)',  # tarif: 13
            r'(prix\s*:?\s*\d+[\s,.]?\d*)',  # prix: 13
            r'(gratuit)',  # gratuit
            r'(entrée\s+libre)',  # entrée libre
        ]
        
        # Construire un résumé structuré
        synthesis = f"# Résultats de recherche: {query}\n\n"
        synthesis += f"📊 {len(page_contents)} sources analysées\n\n"
        
        # Collecter toutes les informations de prix trouvées
        all_prices = []
        
        for page in page_contents:
            synthesis += f"## 📄 {page['title']}\n"
            synthesis += f"🔗 {page['url']}\n\n"
            
            content = page.get("content", "")
            
            # Extraire les prix de cette page
            page_prices = []
            for pattern in price_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                page_prices.extend(matches)
            
            if page_prices:
                unique_prices = list(set(page_prices))[:5]  # Limiter à 5 prix
                synthesis += f"💰 **Prix trouvés**: {', '.join(unique_prices)}\n\n"
                all_prices.extend(unique_prices)
            
            # Extraire les paragraphes pertinents
            paragraphs = [p.strip() for p in content.split('\n\n') if len(p.strip()) > 50]
            
            # Chercher les paragraphes qui contiennent des mots clés importants
            keywords = ["prix", "tarif", "€", "euro", "billet", "gratuit", "horaire", 
                        "ouvert", "fermé", "réservation", "achat", "visite"]
            
            relevant_paragraphs = []
            for p in paragraphs[:10]:  # Chercher dans les 10 premiers
                if any(kw in p.lower() for kw in keywords):
                    relevant_paragraphs.append(p)
            
            # Si pas de paragraphes pertinents, prendre les premiers
            if not relevant_paragraphs:
                relevant_paragraphs = paragraphs[:2]
            
            # Garder les 2 plus pertinents
            key_info = "\n\n".join(relevant_paragraphs[:2])
            if key_info:
                synthesis += f"{key_info[:600]}...\n\n"
            
            synthesis += "---\n\n"
        
        # Résumé des prix en haut
        if all_prices:
            unique_all = list(set(all_prices))
            synthesis = f"💰 **RÉSUMÉ PRIX**: {', '.join(unique_all[:8])}\n\n" + synthesis
        
        return synthesis
    
    def close_all_tabs_except_main(self) -> Dict[str, Any]:
        """Ferme tous les onglets sauf le principal."""
        if not self.driver:
            return {"success": False, "error": "Navigateur non démarré"}
        
        try:
            handles = self.driver.window_handles
            main_handle = handles[0]
            
            for handle in handles[1:]:
                self.driver.switch_to.window(handle)
                self.driver.close()
            
            self.driver.switch_to.window(main_handle)
            
            return {"success": True, "closed_tabs": len(handles) - 1}
        except Exception as e:
            return {"success": False, "error": str(e)}


# Instance singleton
_browser_instance: Optional[LumenaBrowser] = None
_browser_lock = threading.Lock()

def get_browser(headless: bool = False) -> LumenaBrowser:
    """Retourne l'instance singleton du navigateur."""
    global _browser_instance
    if _browser_instance is None:
        with _browser_lock:
            if _browser_instance is None:
                _browser_instance = LumenaBrowser(headless=headless)
    return _browser_instance
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
