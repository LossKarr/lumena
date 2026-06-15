"""
dom_indexer.py — DOM Accessibility Index + Set-of-Mark overlay.

Phase 2.2 : Indexe les elements interactifs du DOM via l'arbre d'accessibilite
Playwright, et produit un overlay Set-of-Mark sur le screenshot du navigateur.

Classes:
    DOMElement  — un element interactif indexe (bouton, lien, input, select, etc.)
    DOMSnapshot — snapshot complet d'une page : liste d'elements + texte compact
    DOMIndexer  — extraie le snapshot depuis un Page Playwright

Fonctions:
    render_set_of_mark(screenshot, elements) -> Image PIL avec labels [1], [2], ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

from loguru import logger

# PIL est optionnel (uniquement pour Set-of-Mark overlay)
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ─── Data classes ──────────────────────────────────────────────────────────

# Roles consideres comme interactifs pour l'indexation
INTERACTIVE_ROLES = frozenset({
    "button",
    "link",
    "textbox",
    "searchbox",
    "combobox",
    "checkbox",
    "radio",
    "switch",
    "slider",
    "spinbutton",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "tab",
    "option",
    "treeitem",
})

# Roles de conteneur a ignorer (pas interactifs en soi)
CONTAINER_ROLES = frozenset({
    "generic",
    "none",
    "presentation",
    "group",
    "list",
    "listitem",
    "navigation",
    "banner",
    "complementary",
    "contentinfo",
    "main",
    "region",
    "article",
    "dialog",
    "grid",
    "row",
    "rowgroup",
    "table",
    "toolbar",
    "menu",
    "menubar",
    "tree",
    "tablist",
    "tabpanel",
    "separator",
    "figure",
    "form",
    "status",
    "alert",
    "log",
    "marquee",
    "timer",
    "math",
    "directory",
    "document",
    "application",
    "cell",
    "columnheader",
    "rowheader",
    "gridcell",
    "definition",
    "term",
    "note",
    "paragraph",
    "blockquote",
    "caption",
    "legend",
    "strong",
    "emphasis",
    "deletion",
    "insertion",
    "subscript",
    "superscript",
    "code",
    "time",
    "img",
    "heading",
    "text",
    "StaticText",
    "InlineTextBox",
    "LineBreak",
    "RootWebArea",
    "WebArea",
    "",
})

# Nombre max d'elements indexes (pour ne pas surcharger le LLM)
MAX_ELEMENTS = 100


@dataclass
class DOMElement:
    """Un element interactif indexe du DOM."""

    index: int
    role: str
    name: str
    tag: str = ""
    value: str = ""
    description: str = ""
    options: List[str] = field(default_factory=list)  # pour <select>
    checked: Optional[bool] = None  # pour checkbox/radio
    disabled: bool = False
    # Bounding box viewport (pixels)
    bbox: Optional[Tuple[float, float, float, float]] = None  # (x, y, w, h)

    @property
    def center(self) -> Optional[Tuple[int, int]]:
        """Retourne le centre de l'element (pour clic)."""
        if self.bbox is None:
            return None
        x, y, w, h = self.bbox
        return (int(x + w / 2), int(y + h / 2))

    def to_text(self) -> str:
        """Formatte l'element comme texte compact pour le LLM."""
        parts = [f"[{self.index}]", self.role]

        label = self.name or self.description
        if label:
            parts.append(f'"{label}"')

        if self.value:
            parts.append(f'value="{self.value}"')

        if self.options:
            opts = ", ".join(f'"{o}"' for o in self.options[:5])
            if len(self.options) > 5:
                opts += f", ... (+{len(self.options) - 5})"
            parts.append(f"options=[{opts}]")

        if self.checked is not None:
            parts.append("checked" if self.checked else "unchecked")

        if self.disabled:
            parts.append("(disabled)")

        return " ".join(parts)


# ─── Fix 6: Profils DOM par taille de contexte modèle ─────────────────────────

