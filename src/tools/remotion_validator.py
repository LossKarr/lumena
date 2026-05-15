"""
🎬 LUMENA — Remotion TSX Validator

Validation pré-rendu ultra-rapide (<100ms) pour éviter des rendus Docker
coûteux (1-3 min) voués à l'échec.

Catégories de validation :
  1. Structure — export default, imports Remotion
  2. Imports — cohérence (pas de staticFile sans assets)
  3. Balises JSX — matching basique
  4. Séquenceur — cohérence durationInFrames
  5. Taille — détection hallucination (composant > 200 lignes)
  6. Patterns dangereux — API Remotion incorrectes connues

Chaque vérification retourne un ValidationIssue avec severity et fix hint.
Le système peut auto-corriger certains problèmes (fixable=True).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class Severity(str, Enum):
    ERROR = "error"       # Bloque le rendu à coup sûr
    WARNING = "warning"   # Probablement un problème
    INFO = "info"         # Suggestion d'amélioration


@dataclass
class ValidationIssue:
    """Un problème détecté dans un fichier TSX."""
    file: str
    line: int
    severity: Severity
    code: str             # Identifiant machine (ex: "MISSING_EXPORT")
    message: str          # Description humaine
    fix_hint: str = ""    # Suggestion de correction pour le LLM
    fixable: bool = False # Peut être corrigé automatiquement
    auto_fix: str = ""    # Code corrigé si fixable=True


@dataclass
class ValidationResult:
    """Résultat global de la validation d'un projet Remotion."""
    valid: bool = True
    issues: List[ValidationIssue] = field(default_factory=list)
    files_checked: int = 0
    errors_count: int = 0
    warnings_count: int = 0

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        if issue.severity == Severity.ERROR:
            self.errors_count += 1
            self.valid = False
        elif issue.severity == Severity.WARNING:
            self.warnings_count += 1

    def summary(self) -> str:
        if self.valid and not self.issues:
            return f"✅ Validation OK ({self.files_checked} fichiers)"
        parts = [f"{'❌' if not self.valid else '⚠️'} {self.errors_count} erreur(s), {self.warnings_count} warning(s)"]
        for issue in self.issues[:10]:
            parts.append(f"  [{issue.severity.value}] {issue.file}:{issue.line} — {issue.code}: {issue.message}")
        if len(self.issues) > 10:
            parts.append(f"  ... et {len(self.issues) - 10} autre(s)")
        return "\n".join(parts)

    def errors_for_llm(self) -> str:
        """Format compact des erreurs pour injection dans un prompt LLM."""
        lines = []
        for issue in self.issues:
            if issue.severity == Severity.ERROR:
                hint = f" → FIX: {issue.fix_hint}" if issue.fix_hint else ""
                lines.append(f"[{issue.file}:{issue.line}] {issue.message}{hint}")
        return "\n".join(lines) if lines else ""


# ── Patterns de détection ─────────────────────────────────────────────────────

_REMOTION_IMPORTS = re.compile(
    r"import\s+\{[^}]*\}\s+from\s+['\"]remotion['\"]", re.MULTILINE
)
_EXPORT_DEFAULT = re.compile(
    r"export\s+default\s+", re.MULTILINE
)
_STATIC_FILE_USAGE = re.compile(
    r"staticFile\s*\(", re.MULTILINE
)
_DURATION_IN_FRAMES = re.compile(
    r"durationInFrames\s*[=:]\s*\{?\s*(\d+)", re.MULTILINE
)
_JSX_OPEN = re.compile(r"<([A-Z][A-Za-z0-9.]*)")
_JSX_SELF_CLOSE = re.compile(r"<([A-Z][A-Za-z0-9.]*)[^>]*/\s*>")
_JSX_CLOSE = re.compile(r"</([A-Z][A-Za-z0-9.]*)>")
_SPRING_USAGE = re.compile(r"spring\s*\(")
_INTERPOLATE_USAGE = re.compile(r"interpolate\s*\(")
_USE_CURRENT_FRAME = re.compile(r"useCurrentFrame\s*\(\s*\)")
_SEQUENCE_TAG = re.compile(r"<Sequence\b")

