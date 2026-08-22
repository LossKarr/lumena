"""
Lumena skills loader and runtime matching.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None


# LOT Z10 — score minimal pour qu'un skill soit injecté dans un prompt.
# Le matcher a un PLANCHER structurel à 5.5 : un skill totalement hors sujet
# marque déjà 5.5. Tout seuil ≤ 5.5 laisse donc passer l'intégralité du bruit.
_MIN_SKILL_SCORE: float = 6.0

ACTION_VERBS = {
    # French infinitives
    "creer", "generer", "modifier", "editer", "corriger",
    "tester", "ouvrir", "lire", "ecrire", "transformer",
    "convertir", "analyser", "construire", "faire", "deployer",
    "developper", "coder", "lancer", "demarrer", "afficher",
    "montrer", "fabriquer", "installer", "configurer", "supprimer",
    # French conjugated (common forms tu/il/imperative)
    "cree", "genere", "modifie", "corrige", "teste",
    "transforme", "convertis", "analyse", "construit", "construis",
    "fais", "fait", "deploie", "developpe", "code", "lance",
    "demarre", "affiche", "montre", "fabrique", "installe",
    "configure", "supprime", "ouvre", "ecris", "lis",
    # English
    "build", "create", "edit", "fix", "test", "generate",
    "read", "write", "deploy", "develop", "make", "show",
    "display", "install", "launch", "start", "run", "delete",
}

# Verbes d'observation: n'impliquent PAS de creation/modification.
# Exclus du bonus d'action verb (+1.5) pour eviter les faux positifs
# (ex: "analyse le site web" ≠ "cree un site web").
OBSERVATION_VERBS = {
    "analyser", "analyse",
    "lire", "lis",
    "afficher", "affiche",
    "montrer", "montre",
    "read", "show", "display",
}

# Stop-words: mots courants qui ne portent pas d'intention.
# Filtres du calcul d'overlap pour eviter les faux positifs.
FRENCH_STOP_WORDS = {
    "a", "ai", "au", "aux", "avec", "bien", "bon", "ca",
    "ce", "ces", "comme", "dans", "de", "des", "du", "elle",
    "en", "es", "est", "et", "ete", "eux", "il", "ils",
    "je", "la", "le", "les", "leur", "lui", "ma", "mais",
    "me", "mes", "moi", "mon", "ne", "nos", "notre", "nous",
    "on", "ont", "ou", "par", "pas", "plus", "pour", "qu",
    "que", "quel", "quelle", "qui", "quoi", "sa", "se", "ses",
    "si", "son", "sont", "sur", "ta", "te", "tes", "toi",
    "ton", "tout", "tres", "tu", "un", "une", "va", "vos",
    "votre", "vous", "y",
}

EXTENSION_TRIGGER_MAP = {
    # ── Documents Office ──────────────────────────────────────────────────────
    "pdf": {"pdf"},
    "docx": {"docx", "word", "rapport", "lettre", "memo", "courrier"},
    "pptx": {"pptx", "powerpoint", "deck", "presentation", "slides", "slide", "diapo", "diaporama"},
    # "calcul" seul est trop generique : une app web de calcul ne doit pas
    # activer Excel. Les formats et termes tableur explicites restent suffisants.
    "xlsx": {"xlsx", "excel", "sheet", "tableur", "spreadsheet", "csv"},
    # ── Web / Frontend ────────────────────────────────────────────────────────
    "website": {"site", "website", "landing", "portfolio", "ecommerce", "boutique", "vitrine", "homepage"},
    "frontend": {"frontend", "html", "css", "javascript", "interface", "ui", "ux", "dashboard", "navbar", "composant", "component", "page"},
    # ── Art / Design visuel ───────────────────────────────────────────────────
    "algorithmic": {"generatif", "generative", "p5", "p5js", "particules", "particles", "flowfield", "algorithmique"},
    "canvas": {"poster", "affiche", "visuel", "illustration", "graphisme", "artwork", "dessin"},
    # ── Vidéo / Animation ─────────────────────────────────────────────────────
    # NB: "rendu" retiré (matchait « compte-rendu » → faux positif). On garde
    # "render"/"rendu video" plus spécifiques au montage vidéo Remotion.
    "remotion": {"video", "reel", "tiktok", "short", "clip", "motion", "captions", "sous-titres", "render"},
    "gif": {"gif", "anime"},
    # ── Code / Apps ───────────────────────────────────────────────────────────
    "artifacts": {"react", "tailwind", "shadcn", "widget", "artifact"},
    "webapp": {"playwright", "testing", "tester", "selenium", "browser"},
    "mcp": {"mcp", "protocol", "fastmcp"},
    # ── Communication ─────────────────────────────────────────────────────────
    "internal": {"newsletter", "communication", "comms", "statut", "incident"},
    "doc": {"documentation", "spec", "proposal", "rediger", "documenter"},
    # ── Git ───────────────────────────────────────────────────────────────────
    "commit": {"commit", "git"},
    # ── Skill / Theme ─────────────────────────────────────────────────────────
    "skill": {"skill", "competence"},
    "theme": {"theme", "palette", "charte"},
    # ── Debug / Ops ───────────────────────────────────────────────────────────
    "dom": {"getelementbyid", "queryselector", "timing", "domcontentloaded"},
    "email": {"smtp", "gmail"},
    "disk": {"disque", "disk", "stockage"},
}

MAX_SKILL_ARCHIVE_FILES = 400
MAX_SKILL_ARCHIVE_FILE_SIZE = 10 * 1024 * 1024
MAX_SKILL_ARCHIVE_TOTAL_SIZE = 50 * 1024 * 1024
BLOCKED_SKILL_FILE_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".cpl",
    ".dll",
    ".exe",
    ".jar",
    ".jsm",
    ".lnk",
    ".msi",
    ".ps1",
    ".psm1",
    ".reg",
    ".scr",
    ".sys",
    ".vbe",
    ".vbs",
}


@dataclass
class Skill:
    """Loaded skill data."""

    name: str
    display_name: str
    description: str
    instructions: str
    path: Path
    scripts: List[Path] = field(default_factory=list)
    references: List[Path] = field(default_factory=list)
    assets: List[Path] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    keywords: List[str] = field(default_factory=list)
    apply_to: List[str] = field(default_factory=list)  # tokens/"*" → injection forcée même si score < seuil

    def get_script(self, name: str) -> Optional[Path]:
        for script in self.scripts:
            if script.stem == name or script.name == name:
                return script
        return None

    def get_reference(self, name: str) -> Optional[str]:
        for ref in self.references:
            if ref.stem == name or ref.name == name:
                return ref.read_text(encoding="utf-8")
        return None

    def to_context(self) -> str:
        lines = [
            f"## Skill: {self.display_name}\n",
            f"{self.description}\n",
            "### Instructions\n",
            f"{self.instructions}\n",
            "### Ressources disponibles\n",
        ]
        if self.scripts:
            script_names = ', '.join(s.stem for s in self.scripts)
            lines.append(f"- **Scripts** ({len(self.scripts)}): {script_names}")
            lines.append(f"  → Utilise `execute_skill(skill_name='{self.name}', script_name='<nom>')` pour les lancer")
        if self.references:
            ref_names = ', '.join(r.stem for r in self.references)
            lines.append(f"- **References** ({len(self.references)}): {ref_names}")
            lines.append(f"  → Utilise `read_skill_reference(skill_name='{self.name}', reference_name='<nom>')` pour les lire")
        if self.assets:
            lines.append(f"- **Assets**: {len(self.assets)} fichiers")
        lines.append("")
        return "\n".join(lines)


@dataclass
class SkillMatch:
    """Runtime skill selection result."""

    name: str
    display_name: str
    score: float
    reasons: List[str] = field(default_factory=list)
    description: str = ""


class SkillLoader:
    """Loads skills and provides runtime skill matching."""

    def __init__(self, base_dirs: Optional[List[Path]] = None):
        if base_dirs is None:
            from src.utils.paths import ROOT_DIR, INSTALLED_SKILLS_DIR
            root = ROOT_DIR
            base_dirs = [root / "skills", INSTALLED_SKILLS_DIR]

        self.base_dirs = [Path(d) for d in base_dirs]
        self.skills: Dict[str, Skill] = {}
        self.last_install_error: str = ""

        for directory in self.base_dirs:
            directory.mkdir(parents=True, exist_ok=True)

    def _fail_install(self, message: str) -> None:
        self.last_install_error = message
        logger.error(message)

    def _validate_skill_archive(self, zipf: zipfile.ZipFile) -> Tuple[bool, str, str]:
        infos = [info for info in zipf.infolist() if not info.is_dir()]
        if not infos:
            return False, "Archive .skill vide", ""

        if len(infos) > MAX_SKILL_ARCHIVE_FILES:
            return False, f"Archive trop volumineuse: {len(infos)} fichiers (max {MAX_SKILL_ARCHIVE_FILES})", ""

        total_uncompressed = 0
        top_dirs = set()
        has_skill_md = False

        for info in infos:
            member = info.filename.replace("\\", "/")
            if member.startswith("/"):
                return False, f"Chemin absolu interdit dans l'archive: {member}", ""

            path = PurePosixPath(member)
            parts = path.parts
            if not parts:
                return False, "Entrée de fichier invalide dans l'archive", ""

            if any(part in {"", ".", ".."} for part in parts):
                return False, f"Chemin dangereux détecté dans l'archive: {member}", ""

            if len(parts) < 2:
                return False, f"Structure invalide: '{member}' doit être sous un dossier de skill", ""

            top_dirs.add(parts[0])

            file_size = int(info.file_size or 0)
            if file_size > MAX_SKILL_ARCHIVE_FILE_SIZE:
                return False, (
                    f"Fichier trop volumineux: {member} ({file_size} octets, max {MAX_SKILL_ARCHIVE_FILE_SIZE})"
                ), ""

            total_uncompressed += file_size
            if total_uncompressed > MAX_SKILL_ARCHIVE_TOTAL_SIZE:
                return False, (
                    f"Archive trop volumineuse: {total_uncompressed} octets (max {MAX_SKILL_ARCHIVE_TOTAL_SIZE})"
                ), ""

            extension = Path(parts[-1]).suffix.lower()
            if extension in BLOCKED_SKILL_FILE_EXTENSIONS:
                return False, f"Fichier interdit détecté: {member}", ""

            if len(parts) == 2 and parts[1].lower() == "skill.md":
                has_skill_md = True

        if len(top_dirs) != 1:
            return False, "Archive invalide: un seul dossier racine de skill est autorisé", ""

        skill_name = next(iter(top_dirs))
        if not has_skill_md:
            return False, "Archive invalide: fichier SKILL.md manquant à la racine du skill", ""

        return True, "", skill_name

    def _safe_extract_skill_archive(self, zipf: zipfile.ZipFile, target_dir: Path) -> None:
        base = target_dir.resolve()
        for info in zipf.infolist():
            if info.is_dir():
                continue

            member = info.filename.replace("\\", "/")
            destination = (base / PurePosixPath(member)).resolve()
            if not str(destination).startswith(str(base)):
                raise ValueError(f"Chemin d'extraction refusé: {member}")

            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipf.open(info, "r") as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)

    def load_all(self) -> Dict[str, Skill]:
        """Load skills from configured folders."""
        self.skills.clear()

        for base_dir in self.base_dirs:
            if not base_dir.exists():
                continue

            for entry in sorted(base_dir.iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith("."):
                    continue

                skill: Optional[Skill] = None
                if entry.is_dir():
                    skill_md = entry / "SKILL.md"
                    if skill_md.exists():
                        skill = self._load_skill_directory(entry)
                elif entry.is_file() and entry.suffix.lower() == ".md":
                    skill = self._load_legacy_skill_file(entry)

                if skill:
                    self._register_loaded_skill(skill, base_dir)

        logger.info(f"{len(self.skills)} skills loaded")
        return self.skills

    def _register_loaded_skill(self, skill: Skill, source_dir: Path) -> None:
        existing = self.skills.get(skill.name)
        if existing:
            # Prefer modern directory skills over legacy flat .md skills.
            if existing.path.is_dir() and skill.path.is_file():
                logger.warning(
                    "Skill collision on '{}': keeping directory '{}' and skipping legacy file '{}'",
                    skill.name,
                    existing.path,
                    skill.path,
                )
                return
            if existing.path.is_file() and skill.path.is_dir():
                logger.warning(
                    "Skill collision on '{}': replacing legacy file '{}' with directory '{}'",
                    skill.name,
                    existing.path,
                    skill.path,
                )
                self.skills[skill.name] = skill
                logger.debug("Skill loaded: {} (from {})", skill.name, source_dir)
                return
            logger.warning(
                "Skill collision on '{}': '{}' replaced by '{}'",
                skill.name,
                existing.path,
                skill.path,
            )
        self.skills[skill.name] = skill
        logger.debug("Skill loaded: {} (from {})", skill.name, source_dir)

    def register_single(self, skill_path: Path) -> Optional[Skill]:
        """Charge et enregistre UN seul skill sans recharger tout le registre."""
        skill_path = Path(skill_path)
        skill: Optional[Skill] = None
        if skill_path.is_dir():
            skill_md = skill_path / "SKILL.md"
            if skill_md.exists():
                skill = self._load_skill_directory(skill_path)
        elif skill_path.is_file() and skill_path.suffix.lower() == ".md":
            skill = self._load_legacy_skill_file(skill_path)
        if skill:
            source_dir = skill_path.parent if skill_path.is_file() else skill_path.parent
            self._register_loaded_skill(skill, source_dir)
            logger.info("Skill registered (single): {}", skill.name)
        return skill

    def _load_skill_directory(self, skill_dir: Path) -> Optional[Skill]:
        skill_md = skill_dir / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Skill read error {}: {}", skill_dir.name, e)
            return None

        frontmatter, body = self._parse_frontmatter(content)
        if not body.strip():
            logger.warning("Skill {} has empty instructions", skill_dir.name)

        name = self._normalize_skill_name(frontmatter.get("name") or skill_dir.name)
        description = str(frontmatter.get("description") or "").strip()
        display_name = self._to_display_name(name)
        keywords = self._extract_keywords(frontmatter, name, description)
        apply_to = self._parse_apply_to(frontmatter)

        scripts = self._collect_resource_files(skill_dir / "scripts")
        references = self._collect_resource_files(skill_dir / "references")
        assets = self._collect_resource_files(skill_dir / "assets")

        return Skill(
            name=name,
            display_name=display_name,
            description=description,
            instructions=body.strip(),
            path=skill_dir,
            scripts=scripts,
            references=references,
            assets=assets,
            metadata=frontmatter,
            keywords=keywords,
            apply_to=apply_to,
        )

    def _load_legacy_skill_file(self, skill_file: Path) -> Optional[Skill]:
        try:
            content = skill_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.error("Legacy skill read error {}: {}", skill_file.name, e)
            return None

        frontmatter, body = self._parse_frontmatter(content)
        name = self._normalize_skill_name(frontmatter.get("name") or skill_file.stem)
        description = str(frontmatter.get("description") or "").strip()
        if not description:
            description = self._extract_first_plain_line(body)

        keywords = self._extract_keywords(frontmatter, name, description)
        apply_to = self._parse_apply_to(frontmatter)
        display_name = self._to_display_name(name)

        return Skill(
            name=name,
            display_name=display_name,
            description=description,
            instructions=body.strip() if body.strip() else content.strip(),
            path=skill_file,
            metadata=frontmatter,
            keywords=keywords,
            apply_to=apply_to,
        )

    def _parse_frontmatter(self, content: str) -> Tuple[Dict[str, Any], str]:
        if not content.startswith("---"):
            return {}, content

        lines = content.splitlines()
        end_index = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_index = i
                break

        if end_index == -1:
            return {}, content

        frontmatter_raw = "\n".join(lines[1:end_index]).strip()
        body = "\n".join(lines[end_index + 1 :]).strip()
        parsed = self._parse_frontmatter_yaml(frontmatter_raw)
        return parsed, body

    def _parse_frontmatter_yaml(self, raw: str) -> Dict[str, Any]:
        if not raw:
            return {}

        if yaml is not None:
            try:
                parsed = yaml.safe_load(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass  # YAML parsing échoué

        result: Dict[str, Any] = {}
        current_key: Optional[str] = None
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if stripped.startswith("- ") and current_key:
                existing = result.get(current_key)
                if not isinstance(existing, list):
                    existing = []
                existing.append(stripped[2:].strip().strip("\"'"))
                result[current_key] = existing
                continue

            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            current_key = key

            if not value:
                result[key] = []
                continue

            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if not inner:
                    result[key] = []
                else:
                    result[key] = [part.strip().strip("\"'") for part in inner.split(",") if part.strip()]
                continue

            result[key] = value.strip("\"'")

        return result

    def _extract_keywords(self, frontmatter: Dict[str, Any], name: str, description: str) -> List[str]:
        keywords: List[str] = []
        raw_keywords = frontmatter.get("keywords")
        if isinstance(raw_keywords, list):
            keywords.extend(str(item) for item in raw_keywords if str(item).strip())
        elif isinstance(raw_keywords, str):
            keywords.extend(part.strip() for part in raw_keywords.split(",") if part.strip())

        keywords.extend(name.replace("-", " ").split())
        keywords.extend(description.split())

        unique: List[str] = []
        seen = set()
        for token in keywords:
            norm = _normalize_text(token)
            if norm and norm not in seen:
                unique.append(norm)
                seen.add(norm)
        return unique

    def _parse_apply_to(self, frontmatter: Dict[str, Any]) -> List[str]:
        """Extrait applyTo du frontmatter: liste de tokens ou "*" pour injection forcée."""
        raw = frontmatter.get("applyTo") or frontmatter.get("apply_to") or []
        if isinstance(raw, str):
            if raw.strip() == "*":
                return ["*"]
            return [t.strip().lower() for t in raw.split(",") if t.strip()]
        if isinstance(raw, list):
            return [str(x).strip().lower() for x in raw if str(x).strip()]
        return []

    def _collect_resource_files(self, directory: Path) -> List[Path]:
        if not directory.exists() or not directory.is_dir():
            return []
        return sorted([path for path in directory.rglob("*") if path.is_file()])

    def _extract_first_plain_line(self, text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            return stripped[:200]
        return ""

    def _normalize_skill_name(self, value: str) -> str:
        normalized = _normalize_text(value).replace(" ", "-")
        normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
        return normalized or "unknown-skill"

    def _to_display_name(self, name: str) -> str:
        return " ".join(word.capitalize() for word in name.split("-") if word)

    def get_skill(self, name: str) -> Optional[Skill]:
        return self.skills.get(name)

    def list_skills(self) -> List[str]:
        return sorted(self.skills.keys())

    def get_skills_context(self) -> str:
        if not self.skills:
            return "Aucun skill disponible."

        lines = ["# Skills disponibles", ""]
        for skill in self.skills.values():
            lines.append(f"## {skill.display_name}")
            lines.append(f"- Nom: `{skill.name}`")
            if skill.description:
                lines.append(f"- Description: {skill.description}")
            lines.append("")
        return "\n".join(lines).strip()

    def match_skills(self, query: str, max_results: int = 3) -> List[SkillMatch]:
        if not query or not query.strip():
            return []

        if not self.skills:
            self.load_all()

        normalized_query = _normalize_text(query)
        query_tokens = _tokenize(query)
        has_action_verb = any(
            token in ACTION_VERBS and token not in OBSERVATION_VERBS
            for token in query_tokens
        )

        matches: List[SkillMatch] = []
        for skill in self.skills.values():
            score, reasons = self._score_skill(skill, normalized_query, query_tokens, has_action_verb)
            if score <= 0:
                continue
            matches.append(
                SkillMatch(
                    name=skill.name,
                    display_name=skill.display_name,
                    score=score,
                    reasons=reasons,
                    description=skill.description,
                )
            )

        # ── Force-injection via applyTo — bypass le seuil de scoring ──────
        scored_names = {m.name for m in matches}
        for skill in self.skills.values():
            if not skill.apply_to or skill.name in scored_names:
                continue
            if "*" in skill.apply_to:
                matches.append(SkillMatch(
                    name=skill.name, display_name=skill.display_name,
                    score=9.5, reasons=["applyTo:*"], description=skill.description,
                ))
                scored_names.add(skill.name)
            else:
                apply_tokens = {_normalize_text(t) for t in skill.apply_to}
                matched = apply_tokens & set(query_tokens)
                if matched:
                    matches.append(SkillMatch(
                        name=skill.name, display_name=skill.display_name,
                        score=9.0,
                        reasons=[f"applyTo:{','.join(sorted(matched)[:3])}"],
                        description=skill.description,
                    ))
                    scored_names.add(skill.name)

        matches.sort(key=lambda m: (-m.score, m.name))
        return matches[: max(1, int(max_results))]

    def _score_skill(
        self,
        skill: Skill,
        normalized_query: str,
        query_tokens: List[str],
        has_action_verb: bool,
    ) -> Tuple[float, List[str]]:
        score = 0.0
        reasons: List[str] = []

        skill_name_norm = _normalize_text(skill.name)
        skill_display_norm = _normalize_text(skill.display_name)
        skill_desc_norm = _normalize_text(skill.description)

        if skill_name_norm and (
            normalized_query == skill_name_norm
            or f" {skill_name_norm} " in f" {normalized_query} "
            or skill_name_norm in normalized_query
        ):
            score += 8.0
            reasons.append(f"name match:{skill.name}")

        if skill_display_norm and skill_display_norm in normalized_query and skill_display_norm != skill_name_norm:
            score += 4.0
            reasons.append("display name match")

        skill_tokens = set(_tokenize(" ".join([skill.name, skill.display_name, skill.description, " ".join(skill.keywords)])))
        overlap = sorted((set(query_tokens) & skill_tokens) - FRENCH_STOP_WORDS)
        if overlap:
            score += float(len(overlap) * 2)
            reasons.append(f"token overlap:{','.join(overlap[:6])}")

        if has_action_verb and overlap:
            score += 1.5
            reasons.append("action verb bonus")

        query_token_set = set(query_tokens)
        for extension, trigger_tokens in EXTENSION_TRIGGER_MAP.items():
            if query_token_set & trigger_tokens and extension in skill_tokens:
                score += 10.0
                reasons.append(f"extension intent:{extension}")

        if skill_desc_norm and skill_desc_norm[:80] and skill_desc_norm[:40] in normalized_query:
            score += 1.0
            reasons.append("description phrase match")

        return score, reasons

    def build_active_skills_context(
        self,
        query: str,
        max_results: int = 3,
        max_chars: int = 5000,
    ) -> str:
        matches = self.match_skills(query=query, max_results=max_results)
        # Seuil minimum: eviter d'injecter des skills sur des matchs faibles
        # (ex: "parle moi de mon site web" ne doit pas activer website-generator)
        #
        # LOT Z10 (2026-08-16) — le seuil était à 5.0 et laissait passer TOUT le
        # bruit, parce que le matcher a un plancher structurel à **5.5** : un
        # skill sans aucun rapport marque 5.5, donc au-dessus de 5.0.
        # Conséquence mesurée : le worker qui code `donnees.js` (persistance
        # localStorage) recevait `algorithmic-art` + `datagouv`, et celui qui code
        # `chrono.js` recevait `algorithmic-art` — injectés par `delegate_task`
        # sous le titre « Instructions spécifiques à appliquer dans ton code ».
        # Du bruit présenté comme des ordres, sur des tâches de code.
        #
        # 6.0 mesuré sur un corpus de 13 cas réels (workers + chat) : 3 cas
        # changent, les 3 sont du bruit à 5.5 ; AUCUN cas pertinent n'est perdu
        # (css 17.5, html 17.5, design 26, pdf 21.5, xlsx 15.5 — tous conservés).
        matches = [m for m in matches if m.score >= _MIN_SKILL_SCORE]
        if not matches:
            return ""

        budget = max(120, int(max_chars))
        lines = [
            "## Bonnes pratiques à appliquer (guidelines uniquement — pas des outils appelables)",
            "⚠️ Ces sections décrivent des INSTRUCTIONS à suivre, PAS des actions ou fonctions disponibles.",
            "",
        ]
        current_len = sum(len(line) + 1 for line in lines)

        for match in matches:
            skill = self.skills.get(match.name)
            if not skill:
                continue

            reasons = ", ".join(match.reasons[:3]) if match.reasons else "intent match"
            header = f"### {skill.display_name} (`{skill.name}`)\n- Score: {match.score:.1f}\n- Raisons: {reasons}\n"
            instructions = skill.instructions.strip()
            block_prefix = f"{header}\nInstructions:\n"
            remaining = budget - current_len - len(block_prefix) - 2
            if remaining <= 0:
                break

            if len(instructions) > remaining:
                instructions = instructions[: max(0, remaining - 3)].rstrip() + "..."
            block = f"{block_prefix}{instructions}\n"

            if current_len + len(block) > budget:
                break

            lines.append(block)
            current_len += len(block)

        context = "\n".join(lines).strip()
        if len(context) > budget:
            context = context[: budget - 3].rstrip() + "..."
        return context

    def install_skill(self, skill_file: Path, target_dir: Optional[Path] = None) -> Optional[Skill]:
        self.last_install_error = ""
        skill_file = Path(skill_file).resolve()
        if not skill_file.exists():
            self._fail_install(f"Skill file not found: {skill_file}")
            return None
        if skill_file.suffix.lower() != ".skill":
            self._fail_install(f"Invalid skill extension: {skill_file.suffix}")
            return None

        if target_dir is None:
            target_dir = self.base_dirs[-1]
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(skill_file, "r") as zipf:
                is_valid, reason, skill_name = self._validate_skill_archive(zipf)
                if not is_valid:
                    self._fail_install(reason)
                    return None

                skill_dir = target_dir / skill_name
                if skill_dir.exists():
                    shutil.rmtree(skill_dir)
                self._safe_extract_skill_archive(zipf, target_dir)

            skill = self._load_skill_directory(skill_dir)
            if skill:
                self.skills[skill.name] = skill
                logger.info("Skill installed: {}", skill.name)
                return skill
            self._fail_install("Skill extraction terminée mais SKILL.md invalide ou illisible")
            return None
        except Exception as e:
            self._fail_install(f"Skill install error: {e}")
            return None

    def uninstall_skill(self, name: str) -> bool:
        skill = self.skills.get(name)
        if not skill:
            logger.error("Skill not found: {}", name)
            return False
        try:
            if skill.path.is_dir():
                shutil.rmtree(skill.path)
            elif skill.path.exists():
                skill.path.unlink()
            self.skills.pop(name, None)
            return True
        except Exception as e:
            logger.error("Skill uninstall error {}: {}", name, e)
            return False


# Singleton avec lock thread-safe (Phase 2.1)
import threading
_loader: Optional[SkillLoader] = None
_loader_lock = threading.Lock()


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _tokenize(value: str) -> List[str]:
    normalized = _normalize_text(value)
    tokens = re.split(r"[^a-z0-9]+", normalized)
    return [token for token in tokens if token]


def get_skill_loader() -> SkillLoader:
    """Retourne l'instance singleton du SkillLoader (thread-safe)."""
    global _loader
    
    # Double-check locking pattern
    if _loader is None:
        with _loader_lock:
            if _loader is None:
                _loader = SkillLoader()
                _loader.load_all()
    return _loader


def reload_skills() -> Dict[str, Skill]:
    """Recharge tous les skills (thread-safe)."""
    global _loader
    with _loader_lock:
        if _loader is None:
            _loader = SkillLoader()
        return _loader.load_all()


def match_skills(query: str, max_results: int = 3) -> List[SkillMatch]:
    loader = get_skill_loader()
    return loader.match_skills(query=query, max_results=max_results)


def build_active_skills_context(query: str, max_results: int = 3, max_chars: int = 5000) -> str:
    loader = get_skill_loader()
    return loader.build_active_skills_context(query=query, max_results=max_results, max_chars=max_chars)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
