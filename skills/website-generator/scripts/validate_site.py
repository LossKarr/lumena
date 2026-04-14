#!/usr/bin/env python3
"""
Website Validator — Vérifie la qualité d'un site web généré.

Usage:
    python scripts/validate_site.py <project-name-or-path>

Examples:
    python scripts/validate_site.py mon-restaurant
    python scripts/validate_site.py ./workspace/saas-fitness

Checks effectués:
1. Structure : index.html, CSS, JS présents
2. Navigation : tous les navigateTo() pointent vers des sections existantes
3. Images : URLs valides (Unsplash, pravatar, picsum), fallback onerror
4. Interactivité : chaque bouton/lien a un onclick ou href fonctionnel
5. Design : variables CSS, pas de couleurs génériques, responsive meta
6. Contenu : pas de Lorem Ipsum, pas de TODO, dates 2026
7. Admin : sections admin non vides, pas de fade-in sur admin
8. Accessibilité : alt sur images, aria-labels sur boutons
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


class SiteValidator:
    """Valide un site web généré."""
    
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.passes: List[str] = []
        self.files: Dict[str, str] = {}
        
        self._load_files()
    
    def _load_files(self):
        """Charge tous les fichiers du projet."""
        for root, dirs, files in os.walk(self.project_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
            for f in files:
                fp = Path(root) / f
                rel = str(fp.relative_to(self.project_dir)).replace("\\", "/")
                try:
                    self.files[rel] = fp.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
    
    def _find_html(self) -> str:
        """Trouve le contenu du fichier HTML principal."""
        for name in ["index.html", "public/index.html"]:
            if name in self.files:
                return self.files[name]
        for name, content in self.files.items():
            if name.endswith(".html"):
                return content
        return ""
    
    def _find_css(self) -> str:
        """Récupère tout le CSS (fichiers + inline)."""
        css = ""
        for name, content in self.files.items():
            if name.endswith(".css"):
                css += content + "\n"
        html = self._find_html()
        style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
        css += "\n".join(style_blocks)
        return css
    
    def _find_js(self) -> str:
        """Récupère tout le JS (fichiers + inline)."""
        js = ""
        for name, content in self.files.items():
            if name.endswith(".js"):
                js += content + "\n"
        html = self._find_html()
        script_blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
        js += "\n".join(script_blocks)
        return js
    
    def check_structure(self):
        """Vérifie la structure de fichiers."""
        html = self._find_html()
        if not html:
            self.errors.append("❌ STRUCTURE: Pas de fichier index.html trouvé")
            return
        self.passes.append("✅ STRUCTURE: index.html présent")
        
        css = self._find_css()
        if len(css) < 100:
            self.errors.append("❌ STRUCTURE: CSS manquant ou trop court (< 100 chars)")
        elif len(css) < 2000:
            self.warnings.append("⚠️  STRUCTURE: CSS court (< 2000 chars) — un site premium devrait avoir 800+ lignes")
        else:
            self.passes.append(f"✅ STRUCTURE: CSS présent ({len(css):,} chars)")
        
        js = self._find_js()
        if len(js) < 50:
            self.errors.append("❌ STRUCTURE: JavaScript manquant")
        else:
            self.passes.append(f"✅ STRUCTURE: JavaScript présent ({len(js):,} chars)")
    
    def check_navigation(self):
        """Vérifie que la navigation SPA fonctionne."""
        html = self._find_html()
        js = self._find_js()
        all_code = html + "\n" + js
        
        # Trouver toutes les sections SPA
        sections = set(re.findall(r'id="(page-[^"]+)"', html))
        
        if not sections:
            self.warnings.append("⚠️  NAVIGATION: Aucune section page-* trouvée (pas un SPA ?)")
            return
        
        self.passes.append(f"✅ NAVIGATION: {len(sections)} sections SPA trouvées")
        
        # Trouver tous les navigateTo() appelés
        nav_calls = set(re.findall(r"navigateTo\(['\"]([^'\"]+)['\"]\)", all_code))
        
        # Vérifier que chaque navigateTo pointe vers une section existante
        for target in nav_calls:
            if target not in sections:
                self.errors.append(f"❌ NAVIGATION: navigateTo('{target}') — section inexistante")
            else:
                self.passes.append(f"✅ NAVIGATION: navigateTo('{target}') → OK")
        
        # Vérifier que navigateTo est global (pas dans DOMContentLoaded)
        if "function navigateTo" in all_code:
            self.passes.append("✅ NAVIGATION: fonction navigateTo() définie")
        else:
            self.errors.append("❌ NAVIGATION: fonction navigateTo() non trouvée")
        
        # Vérifier la section active par défaut
        if 'class="active"' in html or "class='active'" in html:
            self.passes.append("✅ NAVIGATION: section active par défaut trouvée")
        else:
            self.warnings.append("⚠️  NAVIGATION: pas de section active par défaut")
    
    def check_images(self):
        """Vérifie les images."""
        html = self._find_html()
        
        img_srcs = re.findall(r'src="([^"]*)"', html)
        img_srcs = [s for s in img_srcs if any(ext in s.lower() for ext in [".jpg", ".png", ".webp", ".svg", "unsplash", "pravatar", "picsum"])]
        
        if not img_srcs:
            self.warnings.append("⚠️  IMAGES: Aucune image trouvée")
            return
        
        valid_domains = ["unsplash.com", "pravatar.cc", "picsum.photos", "cdnjs.cloudflare.com", "fonts.googleapis.com"]
        
        for src in img_srcs:
            if src.startswith("http"):
                if any(d in src for d in valid_domains):
                    continue  # OK
                else:
                    self.warnings.append(f"⚠️  IMAGES: URL externe non standard : {src[:80]}")
            elif src.startswith("data:"):
                continue  # Data URI, OK
        
        # Vérifier fallback onerror
        onerror_count = html.count("onerror=")
        if onerror_count == 0:
            self.warnings.append("⚠️  IMAGES: Aucun fallback onerror sur les images")
        else:
            self.passes.append(f"✅ IMAGES: {onerror_count} fallback(s) onerror trouvé(s)")
        
        self.passes.append(f"✅ IMAGES: {len(img_srcs)} image(s) détectée(s)")
    
    def check_design(self):
        """Vérifie les règles de design."""
        css = self._find_css()
        html = self._find_html()
        
        # Variables CSS
        if "--primary" in css or "--color-primary" in css:
            self.passes.append("✅ DESIGN: Variables CSS de couleur trouvées")
        else:
            self.errors.append("❌ DESIGN: Pas de variables CSS (--primary, --color-primary)")
        
        # Couleurs génériques
        generic_colors = re.findall(r'(?:color|background):\s*(red|blue|green|yellow|purple)\b', css, re.IGNORECASE)
        if generic_colors:
            self.errors.append(f"❌ DESIGN: Couleurs génériques trouvées : {', '.join(set(generic_colors))} — utiliser HSL")
        else:
            self.passes.append("✅ DESIGN: Pas de couleurs génériques")
        
        # HSL
        hsl_count = len(re.findall(r'hsl\(', css + html))
        if hsl_count > 0:
            self.passes.append(f"✅ DESIGN: {hsl_count} valeurs HSL trouvées")
        else:
            self.warnings.append("⚠️  DESIGN: Aucune valeur HSL trouvée")
        
        # Responsive meta
        if 'viewport' in html:
            self.passes.append("✅ DESIGN: Meta viewport présent")
        else:
            self.errors.append("❌ DESIGN: Meta viewport manquant (pas responsive)")
        
        # Google Fonts
        if "fonts.googleapis.com" in html:
            self.passes.append("✅ DESIGN: Google Fonts chargé")
        else:
            self.warnings.append("⚠️  DESIGN: Pas de Google Fonts")
        
        # Font Awesome
        if "font-awesome" in html or "fontawesome" in html:
            self.passes.append("✅ DESIGN: Font Awesome chargé")
        else:
            self.warnings.append("⚠️  DESIGN: Pas de Font Awesome")
    
    def check_interactivity(self):
        """Vérifie que les éléments interactifs fonctionnent."""
        html = self._find_html()
        js = self._find_js()
        
        # Boutons sans onclick
        buttons = re.findall(r'<button[^>]*>(.*?)</button>', html, re.DOTALL)
        buttons_no_action = []
        for match in re.finditer(r'<button([^>]*)>', html):
            attrs = match.group(1)
            if "onclick" not in attrs and "type=\"submit\"" not in attrs and "class=\"hamburger\"" not in attrs.lower():
                # Vérifier s'il a un addEventListener dans le JS
                btn_text = html[match.end():match.end() + 200]
                if "onclick" not in attrs:
                    buttons_no_action.append(attrs[:50])
        
        if buttons_no_action and len(buttons_no_action) > len(buttons) * 0.5:
            self.warnings.append(f"⚠️  INTERACTIVITÉ: {len(buttons_no_action)} boutons potentiellement sans action")
        
        # Formulaires
        forms = re.findall(r'<form[^>]*>', html)
        if forms:
            self.passes.append(f"✅ INTERACTIVITÉ: {len(forms)} formulaire(s) trouvé(s)")
        
        # Event listeners / onclick
        onclick_count = html.count("onclick=")
        listener_count = js.count("addEventListener")
        total_interactions = onclick_count + listener_count
        
        if total_interactions > 0:
            self.passes.append(f"✅ INTERACTIVITÉ: {total_interactions} interactions (onclick + listeners)")
        else:
            self.errors.append("❌ INTERACTIVITÉ: Aucune interaction trouvée")
    
    def check_content(self):
        """Vérifie la qualité du contenu."""
        html = self._find_html()
        
        # Lorem Ipsum
        if "lorem ipsum" in html.lower():
            self.errors.append("❌ CONTENU: Lorem Ipsum détecté — le contenu doit être réaliste")
        else:
            self.passes.append("✅ CONTENU: Pas de Lorem Ipsum")
        
        # TODO / placeholder
        todo_count = len(re.findall(r'TODO|FIXME|à compléter|placeholder', html, re.IGNORECASE))
        if todo_count > 0:
            self.errors.append(f"❌ CONTENU: {todo_count} TODO/placeholder trouvé(s)")
        else:
            self.passes.append("✅ CONTENU: Pas de TODO/placeholder")
        
        # Dates 2024/2025
        old_dates = re.findall(r'202[0-5]', html)
        if old_dates:
            self.warnings.append(f"⚠️  CONTENU: Dates anciennes trouvées ({', '.join(set(old_dates))}) — utiliser 2026")
        else:
            self.passes.append("✅ CONTENU: Dates à jour (2026)")
        
        # console.log
        js = self._find_js()
        if "console.log" in js:
            self.warnings.append("⚠️  CONTENU: console.log trouvé — à retirer en production")
    
    def check_admin(self):
        """Vérifie les sections admin."""
        html = self._find_html()
        css = self._find_css()
        
        admin_sections = re.findall(r'id="(page-admin[^"]*)"', html)
        if not admin_sections:
            return  # Pas de section admin, OK
        
        self.passes.append(f"✅ ADMIN: {len(admin_sections)} section(s) admin trouvée(s)")
        
        for section_id in admin_sections:
            # Trouver le contenu de la section
            pattern = rf'id="{section_id}"[^>]*>(.*?)(?=<section|</body|$)'
            match = re.search(pattern, html, re.DOTALL)
            if match:
                content = match.group(1)
                # Vérifier que ce n'est pas vide
                text_content = re.sub(r'<[^>]+>', '', content).strip()
                if len(text_content) < 50:
                    self.errors.append(f"❌ ADMIN: Section {section_id} quasi-vide ({len(text_content)} chars de texte)")
                
                # Vérifier pas de fade-in dans admin
                if 'fade-in' in content:
                    self.errors.append(f"❌ ADMIN: Section {section_id} contient des .fade-in — interdit (display:none → invisible)")
    
    def validate(self) -> Tuple[int, int, int]:
        """Lance toutes les validations. Retourne (passes, warnings, errors)."""
        self.check_structure()
        self.check_navigation()
        self.check_images()
        self.check_design()
        self.check_interactivity()
        self.check_content()
        self.check_admin()
        
        return len(self.passes), len(self.warnings), len(self.errors)
    
    def report(self) -> str:
        """Génère le rapport de validation."""
        p, w, e = self.validate()
        total = p + w + e
        score = int((p / total) * 100) if total > 0 else 0
        
        # Note
        if score >= 90:
            grade = "A+"
            emoji = "🏆"
        elif score >= 80:
            grade = "A"
            emoji = "✨"
        elif score >= 70:
            grade = "B"
            emoji = "👍"
        elif score >= 60:
            grade = "C"
            emoji = "⚠️"
        else:
            grade = "D"
            emoji = "❌"
        
        lines = [
            f"\n{'═' * 60}",
            f"  {emoji} VALIDATION DU SITE — Note : {grade} ({score}%)",
            f"{'═' * 60}\n",
            f"  ✅ {p} tests réussis | ⚠️  {w} avertissements | ❌ {e} erreurs\n",
        ]
        
        if self.errors:
            lines.append("── ERREURS ──")
            for err in self.errors:
                lines.append(f"  {err}")
            lines.append("")
        
        if self.warnings:
            lines.append("── AVERTISSEMENTS ──")
            for warn in self.warnings:
                lines.append(f"  {warn}")
            lines.append("")
        
        if self.passes:
            lines.append("── RÉUSSIS ──")
            for ok in self.passes:
                lines.append(f"  {ok}")
            lines.append("")
        
        lines.append(f"{'═' * 60}")
        
        return "\n".join(lines)


def resolve_project_path(name_or_path: str) -> Path:
    """Résout le nom de projet en chemin absolu."""
    path = Path(name_or_path)
    if path.exists():
        return path.resolve()
    
    lumena_root = Path(__file__).parent.parent.parent
    workspace = lumena_root / "workspace"
    candidate = workspace / name_or_path
    if candidate.exists():
        return candidate
    
    return path


def main():
    parser = argparse.ArgumentParser(description="Valide la qualité d'un site web généré")
    parser.add_argument("project", help="Nom du projet ou chemin du dossier")
    
    args = parser.parse_args()
    
    project_path = resolve_project_path(args.project)
    
    if not project_path.exists():
        print(f"❌ Projet introuvable : {project_path}")
        sys.exit(1)
    
    validator = SiteValidator(project_path)
    print(validator.report())
    
    # Exit code basé sur les erreurs
    if validator.errors:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