# Patterns d'erreurs connues
_BAD_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"from\s+['\"]@remotion/"), "BAD_IMPORT_PATH",
     "Utiliser 'remotion' au lieu de '@remotion/' — le package s'appelle 'remotion'"),
    (re.compile(r"import\s+React\s+from\s+['\"]react['\"]"), "UNNECESSARY_REACT_IMPORT",
     "React import inutile avec Remotion ≥4 (JSX automatique)"),
    (re.compile(r"useState|useEffect|useReducer"), "REACT_HOOKS_IN_VIDEO",
     "Les hooks React state/effect ne fonctionnent pas dans Remotion — utiliser useCurrentFrame()"),
    (re.compile(r"window\.|document\.|navigator\."), "BROWSER_API",
     "Les APIs navigateur ne sont pas disponibles pendant le rendu Remotion"),
    (re.compile(r"setTimeout|setInterval|requestAnimationFrame"), "ASYNC_TIMER",
     "Les timers ne fonctionnent pas — utiliser frame counting avec useCurrentFrame()"),
    (re.compile(r"require\s*\("), "COMMONJS_REQUIRE",
     "Utiliser import ES6 au lieu de require()"),
    (re.compile(r"<video\s|<audio\s"), "RAW_MEDIA_TAG",
     "Utiliser <Video> et <Audio> de remotion au lieu des tags HTML natifs"),
]


# ── Validateurs unitaires ─────────────────────────────────────────────────────

def _check_structure(filename: str, content: str) -> List[ValidationIssue]:
    """Vérifie la structure de base d'un composant TSX."""
    issues: List[ValidationIssue] = []

    if not _REMOTION_IMPORTS.search(content):
        issues.append(ValidationIssue(
            file=filename, line=1, severity=Severity.ERROR,
            code="NO_REMOTION_IMPORT",
            message="Aucun import depuis 'remotion' trouvé",
            fix_hint="Ajouter: import { useCurrentFrame, useVideoConfig, ... } from 'remotion';",
        ))

    if not _EXPORT_DEFAULT.search(content):
        issues.append(ValidationIssue(
            file=filename, line=1, severity=Severity.ERROR,
            code="NO_EXPORT_DEFAULT",
            message="Pas de 'export default' — le composant ne sera pas importable",
            fix_hint="Ajouter 'export default' devant la fonction composant principale",
        ))

    lines = content.split("\n")
    if len(lines) > 250:
        issues.append(ValidationIssue(
            file=filename, line=len(lines), severity=Severity.WARNING,
            code="COMPONENT_TOO_LONG",
            message=f"Composant de {len(lines)} lignes — probable hallucination ou code dupliqué",
            fix_hint="Simplifier le composant, max 150 lignes recommandées",
        ))

    return issues


def _check_imports_coherence(filename: str, content: str, has_assets: bool) -> List[ValidationIssue]:
    """Vérifie la cohérence des imports."""
    issues: List[ValidationIssue] = []

    if _STATIC_FILE_USAGE.search(content) and not has_assets:
        issues.append(ValidationIssue(
            file=filename, line=1, severity=Severity.WARNING,
            code="STATIC_FILE_NO_ASSETS",
            message="staticFile() utilisé mais aucun asset fourni — le rendu échouera",
            fix_hint="Remplacer staticFile('...') par une URL externe (Unsplash, placeholder) ou supprimer",
        ))

    if _SPRING_USAGE.search(content) and "spring" not in content.split("from")[0] if "from" in content else True:
        # Vérifier que spring est importé
        if "spring" not in content[:500]:
            issues.append(ValidationIssue(
                file=filename, line=1, severity=Severity.WARNING,
                code="SPRING_NOT_IMPORTED",
                message="spring() utilisé mais possiblement non importé depuis 'remotion'",
                fix_hint="Vérifier que spring est dans l'import { ... } from 'remotion'",
            ))

    if _USE_CURRENT_FRAME.search(content):
        import_section = content[:800]
        if "useCurrentFrame" not in import_section:
            issues.append(ValidationIssue(
                file=filename, line=1, severity=Severity.ERROR,
                code="USE_CURRENT_FRAME_NOT_IMPORTED",
                message="useCurrentFrame() utilisé mais non importé",
                fix_hint="Ajouter useCurrentFrame dans l'import depuis 'remotion'",
            ))

    return issues