# Profils calibrés sur le catalogue Lumena (qwen3-8b → Grok 4.20 2M)
_DOM_PROFILES: Dict[str, Dict[str, Any]] = {
    "tiny":   {"max_elements": 25, "max_label": 30},   # Ollama <16K
    "small":  {"max_elements": 40, "max_label": 50},   # 16K-64K
    "medium": {"max_elements": 60, "max_label": 80},   # 64K-150K (DeepSeek, GPT-4o)
    "large":  {"max_elements": 80, "max_label": 120},  # 150K-500K (Claude, o3, Kimi)
    "xlarge": {"max_elements": 100, "max_label": 200}, # 500K-1M (GPT-5.4, Gemini)
    "ultra":  {"max_elements": 150, "max_label": 300}, # >1M (Grok 4.20 2M)
}


def get_dom_profile_for_context(max_context_window: int) -> Dict[str, Any]:
    """Retourne le profil DOM adapté à la fenêtre de contexte du modèle actif.

    Args:
        max_context_window: Fenêtre de contexte en tokens (0 = fallback medium).

    Returns:
        Dict avec max_elements et max_label.
    """
    if max_context_window <= 0:
        return _DOM_PROFILES["medium"]
    if max_context_window < 16_000:
        return _DOM_PROFILES["tiny"]
    if max_context_window < 64_000:
        return _DOM_PROFILES["small"]
    if max_context_window < 150_000:
        return _DOM_PROFILES["medium"]
    if max_context_window < 500_000:
        return _DOM_PROFILES["large"]
    if max_context_window < 1_500_000:
        return _DOM_PROFILES["xlarge"]
    return _DOM_PROFILES["ultra"]


@dataclass
class DOMSnapshot:
    """Snapshot complet de l'etat DOM d'une page."""

    url: str
    title: str
    elements: List[DOMElement]
    total_interactive: int  # nb total avant troncature
    truncated: bool = False

    def to_text(self, max_elements: Optional[int] = None, max_label: Optional[int] = None) -> str:
        """Formatte le snapshot complet comme texte pour le LLM.

        Args:
            max_elements: Nombre max d'éléments à afficher (None = tous).
            max_label: Longueur max des labels (None = illimité).
        """
        elements = self.elements
        if max_elements is not None and len(elements) > max_elements:
            elements = elements[:max_elements]
            truncated_note = f" (showing top {len(elements)} of {self.total_interactive})"
        elif self.truncated:
            truncated_note = f" (showing top {len(elements)})"
        else:
            truncated_note = ""

        lines = [
            f"Page: {self.title}",
            f"URL: {self.url}",
            f"Interactive elements: {self.total_interactive}{truncated_note}",
            "",
        ]
        for elem in elements:
            elem_text = elem.to_text()
            # Tronquer les labels longs si max_label spécifié
            if max_label is not None and len(elem_text) > max_label + 20:
                # Garder l'index et le role, tronquer le nom
                parts = elem_text.split('"')
                if len(parts) >= 3:
                    name = parts[1]
                    if len(name) > max_label:
                        parts[1] = name[:max_label] + "…"
                    elem_text = '"'.join(parts)
            lines.append(elem_text)
        return "\n".join(lines)


# ─── DOMIndexer ────────────────────────────────────────────────────────────

# Contrôles de bandeaux cookies / consentement (CMP). On les DÉPRIORISE dans
# l'index (jamais retirés) pour qu'ils ne raflent plus les premiers index :
# sinon le LLM clique "Préférences de consentement"/"Accepter" en croyant
# ouvrir le contenu (cas vécu en runtime). Déprioriser = sûr (l'élément reste
# accessible) vs supprimer = risqué.
_CONSENT_NAME_RE = re.compile(
    r"(tout accepter|tout refuser|accepter (?:et|&) fermer|\baccepter\b|"
    r"j'accepte|accept all|accept cookies|\baccept\b|agree and close|i agree|"
    r"\bagree\b|reject all|\brefuser\b|\breject\b|\bdecline\b|\bdeny\b|"
    r"pr[ée]f[ée]rences? de consentement|voir les pr[ée]f[ée]rences|"
    r"g[ée]rer les cookies|manage cookies|cookie settings|cookie preferences|"
    r"continuer sans accepter|consentement|gérer mes choix)",
    re.IGNORECASE,
)