def _check_bad_patterns(filename: str, content: str) -> List[ValidationIssue]:
    """Détecte les patterns d'erreurs connues."""
    issues: List[ValidationIssue] = []
    lines = content.split("\n")

    for pattern, code, message in _BAD_PATTERNS:
        match = pattern.search(content)
        if match:
            # Trouver le numéro de ligne
            line_num = content[:match.start()].count("\n") + 1
            issues.append(ValidationIssue(
                file=filename, line=line_num,
                severity=Severity.ERROR if code in ("BAD_IMPORT_PATH", "REACT_HOOKS_IN_VIDEO", "BROWSER_API") else Severity.WARNING,
                code=code,
                message=message,
                fix_hint=message,
            ))

    return issues


def _check_jsx_balance(filename: str, content: str) -> List[ValidationIssue]:
    """Vérification basique de l'équilibre des balises JSX."""
    issues: List[ValidationIssue] = []

    # Compter ouvertures et fermetures (heuristique simple)
    opens = _JSX_OPEN.findall(content)
    self_closes = _JSX_SELF_CLOSE.findall(content)
    closes = _JSX_CLOSE.findall(content)

    # Les self-closing ne comptent pas comme ouvertures nettes
    net_opens = len(opens) - len(self_closes)
    if abs(net_opens - len(closes)) > 3:
        issues.append(ValidationIssue(
            file=filename, line=1, severity=Severity.WARNING,
            code="JSX_IMBALANCE",
            message=f"Déséquilibre JSX détecté: {net_opens} ouvertures nettes vs {len(closes)} fermetures",
            fix_hint="Vérifier que toutes les balises JSX sont correctement fermées",
        ))

    return issues


def _check_sequencer(
    root_content: str,
    scene_files: Dict[str, str],
    expected_total_frames: int,
) -> List[ValidationIssue]:
    """Vérifie la cohérence du séquenceur Root.tsx."""
    issues: List[ValidationIssue] = []

    # Extraire les durationInFrames du Root
    durations = [int(m) for m in _DURATION_IN_FRAMES.findall(root_content)]
    if durations:
        total = sum(durations)
        if expected_total_frames > 0 and abs(total - expected_total_frames) > 30:
            issues.append(ValidationIssue(
                file="Root.tsx", line=1, severity=Severity.ERROR,
                code="DURATION_MISMATCH",
                message=f"Somme durationInFrames={total} ≠ attendu {expected_total_frames}",
                fix_hint=f"Ajuster les durationInFrames pour totaliser {expected_total_frames} frames",
                fixable=True,
            ))

    # Vérifier que les imports de scènes dans Root matchent les fichiers existants
    for scene_name in scene_files:
        base = Path(scene_name).stem
        if base not in root_content and base.replace("_", "") not in root_content.lower():
            issues.append(ValidationIssue(
                file="Root.tsx", line=1, severity=Severity.WARNING,
                code="SCENE_NOT_REFERENCED",
                message=f"Scène '{base}' existe mais n'est pas référencée dans Root.tsx",
                fix_hint=f"Importer et inclure le composant {base} dans la Composition",
            ))

    return issues


# ── API publique ──────────────────────────────────────────────────────────────

def validate_project(
    project_dir: str,
    expected_total_frames: int = 0,
    has_assets: bool = False,
) -> ValidationResult:
    """Valide un projet Remotion complet avant rendu.

    Args:
        project_dir: Chemin vers le dossier du projet Remotion
        expected_total_frames: Nombre de frames attendu (0 = skip check)
        has_assets: Si le projet utilise des assets locaux

    Returns:
        ValidationResult avec tous les problèmes détectés
    """
    result = ValidationResult()
    proj = Path(project_dir)
    src_dir = proj / "src"

    if not src_dir.exists():
        src_dir = proj  # Fallback si pas de sous-dossier src

    # Collecter les fichiers TSX
    tsx_files: Dict[str, str] = {}
    for ext in ("*.tsx", "*.jsx"):
        for f in src_dir.rglob(ext):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                rel = str(f.relative_to(proj))
                tsx_files[rel] = content
            except Exception:
                continue

    if not tsx_files:
        result.add(ValidationIssue(
            file="(project)", line=0, severity=Severity.ERROR,
            code="NO_TSX_FILES",
            message="Aucun fichier .tsx trouvé dans le projet",
            fix_hint="Vérifier que les scènes ont été générées correctement",
        ))
        return result

    result.files_checked = len(tsx_files)

    # Identifier Root.tsx et les scènes
    root_content = ""
    scene_files: Dict[str, str] = {}
    for name, content in tsx_files.items():
        if "Root" in name or "root" in name or "index" in name.lower():
            root_content = content
        else:
            scene_files[name] = content

    # Valider chaque fichier
    for filename, content in tsx_files.items():
        # Skip les fichiers de config/index minimalistes
        if len(content.strip()) < 20:
            continue

        for issue in _check_structure(filename, content):
            result.add(issue)
        for issue in _check_imports_coherence(filename, content, has_assets):
            result.add(issue)
        for issue in _check_bad_patterns(filename, content):
            result.add(issue)
        for issue in _check_jsx_balance(filename, content):
            result.add(issue)

    # Valider le séquenceur
    if root_content and scene_files:
        for issue in _check_sequencer(root_content, scene_files, expected_total_frames):
            result.add(issue)

    return result


def validate_single_component(
    content: str,
    filename: str = "Scene.tsx",
    has_assets: bool = False,
) -> ValidationResult:
    """Valide un composant TSX isolé (utile pendant la génération itérative)."""
    result = ValidationResult()
    result.files_checked = 1

    if not content or len(content.strip()) < 30:
        result.add(ValidationIssue(
            file=filename, line=0, severity=Severity.ERROR,
            code="EMPTY_COMPONENT",
            message="Composant vide ou trop court",
            fix_hint="Générer un composant TSX Remotion complet",
        ))
        return result

    for issue in _check_structure(filename, content):
        result.add(issue)
    for issue in _check_imports_coherence(filename, content, has_assets):
        result.add(issue)
    for issue in _check_bad_patterns(filename, content):
        result.add(issue)
    for issue in _check_jsx_balance(filename, content):
        result.add(issue)

    return result


# ── Auto-fix basique ──────────────────────────────────────────────────────────

def attempt_auto_fix(content: str, issues: List[ValidationIssue]) -> Tuple[str, List[str]]:
    """Tente de corriger automatiquement les problèmes simples.

    Returns:
        (content_corrigé, liste_des_corrections_appliquées)
    """
    fixes_applied: List[str] = []
    fixed = content

    for issue in issues:
        if issue.code == "BAD_IMPORT_PATH":
            fixed = re.sub(r"from\s+['\"]@remotion/([^'\"]+)['\"]", r"from 'remotion'", fixed)
            fixes_applied.append("Corrigé @remotion/* → remotion")

        elif issue.code == "UNNECESSARY_REACT_IMPORT":
            fixed = re.sub(r"import\s+React\s+from\s+['\"]react['\"];?\s*\n?", "", fixed)
            fixes_applied.append("Supprimé import React inutile")

        elif issue.code == "COMMONJS_REQUIRE":
            # Basique : ne pas tenter de transformer un require complexe
            pass

        elif issue.code == "RAW_MEDIA_TAG":
            fixed = fixed.replace("<video ", "<Video ")
            fixed = fixed.replace("<audio ", "<Audio ")
            if "Video" in fixed and "Video" not in fixed[:500]:
                # Ajouter l'import si manquant
                if "import" in fixed[:100]:
                    fixed = re.sub(
                        r"(import\s+\{[^}]*)(}\s+from\s+['\"]remotion['\"])",
                        r"\1, Video\2",
                        fixed, count=1
                    )
            fixes_applied.append("Remplacé <video>/<audio> par composants Remotion")

    return fixed, fixes_applied


# ── Error parser pour stderr de rendu ─────────────────────────────────────────