def _is_consent_control(raw: Dict[str, Any]) -> bool:
    """True si l'élément ressemble à un bouton de bandeau cookies/consentement."""
    name = str(raw.get("name", "") or "").strip()
    if not name or len(name) > 48:
        return False
    return bool(_CONSENT_NAME_RE.search(name))


def _deprioritize_consent(raw_elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pousse les contrôles de consentement en fin de liste (tri stable :
    l'ordre des autres éléments est préservé). Appliqué AVANT la troncature
    pour que les vrais éléments de contenu gardent les premiers index."""
    if not raw_elements:
        return raw_elements
    return sorted(raw_elements, key=lambda r: 1 if _is_consent_control(r) else 0)


class DOMIndexer:
    """
    Extraie un DOMSnapshot depuis une page Playwright.

    Utilise l'arbre d'accessibilite (page.accessibility.snapshot())
    qui est bien plus compact que le HTML brut et identifie nativement
    les roles, noms, et etats des elements interactifs.
    """

    def __init__(self, max_elements: int = MAX_ELEMENTS):
        self.max_elements = max_elements

    async def snapshot(self, page: Any) -> DOMSnapshot:
        """
        Extrait le DOMSnapshot d'une page Playwright.

        Args:
            page: instance playwright.async_api.Page

        Returns:
            DOMSnapshot avec les elements interactifs indexes
        """
        try:
            title = await page.title()
        except Exception:
            title = ""  # titre page inaccessible

        url = page.url or ""

        # 1. Recuperer l'arbre d'accessibilite
        if not hasattr(page, 'accessibility'):
            return await self._snapshot_via_dom(page, url=url, title=title)
        try:
            tree = await page.accessibility.snapshot(interesting_only=True)
        except Exception as e:
            logger.warning(f"DOM indexer: accessibility.snapshot() echoue: {e}")
            return await self._snapshot_via_dom(page, url=url, title=title)

        if tree is None:
            return await self._snapshot_via_dom(page, url=url, title=title)

        # 2. Parcourir l'arbre et collecter les elements interactifs
        raw_elements = self._collect_interactive(tree)
        raw_elements = await self._merge_dom_augmented_inputs(page, raw_elements)
        raw_elements = _deprioritize_consent(raw_elements)
        total = len(raw_elements)

        # 3. Tronquer si necessaire et indexer
        truncated = total > self.max_elements
        selected = raw_elements[: self.max_elements]

        elements: List[DOMElement] = []
        for i, raw in enumerate(selected, start=1):
            elements.append(
                DOMElement(
                    index=i,
                    role=raw.get("role", ""),
                    name=raw.get("name", ""),
                    value=raw.get("valuetext", raw.get("valuemin", "")),
                    description=raw.get("description", ""),
                    options=self._extract_options(raw),
                    checked=raw.get("checked"),
                    disabled=raw.get("disabled", False),
                    bbox=None,  # sera rempli par enrich_with_bboxes()
                )
            )

        return DOMSnapshot(
            url=url,
            title=title,
            elements=elements,
            total_interactive=total,
            truncated=truncated,
        )

    async def _snapshot_via_dom(self, page: Any, *, url: str, title: str) -> DOMSnapshot:
        """Fallback DOM pur quand accessibility.snapshot() est indisponible.

        Certains builds Playwright/Chromium n'exposent pas `page.accessibility`.
        On extrait alors les éléments interactifs visibles via querySelectorAll.
        """
        try:
            raw_elements = await page.evaluate(
                """
                () => {
                    const selectors = [
                        'button',
                        'a[href]',
                        'input',
                        'textarea',
                        'select',
                        '[contenteditable="true"]',
                        '[contenteditable=""]',
                        '.ProseMirror',
                        '[role="button"]',
                        '[role="link"]',
                        '[role="textbox"]',
                        '[role="searchbox"]',
                        '[role="combobox"]',
                        '[role="checkbox"]',
                        '[role="radio"]',
                        '[role="switch"]',
                        '[role="option"]',
                        '[tabindex]:not([tabindex="-1"])',
                    ];

                    const seen = new Set();
                    const out = [];

                    const roleFromEl = (el) => {
                        const explicit = (el.getAttribute('role') || '').trim().toLowerCase();
                        if (explicit) return explicit;
                        const tag = (el.tagName || '').toLowerCase();
                        const type = (el.getAttribute('type') || '').toLowerCase();
                        if (tag === 'a') return 'link';
                        if (tag === 'button') return 'button';
                        if (tag === 'textarea') return 'textbox';
                        if (tag === 'select') return 'combobox';
                        if (el.isContentEditable || el.matches('[contenteditable="true"], [contenteditable=""], .ProseMirror')) return 'textbox';
                        if (tag === 'input') {
                            if (type === 'checkbox') return 'checkbox';
                            if (type === 'radio') return 'radio';
                            if (type === 'search') return 'searchbox';
                            if (['button', 'submit', 'reset'].includes(type)) return 'button';
                            return 'textbox';
                        }
                        return 'button';
                    };

                    const nameFromEl = (el) => {
                        return (
                            (el.getAttribute('aria-label') || '').trim() ||
                            (el.getAttribute('title') || '').trim() ||
                            (el.getAttribute('placeholder') || '').trim() ||
                            (el.getAttribute('data-placeholder') || '').trim() ||
                            (el.getAttribute('aria-placeholder') || '').trim() ||
                            (el.textContent || '').trim()
                        );
                    };

                    for (const sel of selectors) {
                        for (const el of document.querySelectorAll(sel)) {
                            if (!el || seen.has(el)) continue;
                            seen.add(el);

                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            const visible = style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                            if (!visible) continue;

                            const role = roleFromEl(el);
                            const name = nameFromEl(el);
                            const disabled = !!(el.disabled || el.getAttribute('aria-disabled') === 'true');

                            if (!name && !['textbox', 'searchbox', 'combobox', 'checkbox', 'radio', 'switch'].includes(role)) {
                                continue;
                            }

                            out.push({
                                role,
                                name,
                                description: '',
                                checked: typeof el.checked === 'boolean' ? !!el.checked : null,
                                disabled,
                                options: [],
                            });
                            if (out.length >= 300) return out;
                        }
                    }
                    return out;
                }
                """
            )
        except Exception as e:
            logger.warning(f"DOM indexer fallback DOM echoue: {e}")
            raw_elements = []

        raw_elements = _deprioritize_consent(raw_elements)
        total = len(raw_elements)
        truncated = total > self.max_elements
        selected = raw_elements[: self.max_elements]

        elements: List[DOMElement] = []
        for i, raw in enumerate(selected, start=1):
            elements.append(
                DOMElement(
                    index=i,
                    role=raw.get("role", ""),
                    name=raw.get("name", ""),
                    value=raw.get("value", ""),
                    description=raw.get("description", ""),
                    options=raw.get("options", []) or [],
                    checked=raw.get("checked"),
                    disabled=raw.get("disabled", False),
                    bbox=None,
                )
            )

        return DOMSnapshot(
            url=url,
            title=title,
            elements=elements,
            total_interactive=total,
            truncated=truncated,
        )

    async def enrich_with_bboxes(
        self, page: Any, snapshot: DOMSnapshot
    ) -> DOMSnapshot:
        """
        Enrichit chaque DOMElement avec sa bounding box viewport.

        Utilise page.evaluate() avec querySelectorAll pour trouver les elements
        correspondant a chaque (role, name) et recuperer leur getBoundingClientRect().

        Args:
            page: instance playwright Page
            snapshot: DOMSnapshot a enrichir

        Returns:
            Le meme DOMSnapshot avec les bbox remplies (in-place)
        """
        if not snapshot.elements:
            return snapshot

        # On utilise un script JS qui recupere toutes les bboxes
        # pour les elements interactifs visibles
        js_script = """
        () => {
            const ROLES = %s;
            const results = [];
            
            // Selecteurs par role ARIA
            const roleMap = {
                'button': 'button, [role="button"], input[type="submit"], input[type="button"]',
                'link': 'a[href], [role="link"]',
                'textbox': 'input[type="text"], input[type="email"], input[type="password"], input[type="url"], input[type="tel"], input[type="number"], input:not([type]), textarea, [role="textbox"], [contenteditable="true"], [contenteditable=""], .ProseMirror',
                'searchbox': 'input[type="search"], [role="searchbox"]',
                'combobox': 'select, [role="combobox"]',
                'checkbox': 'input[type="checkbox"], [role="checkbox"]',
                'radio': 'input[type="radio"], [role="radio"]',
                'switch': '[role="switch"]',
                'slider': 'input[type="range"], [role="slider"]',
                'spinbutton': 'input[type="number"], [role="spinbutton"]',
                'menuitem': '[role="menuitem"]',
                'menuitemcheckbox': '[role="menuitemcheckbox"]',
                'menuitemradio': '[role="menuitemradio"]',
                'tab': '[role="tab"]',
                'option': 'option, [role="option"]',
                'treeitem': '[role="treeitem"]',
            };
            
            const seen = new Set();
            
            for (const role of ROLES) {
                const selector = roleMap[role];
                if (!selector) continue;
                
                const elems = document.querySelectorAll(selector);
                for (const el of elems) {
                    if (seen.has(el)) continue;
                    seen.add(el);
                    
                    const rect = el.getBoundingClientRect();
                    // Ignorer les elements invisibles
                    if (rect.width === 0 || rect.height === 0) continue;
                    if (rect.bottom < 0 || rect.right < 0) continue;
                    
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    if (el.getAttribute('aria-hidden') === 'true') continue;
                    
                    // Determiner le role effectif
                    let effectiveRole = el.getAttribute('role') || '';
                    if (!effectiveRole) {
                        const tag = el.tagName.toLowerCase();
                        const type = (el.getAttribute('type') || '').toLowerCase();
                        if (tag === 'a') effectiveRole = 'link';
                        else if (tag === 'button' || type === 'submit' || type === 'button') effectiveRole = 'button';
                        else if (tag === 'select') effectiveRole = 'combobox';
                        else if (tag === 'textarea') effectiveRole = 'textbox';
                        else if (el.isContentEditable || el.matches?.('[contenteditable="true"], [contenteditable=""], .ProseMirror')) effectiveRole = 'textbox';
                        else if (tag === 'option') effectiveRole = 'option';
                        else if (type === 'checkbox') effectiveRole = 'checkbox';
                        else if (type === 'radio') effectiveRole = 'radio';
                        else if (type === 'range') effectiveRole = 'slider';
                        else if (type === 'search') effectiveRole = 'searchbox';
                        else if (['text','email','password','url','tel','number',''].includes(type) && tag === 'input') effectiveRole = 'textbox';
                    }
                    
                    // Determiner le nom
                    let name = el.getAttribute('aria-label') 
                             || el.getAttribute('alt')
                             || el.getAttribute('title')
                             || el.getAttribute('placeholder')
                             || el.getAttribute('data-placeholder')
                             || el.getAttribute('aria-placeholder')
                             || '';
                    if (!name && (el.isContentEditable || el.matches?.('.ProseMirror'))) {
                        name = 'Message input';
                    }
                    if (!name) {
                        name = el.innerText || el.textContent || '';
                        name = name.trim().substring(0, 80);
                    }
                    
                    results.push({
                        role: effectiveRole,
                        name: name,
                        x: rect.x,
                        y: rect.y,
                        w: rect.width,
                        h: rect.height,
                    });
                }
            }
            return results;
        }
        """ % str(list(INTERACTIVE_ROLES))

        try:
            js_results = await page.evaluate(js_script)
        except Exception as e:
            logger.warning(f"DOM indexer: enrich_with_bboxes JS echoue: {e}")
            return snapshot

        if not js_results:
            return snapshot

        # Matcher les elements js_results avec les elements du snapshot
        # par (role, nom) — en conservant les doublons (listes FIFO).
        js_by_role_name: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
        for idx, item in enumerate(js_results):
            item["__idx"] = idx
            key = (item["role"], item["name"][:80])
            js_by_role_name[key].append(item)

        matched = 0
        used_js_indices = set()
        for elem in snapshot.elements:
            key = (elem.role, (elem.name or elem.description)[:80])
            bucket = js_by_role_name.get(key)
            js_item = bucket.pop(0) if bucket else None
            if js_item:
                used_js_indices.add(js_item.get("__idx"))
                elem_dict = elem.__dict__
                elem_dict["bbox"] = (
                    js_item["x"],
                    js_item["y"],
                    js_item["w"],
                    js_item["h"],
                )
                matched += 1

        # Pour les elements non matches, tenter un fallback par role + position
        unmatched = [e for e in snapshot.elements if e.bbox is None]
        if unmatched and js_results:
            js_unmatched = [
                item
                for item in js_results
                if item.get("__idx") not in used_js_indices
            ]
            # Matcher par role seul (best effort)
            idx_map: Dict[str, List[Dict]] = {}
            for item in js_unmatched:
                idx_map.setdefault(item["role"], []).append(item)
            for elem in unmatched:
                candidates = idx_map.get(elem.role, [])
                if candidates:
                    picked = candidates.pop(0)
                    elem.__dict__["bbox"] = (
                        picked["x"],
                        picked["y"],
                        picked["w"],
                        picked["h"],
                    )
                    matched += 1

        logger.debug(
            f"DOM indexer: {matched}/{len(snapshot.elements)} elements enrichis avec bbox"
        )
        return snapshot

    async def _merge_dom_augmented_inputs(self, page: Any, raw_elements: List[Dict]) -> List[Dict]:
        """Ajoute les contenteditables visibles qui échappent à l'arbre accessibility."""
        try:
            extras = await page.evaluate(
                """
                () => {
                    const out = [];
                    const seen = new Set();
                    const selectors = ['[contenteditable="true"]', '[contenteditable=""]', '.ProseMirror'];

                    const nameFromEl = (el) => {
                        const label =
                            (el.getAttribute('aria-label') || '').trim() ||
                            (el.getAttribute('placeholder') || '').trim() ||
                            (el.getAttribute('data-placeholder') || '').trim() ||
                            (el.getAttribute('aria-placeholder') || '').trim() ||
                            (el.getAttribute('title') || '').trim();
                        if (label) return label;
                        const text = (el.innerText || el.textContent || '').trim();
                        return text.slice(0, 80) || 'Message input';
                    };

                    for (const sel of selectors) {
                        for (const el of document.querySelectorAll(sel)) {
                            if (!el || seen.has(el)) continue;
                            seen.add(el);
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            const visible =
                                style.display !== 'none' &&
                                style.visibility !== 'hidden' &&
                                rect.width > 0 &&
                                rect.height > 0;
                            if (!visible) continue;
                            out.push({
                                role: 'textbox',
                                name: nameFromEl(el),
                                description: '',
                                checked: null,
                                disabled: !!(el.getAttribute('aria-disabled') === 'true'),
                                options: [],
                            });
                        }
                    }
                    return out;
                }
                """
            )
        except Exception as e:
            logger.debug(f"DOM indexer: augmentation contenteditable echouee: {e}")
            return raw_elements

        if not isinstance(extras, list) or not extras:
            return raw_elements

        merged = list(raw_elements)
        existing = {
            (
                str(item.get("role", "")).strip().lower(),
                str(item.get("name", "")).strip().lower(),
            )
            for item in raw_elements
        }
        for extra in extras:
            key = (
                str(extra.get("role", "")).strip().lower(),
                str(extra.get("name", "")).strip().lower(),
            )
            if key in existing:
                continue
            existing.add(key)
            merged.append(extra)
        return merged

    def _collect_interactive(self, node: Dict, depth: int = 0) -> List[Dict]:
        """Parcours recursif de l'arbre accessibility pour collecter les interactifs."""
        results: List[Dict] = []

        role = (node.get("role") or "").lower()
        name = (node.get("name") or "").strip()

        # Garder si role interactif ET (a un nom OU est un input vide)
        if role in INTERACTIVE_ROLES:
            if name or role in ("textbox", "searchbox", "combobox", "checkbox", "radio"):
                results.append(node)

        # Recurser dans les enfants
        for child in node.get("children", []):
            results.extend(self._collect_interactive(child, depth + 1))

        return results

    def _extract_options(self, node: Dict) -> List[str]:
        """Extrait les options d'un select/combobox depuis ses enfants."""
        options: List[str] = []
        for child in node.get("children", []):
            child_role = (child.get("role") or "").lower()
            if child_role == "option":
                opt_name = (child.get("name") or "").strip()
                if opt_name:
                    options.append(opt_name)
        return options


# ─── Set-of-Mark overlay ──────────────────────────────────────────────────

# Palette de couleurs distinctes pour les labels
_SOM_COLORS = [
    (255, 0, 0),       # red
    (0, 128, 255),     # blue
    (0, 200, 0),       # green
    (255, 165, 0),     # orange
    (148, 0, 211),     # violet
    (0, 200, 200),     # cyan
    (255, 20, 147),    # deep pink
    (139, 69, 19),     # brown
    (0, 100, 0),       # dark green
    (255, 215, 0),     # gold
]


def render_set_of_mark(
    screenshot: "Image.Image",
    elements: List[DOMElement],
    *,
    label_size: int = 14,
    show_bbox: bool = True,
) -> "Image.Image":
    """
    Dessine les labels [1], [2], ... sur le screenshot a cote de chaque element.

    Args:
        screenshot: Image PIL du screenshot navigateur
        elements: Liste d'elements avec bbox remplie
        label_size: Taille de la police des labels (px approximatif)
        show_bbox: Si True, dessine aussi un rectangle autour de l'element

    Returns:
        Nouvelle Image PIL avec le overlay dessine
    """
    if not PIL_AVAILABLE:
        logger.warning("PIL non disponible — Set-of-Mark desactive")
        return screenshot

    img = screenshot.copy()
    draw = ImageDraw.Draw(img)

    # Essayer de charger une police TrueType, sinon fallback
    font = None
    try:
        font = ImageFont.truetype("arial.ttf", label_size)
    except Exception:  # arial absent, essayer alternative
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", label_size)
        except Exception:
            font = ImageFont.load_default()  # police par défaut

    for elem in elements:
        if elem.bbox is None:
            continue

        x, y, w, h = elem.bbox
        color = _SOM_COLORS[(elem.index - 1) % len(_SOM_COLORS)]

        # Dessiner le rectangle bbox
        if show_bbox:
            draw.rectangle(
                [(x, y), (x + w, y + h)],
                outline=color,
                width=2,
            )

        # Dessiner le label [N] au-dessus de l'element
        label = f"[{elem.index}]"
        # Calculer la taille du texte
        text_bbox = draw.textbbox((0, 0), label, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]

        # Position du label: en haut a gauche de l'element, au-dessus
        label_x = max(0, x)
        label_y = max(0, y - text_h - 4)

        # Fond semi-opaque pour lisibilite
        padding = 2
        draw.rectangle(
            [
                (label_x - padding, label_y - padding),
                (label_x + text_w + padding, label_y + text_h + padding),
            ],
            fill=(0, 0, 0, 200) if img.mode == "RGBA" else (0, 0, 0),
        )

        # Texte du label
        draw.text((label_x, label_y), label, fill=color, font=font)

    return img


# ─── Singleton indexer ─────────────────────────────────────────────────────

_dom_indexer: Optional[DOMIndexer] = None


def get_dom_indexer(max_elements: int = MAX_ELEMENTS) -> DOMIndexer:
    """Retourne l'instance singleton du DOMIndexer."""
    global _dom_indexer
    if _dom_indexer is None:
        _dom_indexer = DOMIndexer(max_elements=max_elements)
    return _dom_indexer
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