@dataclass
class RenderError:
    """Erreur parsée depuis la sortie du rendu."""
    category: str          # "syntax", "import", "runtime", "timeout", "unknown"
    file: str              # Fichier concerné (si identifiable)
    line: int              # Ligne (si identifiable)
    message: str           # Message d'erreur
    fix_suggestion: str    # Suggestion de correction
    severity: str = "error"


_RENDER_ERROR_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"Cannot find module '([^']+)'"), "import",
     "Module '{0}' manquant — vérifier l'import ou installer la dépendance"),
    (re.compile(r"SyntaxError:\s*(.+?)(?:\n|$)"), "syntax",
     "Erreur de syntaxe: {0}"),
    (re.compile(r"TypeError:\s*(.+?)(?:\n|$)"), "runtime",
     "TypeError: {0}"),
    (re.compile(r"ReferenceError:\s*([^\n]+)"), "runtime",
     "Variable non définie: {0}"),
    (re.compile(r"Property '([^']+)' does not exist on type"), "runtime",
     "Propriété '{0}' inexistante — vérifier l'API Remotion"),
    (re.compile(r"Expected \d+ arguments?, but got \d+"), "runtime",
     "Nombre d'arguments incorrect dans un appel de fonction"),
    (re.compile(r"error TS(\d+):\s*(.+?)(?:\n|$)"), "typescript",
     "Erreur TypeScript TS{0}: {1}"),
    (re.compile(r"ENOENT.*?'([^']+)'"), "filesystem",
     "Fichier introuvable: {0}"),
    (re.compile(r"npm ERR!.*?(\S+)"), "npm",
     "Erreur npm: {0}"),
]

_FILE_LINE_PATTERN = re.compile(r"(?:at\s+|in\s+|→\s*)?([^\s:]+\.(?:tsx?|jsx?)):(\d+)")


def parse_render_errors(stderr: str, stdout: str = "") -> List[RenderError]:
    """Parse les erreurs de rendu pour extraction de diagnostics actionnables."""
    errors: List[RenderError] = []
    combined = f"{stderr}\n{stdout}"

    if not combined.strip():
        return errors

    # Timeout
    if "Timeout" in combined or "timeout" in combined.lower():
        errors.append(RenderError(
            category="timeout", file="", line=0,
            message="Rendu timeout — projet trop lourd ou boucle infinie",
            fix_suggestion="Simplifier les animations, réduire le nombre de frames",
        ))
        return errors

    # Pattern matching
    for pattern, category, template in _RENDER_ERROR_PATTERNS:
        match = pattern.search(combined)
        if match:
            groups = match.groups()
            message = template.format(*groups) if groups else template

            # Chercher fichier/ligne associé
            file_match = _FILE_LINE_PATTERN.search(combined[max(0, match.start()-200):match.end()+200])
            file_name = file_match.group(1) if file_match else ""
            line_num = int(file_match.group(2)) if file_match else 0

            errors.append(RenderError(
                category=category,
                file=file_name,
                line=line_num,
                message=message,
                fix_suggestion=message,
            ))

    # Si aucun pattern matché mais erreur évidente
    if not errors and ("error" in combined.lower() or "Error" in combined):
        # Extraire les premières lignes d'erreur
        error_lines = [l for l in combined.split("\n") if "error" in l.lower() or "Error" in l][:3]
        if error_lines:
            errors.append(RenderError(
                category="unknown", file="", line=0,
                message="\n".join(error_lines)[:500],
                fix_suggestion="Analyser l'erreur et corriger le TSX",
            ))

    return errors


def format_errors_for_llm(errors: List[RenderError]) -> str:
    """Formatte les erreurs de rendu pour injection dans un prompt LLM de correction."""
    if not errors:
        return ""
    lines = ["ERREURS DE RENDU DÉTECTÉES:"]
    for err in errors[:5]:
        loc = f" ({err.file}:{err.line})" if err.file else ""
        lines.append(f"  [{err.category.upper()}]{loc} {err.message}")
        if err.fix_suggestion and err.fix_suggestion != err.message:
            lines.append(f"    → Suggestion: {err.fix_suggestion}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
